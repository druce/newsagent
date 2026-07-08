"""newsagent:crossdedupe — drop stories already published in the last N days.

Cross-day, story-level de-duplication. Unlike `dedupe` (which removes
syndicated reprints *within* one run by full-body cosine ≥ 0.95), this step
catches the *same event reported by a different outlet on a different day* — the
Yahoo-today / Fortune-yesterday case that shares facts but not prose and so
never approaches the syndication threshold.

Signal: OpenAI `text-embedding-3-large` of `title + short_summary` (always
available post-`summarize`, length-normalized, event-focused). Today's
candidates are compared against the `published_articles` store (articles that
survived into a final newsletter in the last `--lookback-days` days). A
permissive cosine shortlist (`--shortlist-threshold`, default 0.70) picks which
candidate/prior pairs a Haiku judge then confirms as same/different; only
confirmed duplicates are dropped.

Three modes, mirroring `filter`:
  1. classic (default): in-process `same_story` calls via `call_prompt`
     (use `--engine`; CI/cron only).
  2. --prepare-batches: write 25-pair batch files for parallel Haiku subagent
     dispatch by the calling Claude session.
  3. --apply-results DIR: read result JSONs, drop confirmed duplicates.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

import click
import numpy as np
from pydantic import ValidationError

import lib.prompts  # noqa: F401 — register SAME_STORY
from lib.db import PublishedArticle
from lib.embeddings import embed_texts
from lib.llm import call_prompt, get_prompt
from lib.prompts.same_story import SameStoryInput, SameStoryOutput
from lib.state import NewsletterAgentState


_DEFAULT_BATCH = 25
_DEFAULT_LOOKBACK_DAYS = 4
_DEFAULT_SHORTLIST_THRESHOLD = 0.70
_BATCHES_SUBDIR = "crossdedupe-batches"
_RESULTS_SUBDIR = "crossdedupe-results"


# ──────────────────────────────────────────────────────────────────────
# Candidate selection + shortlist
# ──────────────────────────────────────────────────────────────────────

def _candidates(state: NewsletterAgentState) -> list[tuple[int, dict]]:
    """(index, headline) for headlines carrying a non-empty short_summary."""
    return [
        (i, h) for i, h in enumerate(state.headline_data)
        if (h.get("short_summary") or "").strip()
    ]


def _candidate_text(h: dict) -> str:
    return (h.get("title", "") + "\n" + (h.get("short_summary") or "")).strip()


def _shortlist(
    cand_vecs: list[list[float]],
    priors: list[PublishedArticle],
    threshold: float,
) -> dict[int, int]:
    """Map candidate position → best-matching prior position (cosine ≥ threshold).

    Candidates with no prior at/above the threshold are omitted.
    """
    prior_vecs = [p.embedding for p in priors if p.embedding]
    if not cand_vecs or not prior_vecs:
        return {}
    C = np.array(cand_vecs, dtype=float)
    P = np.array(prior_vecs, dtype=float)
    C /= np.where((cn := np.linalg.norm(C, axis=1, keepdims=True)) == 0, 1.0, cn)
    P /= np.where((pn := np.linalg.norm(P, axis=1, keepdims=True)) == 0, 1.0, pn)
    sims = C @ P.T  # (n_cand, n_prior)
    out: dict[int, int] = {}
    for ci in range(sims.shape[0]):
        pj = int(np.argmax(sims[ci]))
        if float(sims[ci, pj]) >= threshold:
            out[ci] = pj
    return out


def _build_pairs(
    state: NewsletterAgentState,
    priors_with_emb: list[PublishedArticle],
    shortlist: dict[int, int],
    cand_index: list[int],
) -> list[dict]:
    """One pair dict per shortlisted candidate. `id` is the headline index."""
    pairs: list[dict] = []
    for ci, pj in shortlist.items():
        h = state.headline_data[cand_index[ci]]
        prior = priors_with_emb[pj]
        pairs.append({
            "id": str(cand_index[ci]),
            "a_title": h.get("title", ""),
            "a_short_summary": h.get("short_summary") or "",
            "b_title": prior.title or "",
            "b_short_summary": prior.short_summary or "",
            "b_url": prior.url,  # metadata for logging (ignored by the prompt)
        })
    return pairs


def _compute_pairs(
    state: NewsletterAgentState,
    db_path: str,
    lookback_days: int,
    threshold: float,
) -> list[dict]:
    """Embed candidates, load the recent-published store, shortlist by cosine."""
    cands = _candidates(state)
    if not cands:
        return []
    since = datetime.now() - timedelta(days=lookback_days)
    with sqlite3.connect(db_path) as conn:
        priors = PublishedArticle.recent(conn, since=since)
    priors_with_emb = [p for p in priors if p.embedding]
    if not priors_with_emb:
        return []

    cand_index = [i for i, _ in cands]
    cand_vecs = embed_texts([_candidate_text(h) for _, h in cands])
    shortlist = _shortlist(cand_vecs, priors_with_emb, threshold)
    return _build_pairs(state, priors_with_emb, shortlist, cand_index)


# ──────────────────────────────────────────────────────────────────────
# Batch prepare / apply
# ──────────────────────────────────────────────────────────────────────

def _render_prompts(pairs: list[dict]) -> tuple[str, str]:
    cfg = get_prompt("same_story")
    validated = SameStoryInput.model_validate({"pairs": pairs})
    user = cfg.user_prompt.format(**validated.model_dump())
    return cfg.system_prompt, user


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
            "output_schema": SameStoryOutput.model_json_schema(),
        }
        path = batches_dir / f"batch-{batch_idx:03d}.json"
        path.write_text(json.dumps(payload, indent=2))
        paths.append(path)
    return paths


def _load_batch_pairs(session_id: str) -> dict[str, dict]:
    """Map candidate id → its pair dict, read from the prepared batch files.

    Used at apply time so we know which ids were shortlisted (and the matched
    prior, for logging) without recomputing embeddings."""
    batches_dir = Path("runs") / session_id / _BATCHES_SUBDIR
    out: dict[str, dict] = {}
    if not batches_dir.exists():
        return out
    for f in sorted(batches_dir.glob("batch-*.json")):
        try:
            batch = json.loads(f.read_text())
        except json.JSONDecodeError:
            continue
        for pair in batch.get("pairs", []):
            out[str(pair["id"])] = pair
    return out


def _load_results(
    results_dir: Path, expected_ids: set[str]
) -> tuple[dict[str, bool], list[str]]:
    """Return (id→same, problems) parsed from result JSONs."""
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
            parsed = SameStoryOutput.model_validate(raw)
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


def _apply_drops(
    state: NewsletterAgentState,
    verdicts: dict[str, bool],
    pairs_by_id: dict[str, dict],
) -> list[dict]:
    """Drop headlines whose index (as str) is confirmed a duplicate.

    Returns the list of dropped headline dicts (for reporting)."""
    drop_idx = {int(i) for i, same in verdicts.items() if same}
    dropped = [state.headline_data[i] for i in sorted(drop_idx)
               if 0 <= i < len(state.headline_data)]
    for i in sorted(drop_idx):
        pair = pairs_by_id.get(str(i), {})
        if 0 <= i < len(state.headline_data):
            click.echo(
                f"  drop dup: {state.headline_data[i].get('title', '')!r} "
                f"↔ {pair.get('b_title', '')!r} ({pair.get('b_url', '')})"
            )
    state.headline_data = [
        h for i, h in enumerate(state.headline_data) if i not in drop_idx
    ]
    return dropped


def _write_report(session_id: str, considered: int, dropped: int) -> None:
    runs_dir = Path("runs") / session_id
    runs_dir.mkdir(parents=True, exist_ok=True)
    (runs_dir / "crossdedupe.json").write_text(json.dumps({
        "session_id": session_id,
        "completed_at": datetime.now().isoformat(),
        "considered": considered,
        "dropped": dropped,
    }, indent=2))


# ──────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────

@click.command()
@click.option("--db", "db_path", default="newsletter_agent.db")
@click.option("--session", "session_id", required=True)
@click.option("--engine", default=None,
              help="Override engine for classic mode (e.g. google:gemini-3.1-flash-lite)")
@click.option("--prepare-batches", is_flag=True,
              help="Write pair batches to runs/<SID>/crossdedupe-batches/ for dispatch")
@click.option("--apply-results", "apply_results", default=None,
              help="Read result JSONs from this dir and drop confirmed duplicates")
@click.option("--batch-size", type=int, default=_DEFAULT_BATCH, show_default=True,
              help="Pairs per batch (prepare-batches mode)")
@click.option("--lookback-days", type=int, default=_DEFAULT_LOOKBACK_DAYS,
              show_default=True, help="Suppress stories published in the last N days")
@click.option("--shortlist-threshold", type=float, default=_DEFAULT_SHORTLIST_THRESHOLD,
              show_default=True, help="Cosine cutoff for shortlisting pairs for the judge")
def cli(
    db_path: str,
    session_id: str,
    engine: str | None,
    prepare_batches: bool,
    apply_results: str | None,
    batch_size: int,
    lookback_days: int,
    shortlist_threshold: float,
) -> None:
    if prepare_batches and apply_results:
        raise click.UsageError("--prepare-batches and --apply-results are mutually exclusive")

    state = NewsletterAgentState(session_id=session_id, db_path=db_path).load_latest_from_db()
    if state is None:
        raise click.ClickException(f"No state found for session {session_id}")

    # ── prepare mode ───────────────────────────────────────────────
    if prepare_batches:
        state.start_step("crossdedupe")
        state.save_checkpoint("crossdedupe")
        pairs = _compute_pairs(state, db_path, lookback_days, shortlist_threshold)
        paths = _write_batches(session_id, pairs, batch_size)
        if not paths:
            click.echo("No cross-day duplicate candidates to check.")
            return
        click.echo(f"Prepared {len(paths)} batch(es) ({len(pairs)} candidate pairs).")
        for p in paths:
            click.echo(f"  {p}")
        click.echo("\nDispatch one Haiku subagent per batch. Write each result to:")
        click.echo(f"  runs/{session_id}/{_RESULTS_SUBDIR}/batch-NNN.json")
        click.echo(f"Then run: python -m lib.steps.crossdedupe --session {session_id} "
                   f"--apply-results runs/{session_id}/{_RESULTS_SUBDIR}")
        return

    # ── apply mode ─────────────────────────────────────────────────
    if apply_results:
        pairs_by_id = _load_batch_pairs(session_id)
        expected_ids = set(pairs_by_id.keys())
        if not expected_ids:
            state.complete_step("crossdedupe", message="no candidates")
            state.save_checkpoint("crossdedupe")
            _write_report(session_id, considered=0, dropped=0)
            click.echo("No candidate batches found; nothing to apply.")
            return
        verdicts, problems = _load_results(Path(apply_results), expected_ids)
        for p in problems:
            click.echo(f"  - {p}", err=True)
        dropped = _apply_drops(state, verdicts, pairs_by_id)
        state.complete_step(
            "crossdedupe",
            message=f"dropped {len(dropped)}/{len(expected_ids)} cross-day dupes",
        )
        state.save_checkpoint("crossdedupe")
        _write_report(session_id, considered=len(expected_ids), dropped=len(dropped))
        click.echo(f"Cross-day dedupe: dropped {len(dropped)}/{len(expected_ids)} "
                   f"candidate(s); {len(state.headline_data)} headlines remain.")
        return

    # ── classic mode ───────────────────────────────────────────────
    state.start_step("crossdedupe")
    state.save_checkpoint("crossdedupe")
    pairs = _compute_pairs(state, db_path, lookback_days, shortlist_threshold)
    if not pairs:
        state.complete_step("crossdedupe", message="no candidates")
        state.save_checkpoint("crossdedupe")
        click.echo("No cross-day duplicate candidates.")
        return

    pairs_by_id = {p["id"]: p for p in pairs}
    verdicts: dict[str, bool] = {}
    for i in range(0, len(pairs), batch_size):
        chunk = pairs[i:i + batch_size]
        result = call_prompt("same_story", {"pairs": chunk}, engine=engine)
        assert isinstance(result, SameStoryOutput)
        for v in result.results:
            verdicts[v.id] = v.same

    dropped = _apply_drops(state, verdicts, pairs_by_id)
    state.complete_step(
        "crossdedupe",
        message=f"dropped {len(dropped)}/{len(pairs)} cross-day dupes",
    )
    state.save_checkpoint("crossdedupe")
    _write_report(session_id, considered=len(pairs), dropped=len(dropped))
    click.echo(f"Cross-day dedupe: dropped {len(dropped)}/{len(pairs)} "
               f"candidate(s); {len(state.headline_data)} headlines remain.")


if __name__ == "__main__":  # pragma: no cover
    sys.exit(cli())
