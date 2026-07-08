"""newsagent:coverage — count same-day same-event coverage and boost ratings.

Between `crossdedupe` and `rate`. Embeds `title + short_summary` for every
summarized headline, builds the full pairwise cosine matrix, shortlists pairs
above a cosine threshold, and asks a Haiku `same_story_sameday` judge (shown the
FULL bullet `summary`) whether each shortlisted pair reports the same event.
Confirmed-same pairs are grouped by union-find; every headline is stamped
`coverage_count = size of its component`. Nothing is dropped or merged — `rate`
turns the count into a `log2(count)` importance boost and `select`'s MMR keeps
one representative.

Three modes, mirroring `crossdedupe`:
  1. classic (default): in-process `same_story_sameday` calls via `call_prompt`.
  2. --prepare-batches: write 25-pair batches for Haiku subagent dispatch.
  3. --apply-results DIR: read verdicts, group, stamp coverage_count.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import click
import numpy as np
from pydantic import ValidationError

import lib.prompts  # noqa: F401 — register same_story_sameday
from lib.embeddings import embed_texts
from lib.llm import call_prompt, get_prompt
from lib.prompts.same_story_sameday import (
    SameStorySamedayInput,
    SameStorySamedayOutput,
)
from lib.state import NewsletterAgentState


_DEFAULT_BATCH = 25
_DEFAULT_SHORTLIST_THRESHOLD = 0.70
_BATCHES_SUBDIR = "coverage-batches"
_RESULTS_SUBDIR = "coverage-results"


# ── candidate selection + shortlist ──────────────────────────────────

def _candidates(state: NewsletterAgentState) -> list[tuple[int, dict]]:
    """(index, headline) for headlines carrying a non-empty short_summary."""
    return [
        (i, h) for i, h in enumerate(state.headline_data)
        if (h.get("short_summary") or "").strip()
    ]


def _candidate_text(h: dict) -> str:
    return (h.get("title", "") + "\n" + (h.get("short_summary") or "")).strip()


def _shortlist_pairs(
    cand_vecs: list[list[float]], threshold: float
) -> list[tuple[int, int]]:
    """Candidate-local index pairs (i<j) with cosine ≥ threshold."""
    if len(cand_vecs) < 2:
        return []
    M = np.array(cand_vecs, dtype=float)
    norms = np.linalg.norm(M, axis=1, keepdims=True)
    M = M / np.where(norms == 0, 1.0, norms)
    sims = M @ M.T
    pairs: list[tuple[int, int]] = []
    n = sims.shape[0]
    for i in range(n):
        for j in range(i + 1, n):
            if float(sims[i, j]) >= threshold:
                pairs.append((i, j))
    return pairs


def _build_pairs(
    state: NewsletterAgentState,
    cand_index: list[int],
    shortlist: list[tuple[int, int]],
) -> list[dict]:
    """One pair dict per shortlisted pair; `id` encodes both global indices."""
    pairs: list[dict] = []
    for i, j in shortlist:
        gi, gj = cand_index[i], cand_index[j]
        a = state.headline_data[gi]
        b = state.headline_data[gj]
        pairs.append({
            "id": f"{gi}-{gj}",
            "a_title": a.get("title", ""),
            "a_summary": a.get("summary", ""),
            "b_title": b.get("title", ""),
            "b_summary": b.get("summary", ""),
        })
    return pairs


def _group_counts(
    cand_index: list[int], same_pair_ids: set[str]
) -> dict[int, int]:
    """Union-find over global indices; return global_index -> component size."""
    parent = {gi: gi for gi in cand_index}

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for pid in same_pair_ids:
        try:
            a_str, b_str = pid.split("-", 1)
            a, b = int(a_str), int(b_str)
        except (ValueError, KeyError):
            continue
        if a in parent and b in parent:
            union(a, b)

    sizes: dict[int, int] = {}
    for gi in cand_index:
        root = find(gi)
        sizes[root] = sizes.get(root, 0) + 1
    return {gi: sizes[find(gi)] for gi in cand_index}


# ── batch prepare / apply ────────────────────────────────────────────

def _render_prompts(pairs: list[dict]) -> tuple[str, str]:
    cfg = get_prompt("same_story_sameday")
    validated = SameStorySamedayInput.model_validate({"pairs": pairs})
    user = cfg.user_prompt.format(**validated.model_dump())
    return cfg.system_prompt, user


def _compute_pairs(
    state: NewsletterAgentState, threshold: float
) -> tuple[list[dict], list[int]]:
    """Embed candidates, shortlist by cosine, build judge pairs.

    Returns (pairs, cand_index) — cand_index is every candidate's global index,
    needed at grouping time so singletons still get coverage_count = 1.
    """
    cands = _candidates(state)
    cand_index = [i for i, _ in cands]
    if len(cands) < 2:
        return [], cand_index
    cand_vecs = embed_texts([_candidate_text(h) for _, h in cands])
    shortlist = _shortlist_pairs(cand_vecs, threshold)
    return _build_pairs(state, cand_index, shortlist), cand_index


def _write_batches(
    session_id: str, pairs: list[dict], batch_size: int
) -> list[Path]:
    batches_dir = Path("runs") / session_id / _BATCHES_SUBDIR
    if batches_dir.exists():
        for p in batches_dir.glob("batch-*.json"):
            p.unlink()
    if not pairs:
        return []
    batches_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for batch_idx, start in enumerate(range(0, len(pairs), batch_size)):
        chunk = pairs[start:start + batch_size]
        system, user = _render_prompts(chunk)
        payload = {
            "batch_id": batch_idx,
            "session_id": session_id,
            "ids": [p["id"] for p in chunk],
            "pairs": chunk,
            "system_prompt": system,
            "user_prompt": user,
            "output_schema": SameStorySamedayOutput.model_json_schema(),
        }
        path = batches_dir / f"batch-{batch_idx:03d}.json"
        path.write_text(json.dumps(payload, indent=2))
        paths.append(path)
    return paths


def _load_batch_pair_ids(session_id: str) -> set[str]:
    """All pair ids that were prepared (so apply knows what was judged)."""
    batches_dir = Path("runs") / session_id / _BATCHES_SUBDIR
    out: set[str] = set()
    if not batches_dir.exists():
        return out
    for f in sorted(batches_dir.glob("batch-*.json")):
        try:
            batch = json.loads(f.read_text())
        except json.JSONDecodeError:
            continue
        for pair in batch.get("pairs", []):
            out.add(str(pair["id"]))
    return out


def _load_results(
    results_dir: Path, expected_ids: set[str]
) -> tuple[dict[str, bool], list[str]]:
    verdicts: dict[str, bool] = {}
    problems: list[str] = []
    if not results_dir.exists():
        return {}, [f"results dir not found: {results_dir}"]
    files = sorted(results_dir.glob("batch-*.json"))
    if not files:
        return {}, [f"no batch-*.json files in {results_dir}"]
    seen: set[str] = set()
    for f in files:
        try:
            raw = json.loads(f.read_text())
        except json.JSONDecodeError as exc:
            problems.append(f"{f.name}: invalid JSON ({exc})")
            continue
        try:
            parsed = SameStorySamedayOutput.model_validate(raw)
        except ValidationError as exc:
            problems.append(f"{f.name}: schema mismatch ({exc.error_count()} errors)")
            continue
        for v in parsed.results:
            if v.id in seen:
                problems.append(f"{f.name}: duplicate id {v.id!r}")
            seen.add(v.id)
            verdicts[v.id] = v.same
    missing = expected_ids - seen
    if missing:
        problems.append(f"missing verdicts for ids: {sorted(missing)[:10]}...")
    return verdicts, problems


def _stamp_counts(
    state: NewsletterAgentState, cand_index: list[int], same_pair_ids: set[str]
) -> dict[int, int]:
    """Stamp coverage_count on every headline (candidates by component, else 1)."""
    counts = _group_counts(cand_index, same_pair_ids)
    for i, h in enumerate(state.headline_data):
        h["coverage_count"] = counts.get(i, 1)
    return counts


def _write_report(
    session_id: str, n_candidates: int, n_pairs: int,
    n_confirmed: int, counts: dict[int, int]
) -> None:
    runs_dir = Path("runs") / session_id
    runs_dir.mkdir(parents=True, exist_ok=True)
    groups = sorted({c for c in counts.values() if c > 1}, reverse=True)
    (runs_dir / "coverage.json").write_text(json.dumps({
        "session_id": session_id,
        "completed_at": datetime.now().isoformat(),
        "n_candidates": n_candidates,
        "n_shortlisted_pairs": n_pairs,
        "n_confirmed_pairs": n_confirmed,
        "n_boosted": sum(1 for c in counts.values() if c > 1),
        "max_coverage": max(counts.values()) if counts else 1,
        "multi_group_sizes": groups,
    }, indent=2))


# ── CLI ──────────────────────────────────────────────────────────────

@click.command()
@click.option("--db", "db_path", default="newsletter_agent.db")
@click.option("--session", "session_id", required=True)
@click.option("--engine", default=None,
              help="Override engine for classic mode (e.g. google:gemini-3.1-flash-lite)")
@click.option("--prepare-batches", is_flag=True,
              help="Write pair batches to runs/<SID>/coverage-batches/ for dispatch")
@click.option("--apply-results", "apply_results", default=None,
              help="Read result JSONs from this dir, group, stamp coverage_count")
@click.option("--batch-size", type=int, default=_DEFAULT_BATCH, show_default=True,
              help="Pairs per batch (prepare-batches mode)")
@click.option("--shortlist-threshold", type=float,
              default=_DEFAULT_SHORTLIST_THRESHOLD, show_default=True,
              help="Cosine cutoff for shortlisting pairs for the judge")
def cli(
    db_path: str,
    session_id: str,
    engine: str | None,
    prepare_batches: bool,
    apply_results: str | None,
    batch_size: int,
    shortlist_threshold: float,
) -> None:
    if prepare_batches and apply_results:
        raise click.UsageError("--prepare-batches and --apply-results are mutually exclusive")

    state = NewsletterAgentState(session_id=session_id, db_path=db_path).load_latest_from_db()
    if state is None:
        raise click.ClickException(f"No state found for session {session_id}")

    # ── prepare mode ───────────────────────────────────────────────
    if prepare_batches:
        state.start_step("coverage")
        state.save_checkpoint("coverage")
        pairs, _ = _compute_pairs(state, shortlist_threshold)
        paths = _write_batches(session_id, pairs, batch_size)
        if not paths:
            click.echo("No same-day coverage pairs to check.")
            return
        click.echo(f"Prepared {len(paths)} batch(es) ({len(pairs)} candidate pairs).")
        for p in paths:
            click.echo(f"  {p}")
        click.echo("\nDispatch one Haiku subagent per batch. Write each result to:")
        click.echo(f"  runs/{session_id}/{_RESULTS_SUBDIR}/batch-NNN.json")
        click.echo(f"Then run: python -m lib.steps.coverage --session {session_id} "
                   f"--apply-results runs/{session_id}/{_RESULTS_SUBDIR}")
        return

    # ── apply mode ─────────────────────────────────────────────────
    if apply_results:
        cand_index = [i for i, _ in _candidates(state)]
        expected_ids = _load_batch_pair_ids(session_id)
        if not expected_ids:
            counts = _stamp_counts(state, cand_index, same_pair_ids=set())
            state.complete_step("coverage", message="no coverage pairs")
            state.save_checkpoint("coverage")
            _write_report(session_id, len(cand_index), 0, 0, counts)
            click.echo("No coverage pairs; all stories singleton (coverage_count=1).")
            return
        verdicts, problems = _load_results(Path(apply_results), expected_ids)
        for p in problems:
            click.echo(f"  - {p}", err=True)
        same_ids = {i for i, same in verdicts.items() if same}
        counts = _stamp_counts(state, cand_index, same_pair_ids=same_ids)
        n_boosted = sum(1 for c in counts.values() if c > 1)
        state.complete_step(
            "coverage",
            message=f"{n_boosted} stories boosted (max coverage "
                    f"{max(counts.values()) if counts else 1})",
        )
        state.save_checkpoint("coverage")
        _write_report(session_id, len(cand_index), len(expected_ids), len(same_ids), counts)
        click.echo(f"Coverage: {len(same_ids)}/{len(expected_ids)} pairs confirmed; "
                   f"{n_boosted} stories boosted.")
        return

    # ── classic mode ───────────────────────────────────────────────
    state.start_step("coverage")
    state.save_checkpoint("coverage")
    pairs, cand_index = _compute_pairs(state, shortlist_threshold)
    if not pairs:
        counts = _stamp_counts(state, cand_index, same_pair_ids=set())
        state.complete_step("coverage", message="no coverage pairs")
        state.save_checkpoint("coverage")
        _write_report(session_id, len(cand_index), 0, 0, counts)
        click.echo("No same-day coverage pairs; all singleton.")
        return

    verdicts: dict[str, bool] = {}
    for i in range(0, len(pairs), batch_size):
        chunk = pairs[i:i + batch_size]
        result = call_prompt("same_story_sameday", {"pairs": chunk}, engine=engine)
        assert isinstance(result, SameStorySamedayOutput)
        for v in result.results:
            verdicts[v.id] = v.same

    same_ids = {i for i, same in verdicts.items() if same}
    counts = _stamp_counts(state, cand_index, same_pair_ids=same_ids)
    n_boosted = sum(1 for c in counts.values() if c > 1)
    state.complete_step(
        "coverage",
        message=f"{n_boosted} stories boosted (max coverage "
                f"{max(counts.values()) if counts else 1})",
    )
    state.save_checkpoint("coverage")
    _write_report(session_id, len(cand_index), len(pairs), len(same_ids), counts)
    click.echo(f"Coverage: {len(same_ids)}/{len(pairs)} pairs confirmed; "
               f"{n_boosted} stories boosted.")


if __name__ == "__main__":  # pragma: no cover
    sys.exit(cli())
