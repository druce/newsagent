"""newsagent:select — three-phase consolidation + global MMR top-K.

Phases (see ~/.claude/plans/we-need-to-improve-jiggly-hoare.md):
  A. Noise repechage    — LLM mines HDBSCAN noise (cluster_id=-1) for new
                          themes (sharded 50/batch).
  B. Name consolidation — single LLM call unifies HDBSCAN names + repechage
                          proposals into a final section name list (+
                          per-input mapping → final name or "Other").
  C. Reassign all items — every rated headline gets a final cluster_name
                          (or "Other"), sharded 25/batch.
  D. Global MMR top-K   — one MMR call over all reassigned headlines
                          (default K=100, λ=0.7).
  E. Build sections     — group MMR survivors by cluster_name into
                          newsletter_section_data.

Interactive modes (three sequential CLI invocations):
  --prepare-repechage   → write runs/<SID>/select-repechage-batches/
  --prepare-consolidate → read runs/<SID>/select-repechage-results/,
                          write runs/<SID>/select-consolidate-batches/
  --prepare-reassign    → read runs/<SID>/select-consolidate-results/,
                          write runs/<SID>/select-reassign-batches/
  --apply-results DIR   → read runs/<SID>/select-reassign-results/,
                          run global MMR, write sections.

Classic mode (--engine ...) runs all four LLM phases in-process via
lib.llm.call_prompt.
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import click
from pydantic import ValidationError

import lib.prompts  # noqa: F401 — register all prompts
from lib.embeddings import embed_texts
from lib.llm import call_prompt, get_prompt
from lib.mmr import mmr_select
from lib.prompts.assign_noise_batch import (
    ReassignToClustersInput,
    ReassignToClustersOutput,
)
from lib.prompts.consolidate_cluster_names import (
    ConsolidateClusterNamesInput,
    ConsolidateClusterNamesOutput,
)
from lib.prompts.extract_noise_clusters import (
    ExtractNoiseClustersInput,
    ExtractNoiseClustersOutput,
)
from lib.state import NewsletterAgentState


_DEFAULT_TOP_K = 100
_MMR_LAMBDA = 0.7
_DEFAULT_REPECHAGE_BATCH = 50
_DEFAULT_REASSIGN_BATCH = 25
_OTHER_LABEL = "Other"

_REPECHAGE_BATCHES_SUBDIR = "select-repechage-batches"
_REPECHAGE_RESULTS_SUBDIR = "select-repechage-results"
_CONSOLIDATE_BATCHES_SUBDIR = "select-consolidate-batches"
_CONSOLIDATE_RESULTS_SUBDIR = "select-consolidate-results"
_REASSIGN_BATCHES_SUBDIR = "select-reassign-batches"
_REASSIGN_RESULTS_SUBDIR = "select-reassign-results"

_HDBSCAN_PREFIX = "hdbscan-"
_REPECHAGE_PREFIX = "repechage-"


# ──────────────────────────────────────────────────────────────────────
# Shared helpers
# ──────────────────────────────────────────────────────────────────────

def _ensure_runs_dir(session_id: str) -> Path:
    runs_dir = Path("runs") / session_id
    runs_dir.mkdir(parents=True, exist_ok=True)
    return runs_dir


def _wipe_batches_dir(runs_dir: Path, subdir: str) -> Path:
    d = runs_dir / subdir
    if d.exists():
        for p in d.glob("batch-*.json"):
            p.unlink()
    d.mkdir(parents=True, exist_ok=True)
    return d


def _noise_headlines(state: NewsletterAgentState) -> List[tuple[int, dict]]:
    """All headlines with cluster_id == -1, returned as [(state_idx, headline), ...]."""
    return [
        (i, h)
        for i, h in enumerate(state.headline_data)
        if h.get("cluster_id", -1) == -1
    ]


def _hdbscan_cluster_summary(state: NewsletterAgentState) -> Dict[int, dict]:
    """{cluster_id: {"name": ..., "indices": [...], "sample_titles": [...]}}."""
    by_cid: Dict[int, List[int]] = defaultdict(list)
    for i, h in enumerate(state.headline_data):
        cid = h.get("cluster_id", -1)
        if cid >= 0:
            by_cid[cid].append(i)
    out: Dict[int, dict] = {}
    for cid, indices in by_cid.items():
        name = state.headline_data[indices[0]].get("cluster_name", f"Cluster {cid}")
        sample = [state.headline_data[i].get("title", "") for i in indices[:5]]
        out[cid] = {"name": name, "indices": indices, "sample_titles": sample}
    return out


# ──────────────────────────────────────────────────────────────────────
# Phase A: repechage prepare/apply
# ──────────────────────────────────────────────────────────────────────

def _render_repechage_prompts(headlines: list[dict]):
    cfg = get_prompt("extract_noise_clusters")
    validated = ExtractNoiseClustersInput.model_validate({"headlines": headlines})
    return cfg.system_prompt, cfg.user_prompt.format(**validated.model_dump())


def _prepare_repechage_batches(
    session_id: str, state: NewsletterAgentState, batch_size: int,
) -> list[Path]:
    noise = _noise_headlines(state)
    if not noise:
        return []
    runs_dir = _ensure_runs_dir(session_id)
    batches_dir = _wipe_batches_dir(runs_dir, _REPECHAGE_BATCHES_SUBDIR)

    items = [
        {
            "id": str(idx),
            "title": h.get("title", ""),
            "short_summary": h.get("short_summary") or h.get("summary", "")[:200],
        }
        for idx, h in noise
    ]
    paths: list[Path] = []
    for batch_idx, start in enumerate(range(0, len(items), batch_size)):
        chunk = items[start:start + batch_size]
        system, user = _render_repechage_prompts(chunk)
        payload = {
            "batch_id": batch_idx,
            "session_id": session_id,
            "ids": [it["id"] for it in chunk],
            "headlines": chunk,
            "system_prompt": system,
            "user_prompt": user,
            "output_schema": ExtractNoiseClustersOutput.model_json_schema(),
        }
        path = batches_dir / f"batch-{batch_idx:03d}.json"
        path.write_text(json.dumps(payload, indent=2))
        paths.append(path)
    return paths


def _load_repechage_results(results_dir: Path) -> tuple[list[dict], list[str], list[str]]:
    """Return (proposed_clusters, unclustered_ids, problems).

    Each proposed cluster: {"name": str, "member_ids": [str, ...], "batch_id": int}.
    Cross-batch identical names are kept distinct here — phase B consolidates.
    """
    proposed: list[dict] = []
    unclustered: list[str] = []
    problems: list[str] = []
    if not results_dir.exists():
        problems.append(f"repechage results dir not found: {results_dir}")
        return [], [], problems
    files = sorted(results_dir.glob("batch-*.json"))
    if not files:
        problems.append(f"no batch-*.json files in {results_dir}")
        return [], [], problems
    for f in files:
        try:
            raw = json.loads(f.read_text())
            parsed = ExtractNoiseClustersOutput.model_validate(raw)
        except (json.JSONDecodeError, ValidationError) as exc:
            problems.append(f"{f.name}: {exc}")
            continue
        # Derive batch index from filename (batch-NNN.json) for unique tagging.
        batch_idx = int(f.stem.split("-")[-1])
        for c in parsed.proposed_clusters:
            proposed.append({
                "name": c.name,
                "member_ids": list(c.member_ids),
                "batch_id": batch_idx,
            })
        unclustered.extend(parsed.unclustered_ids)
    return proposed, unclustered, problems


# ──────────────────────────────────────────────────────────────────────
# Phase B: consolidate prepare/apply
# ──────────────────────────────────────────────────────────────────────

def _build_consolidate_clusters(
    state: NewsletterAgentState,
    hdbscan_summary: Dict[int, dict],
    repechage_proposals: list[dict],
) -> list[dict]:
    """Combined cluster list (HDBSCAN + repechage) for the consolidate prompt."""
    out: list[dict] = []
    for cid, info in sorted(hdbscan_summary.items()):
        out.append({
            "cluster_id": f"{_HDBSCAN_PREFIX}{cid}",
            "name": info["name"],
            "sample_headlines": info["sample_titles"][:5],
        })
    for i, p in enumerate(repechage_proposals):
        # Pull up to 5 sample titles from the proposed member ids.
        member_titles = []
        for mid in p["member_ids"][:5]:
            try:
                idx = int(mid)
            except ValueError:
                continue
            if 0 <= idx < len(state.headline_data):
                member_titles.append(state.headline_data[idx].get("title", ""))
        out.append({
            "cluster_id": f"{_REPECHAGE_PREFIX}{i}",
            "name": p["name"],
            "sample_headlines": member_titles,
        })
    return out


def _render_consolidate_prompts(clusters: list[dict]):
    cfg = get_prompt("consolidate_cluster_names")
    validated = ConsolidateClusterNamesInput.model_validate({"clusters": clusters})
    return cfg.system_prompt, cfg.user_prompt.format(**validated.model_dump())


def _prepare_consolidate_batch(
    session_id: str, state: NewsletterAgentState,
) -> Optional[Path]:
    runs_dir = _ensure_runs_dir(session_id)
    repechage_results_dir = runs_dir / _REPECHAGE_RESULTS_SUBDIR
    proposed, _unclustered, problems = _load_repechage_results(repechage_results_dir)
    if problems:
        raise click.ClickException(
            "Cannot prepare consolidate batch — repechage results have problems:\n  - "
            + "\n  - ".join(problems)
        )
    hdbscan_summary = _hdbscan_cluster_summary(state)
    clusters = _build_consolidate_clusters(state, hdbscan_summary, proposed)
    if not clusters:
        return None
    batches_dir = _wipe_batches_dir(runs_dir, _CONSOLIDATE_BATCHES_SUBDIR)
    system, user = _render_consolidate_prompts(clusters)
    payload = {
        "batch_id": 0,
        "session_id": session_id,
        "ids": [c["cluster_id"] for c in clusters],
        "clusters": clusters,
        "system_prompt": system,
        "user_prompt": user,
        "output_schema": ConsolidateClusterNamesOutput.model_json_schema(),
    }
    path = batches_dir / "batch-000.json"
    path.write_text(json.dumps(payload, indent=2))
    return path


def _load_consolidate_result(results_dir: Path) -> tuple[list[str], dict[str, str], list[str]]:
    """Return (final_names, mapping cluster_id->final_name, problems)."""
    problems: list[str] = []
    if not results_dir.exists():
        problems.append(f"consolidate results dir not found: {results_dir}")
        return [], {}, problems
    files = sorted(results_dir.glob("batch-*.json"))
    if not files:
        problems.append(f"no batch-*.json files in {results_dir}")
        return [], {}, problems
    final_names: list[str] = []
    mapping: dict[str, str] = {}
    for f in files:
        try:
            raw = json.loads(f.read_text())
            parsed = ConsolidateClusterNamesOutput.model_validate(raw)
        except (json.JSONDecodeError, ValidationError) as exc:
            problems.append(f"{f.name}: {exc}")
            continue
        for n in parsed.final_names:
            if n not in final_names:
                final_names.append(n)
        for m in parsed.mapping:
            mapping[m.cluster_id] = m.final_name
    return final_names, mapping, problems


# ──────────────────────────────────────────────────────────────────────
# Phase C: reassign prepare/apply
# ──────────────────────────────────────────────────────────────────────

def _build_reassign_clusters(
    state: NewsletterAgentState,
    final_names: list[str],
    consolidate_input_clusters: list[dict],
    consolidate_mapping: dict[str, str],
) -> list[dict]:
    """Build the cluster choice list for phase C: one entry per final_name with
    up to 5 sample headlines pulled from any consolidate input cluster that
    mapped to that name."""
    samples_by_name: Dict[str, List[str]] = defaultdict(list)
    by_input_id = {c["cluster_id"]: c for c in consolidate_input_clusters}
    for input_id, input_cluster in by_input_id.items():
        final = consolidate_mapping.get(input_id, _OTHER_LABEL)
        if final == _OTHER_LABEL:
            continue
        for title in input_cluster.get("sample_headlines", []):
            if title and title not in samples_by_name[final]:
                samples_by_name[final].append(title)
            if len(samples_by_name[final]) >= 5:
                break
    return [
        {"name": name, "sample_headlines": samples_by_name.get(name, [])[:5]}
        for name in final_names
    ]


def _render_reassign_prompts(headlines: list[dict], clusters: list[dict]):
    cfg = get_prompt("reassign_to_clusters")
    validated = ReassignToClustersInput.model_validate({
        "headlines": headlines,
        "clusters": clusters,
    })
    return cfg.system_prompt, cfg.user_prompt.format(**validated.model_dump())


def _prepare_reassign_batches(
    session_id: str, state: NewsletterAgentState, batch_size: int,
) -> list[Path]:
    runs_dir = _ensure_runs_dir(session_id)
    consolidate_results_dir = runs_dir / _CONSOLIDATE_RESULTS_SUBDIR
    final_names, mapping, problems = _load_consolidate_result(consolidate_results_dir)
    if problems:
        raise click.ClickException(
            "Cannot prepare reassign batches — consolidate results have problems:\n  - "
            + "\n  - ".join(problems)
        )
    if not final_names:
        raise click.ClickException("consolidate result yielded no final cluster names")

    # Reload the consolidate input clusters so we can map final names → sample headlines.
    consolidate_batch_path = runs_dir / _CONSOLIDATE_BATCHES_SUBDIR / "batch-000.json"
    if not consolidate_batch_path.exists():
        raise click.ClickException(
            f"consolidate input batch missing: {consolidate_batch_path}"
        )
    consolidate_input = json.loads(consolidate_batch_path.read_text())
    cluster_choices = _build_reassign_clusters(
        state, final_names, consolidate_input.get("clusters", []), mapping,
    )

    batches_dir = _wipe_batches_dir(runs_dir, _REASSIGN_BATCHES_SUBDIR)
    items = [
        {
            "id": str(i),
            "title": h.get("title", ""),
            "short_summary": h.get("short_summary") or h.get("summary", "")[:200],
        }
        for i, h in enumerate(state.headline_data)
    ]
    paths: list[Path] = []
    for batch_idx, start in enumerate(range(0, len(items), batch_size)):
        chunk = items[start:start + batch_size]
        system, user = _render_reassign_prompts(chunk, cluster_choices)
        payload = {
            "batch_id": batch_idx,
            "session_id": session_id,
            "ids": [it["id"] for it in chunk],
            "headlines": chunk,
            "clusters": cluster_choices,
            "system_prompt": system,
            "user_prompt": user,
            "output_schema": ReassignToClustersOutput.model_json_schema(),
        }
        path = batches_dir / f"batch-{batch_idx:03d}.json"
        path.write_text(json.dumps(payload, indent=2))
        paths.append(path)
    return paths


def _load_reassign_results(results_dir: Path) -> tuple[dict[str, str], list[str]]:
    """Return (id->assignment, problems)."""
    out: dict[str, str] = {}
    problems: list[str] = []
    if not results_dir.exists():
        problems.append(f"reassign results dir not found: {results_dir}")
        return {}, problems
    files = sorted(results_dir.glob("batch-*.json"))
    if not files:
        problems.append(f"no batch-*.json files in {results_dir}")
        return {}, problems
    for f in files:
        try:
            raw = json.loads(f.read_text())
            parsed = ReassignToClustersOutput.model_validate(raw)
        except (json.JSONDecodeError, ValidationError) as exc:
            problems.append(f"{f.name}: {exc}")
            continue
        for a in parsed.assignments:
            out[a.id] = a.assignment
    return out, problems


# ──────────────────────────────────────────────────────────────────────
# Phase D + E: global MMR + finalize
# ──────────────────────────────────────────────────────────────────────

def _ensure_embeddings(state: NewsletterAgentState, indices: list[int]) -> list[list[float]]:
    """Return embeddings for the given headline indices, computing any missing."""
    missing_idx: list[int] = []
    for idx in indices:
        if not state.headline_data[idx].get("embedding"):
            missing_idx.append(idx)
    if missing_idx:
        texts = [
            (state.headline_data[i].get("title", "") + "\n"
             + (state.headline_data[i].get("short_summary")
                or state.headline_data[i].get("summary", "")[:200]))
            for i in missing_idx
        ]
        embs = embed_texts(texts)
        for i, e in zip(missing_idx, embs):
            state.headline_data[i]["embedding"] = e
    return [state.headline_data[i]["embedding"] for i in indices]


def _apply_reassignments_and_mmr(
    state: NewsletterAgentState,
    assignments: dict[str, str],
    top_k: int,
    mmr_lambda: float,
) -> tuple[int, dict[str, int]]:
    """Apply per-headline cluster_name and run global MMR top-K.

    Returns (count_selected, per_section_counts).
    """
    # Apply the assignment to every headline; default to "Other" if unmapped.
    for idx_str, label in assignments.items():
        try:
            idx = int(idx_str)
        except ValueError:
            continue
        if 0 <= idx < len(state.headline_data):
            state.headline_data[idx]["cluster_name"] = label
    for h in state.headline_data:
        if not h.get("cluster_name"):
            h["cluster_name"] = _OTHER_LABEL

    indices = list(range(len(state.headline_data)))
    if not indices:
        state.newsletter_section_data = []
        state.clusters = {}
        return 0, {}

    embs = _ensure_embeddings(state, indices)
    relevance = [float(state.headline_data[i].get("rating", 0.0)) for i in indices]
    selected_local = mmr_select(embs, relevance, k=top_k, lambda_=mmr_lambda)
    selected = [indices[j] for j in selected_local]

    sections: list[dict] = []
    per_section: dict[str, int] = defaultdict(int)
    for idx in selected:
        h = state.headline_data[idx]
        label = h.get("cluster_name") or _OTHER_LABEL
        sections.append({
            "cat": label,
            "headline": h.get("title", ""),
            "link": h.get("final_url") or h.get("url", ""),
            "site_name": h.get("site_name"),
            "rating": h.get("rating", 0.0),
            "summary": h.get("summary", ""),
            "id": idx,
        })
        per_section[label] += 1
    state.newsletter_section_data = sections

    # Rebuild state.clusters from the selected items.
    by_label: Dict[str, List[str]] = defaultdict(list)
    for idx in selected:
        label = state.headline_data[idx].get("cluster_name") or _OTHER_LABEL
        by_label[label].append(str(idx))
    state.clusters = dict(by_label)
    return len(selected), dict(per_section)


# ──────────────────────────────────────────────────────────────────────
# Classic mode (in-process)
# ──────────────────────────────────────────────────────────────────────

def _classic_run(
    state: NewsletterAgentState,
    engine: Optional[str],
    repechage_batch_size: int,
    reassign_batch_size: int,
) -> tuple[list[dict], list[str], dict[str, str], dict[str, str]]:
    """Run phases A→C in-process.

    Returns (consolidate_input_clusters, final_names, mapping, assignments).
    Caller then runs phase D + E to populate state.
    """
    # Phase A
    noise = _noise_headlines(state)
    proposals: list[dict] = []
    for batch_idx, start in enumerate(range(0, len(noise), repechage_batch_size)):
        chunk_pairs = noise[start:start + repechage_batch_size]
        if not chunk_pairs:
            continue
        items = [
            {
                "id": str(idx),
                "title": h.get("title", ""),
                "short_summary": h.get("short_summary") or h.get("summary", "")[:200],
            }
            for idx, h in chunk_pairs
        ]
        try:
            result = call_prompt(
                "extract_noise_clusters", {"headlines": items}, engine=engine,
            )
        except Exception as exc:  # noqa: BLE001
            click.echo(f"  repechage batch {batch_idx}: {exc}", err=True)
            continue
        for c in result.proposed_clusters:
            proposals.append({
                "name": c.name,
                "member_ids": list(c.member_ids),
                "batch_id": batch_idx,
            })

    # Phase B
    hdbscan_summary = _hdbscan_cluster_summary(state)
    consolidate_input = _build_consolidate_clusters(state, hdbscan_summary, proposals)
    final_names: list[str] = []
    mapping: dict[str, str] = {}
    if consolidate_input:
        try:
            cresult = call_prompt(
                "consolidate_cluster_names", {"clusters": consolidate_input}, engine=engine,
            )
            for n in cresult.final_names:
                if n not in final_names:
                    final_names.append(n)
            for m in cresult.mapping:
                mapping[m.cluster_id] = m.final_name
        except Exception as exc:  # noqa: BLE001
            click.echo(f"  consolidate: {exc}", err=True)

    # Phase C
    cluster_choices = _build_reassign_clusters(state, final_names, consolidate_input, mapping)
    all_items = [
        {
            "id": str(i),
            "title": h.get("title", ""),
            "short_summary": h.get("short_summary") or h.get("summary", "")[:200],
        }
        for i, h in enumerate(state.headline_data)
    ]
    assignments: dict[str, str] = {}
    for batch_idx, start in enumerate(range(0, len(all_items), reassign_batch_size)):
        chunk = all_items[start:start + reassign_batch_size]
        if not chunk:
            continue
        try:
            rresult = call_prompt(
                "reassign_to_clusters",
                {"headlines": chunk, "clusters": cluster_choices},
                engine=engine,
            )
            for a in rresult.assignments:
                assignments[a.id] = a.assignment
        except Exception as exc:  # noqa: BLE001
            click.echo(f"  reassign batch {batch_idx}: {exc}", err=True)
            continue
    return consolidate_input, final_names, mapping, assignments


# ──────────────────────────────────────────────────────────────────────
# Report writer
# ──────────────────────────────────────────────────────────────────────

def _write_select_report(
    session_id: str, n_selected: int, per_section: dict[str, int],
    n_final_names: int, top_k: int, mmr_lambda: float,
) -> None:
    runs_dir = _ensure_runs_dir(session_id)
    (runs_dir / "select.json").write_text(json.dumps({
        "session_id": session_id,
        "completed_at": datetime.now().isoformat(),
        "n_selected": n_selected,
        "n_final_names": n_final_names,
        "per_section_counts": per_section,
        "top_k": top_k,
        "mmr_lambda": mmr_lambda,
    }, indent=2))


# ──────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────

@click.command()
@click.option("--db", "db_path", default="newsletter_agent.db")
@click.option("--session", "session_id", required=True)
@click.option("--k", "top_k", default=_DEFAULT_TOP_K, type=int,
              help="Global MMR top-K (number of items in the final newsletter)")
@click.option("--lambda", "mmr_lambda", default=_MMR_LAMBDA, type=float)
@click.option("--engine", default=None, help="Override LLM engine for classic mode")
@click.option("--prepare-repechage", is_flag=True,
              help="Phase A: write noise-mining batches")
@click.option("--prepare-consolidate", is_flag=True,
              help="Phase B: write single consolidate batch from repechage results")
@click.option("--prepare-reassign", is_flag=True,
              help="Phase C: write reassign batches from consolidate result")
@click.option("--apply-results", "apply_results_dir", default=None,
              help="Phase D+E: read reassign results, run global MMR, write sections")
@click.option("--repechage-batch-size", type=int, default=_DEFAULT_REPECHAGE_BATCH,
              show_default=True)
@click.option("--reassign-batch-size", type=int, default=_DEFAULT_REASSIGN_BATCH,
              show_default=True)
def cli(
    db_path: str,
    session_id: str,
    top_k: int,
    mmr_lambda: float,
    engine: Optional[str],
    prepare_repechage: bool,
    prepare_consolidate: bool,
    prepare_reassign: bool,
    apply_results_dir: Optional[str],
    repechage_batch_size: int,
    reassign_batch_size: int,
) -> None:
    prepare_flags = sum([prepare_repechage, prepare_consolidate, prepare_reassign])
    if prepare_flags > 1:
        raise click.UsageError(
            "--prepare-repechage, --prepare-consolidate, --prepare-reassign "
            "are mutually exclusive (run them sequentially)."
        )
    if prepare_flags and apply_results_dir:
        raise click.UsageError(
            "prepare flags and --apply-results are mutually exclusive."
        )

    state = NewsletterAgentState(session_id=session_id, db_path=db_path).load_latest_from_db()
    if state is None:
        raise click.ClickException(f"No state found for session {session_id}")

    # ── --prepare-repechage ───────────────────────────────────────────
    if prepare_repechage:
        state.start_step("select")
        state.save_checkpoint("select")
        paths = _prepare_repechage_batches(session_id, state, repechage_batch_size)
        click.echo(f"Phase A: prepared {len(paths)} repechage batch(es).")
        for p in paths:
            click.echo(f"  {p}")
        click.echo(
            f"\nDispatch one Sonnet subagent per batch. Each must write its result to:"
            f"\n  runs/{session_id}/{_REPECHAGE_RESULTS_SUBDIR}/batch-NNN.json"
            f"\nThen run: python -m lib.steps.select --session {session_id} --prepare-consolidate"
        )
        return

    # ── --prepare-consolidate ─────────────────────────────────────────
    if prepare_consolidate:
        path = _prepare_consolidate_batch(session_id, state)
        if path is None:
            click.echo("Phase B: no clusters to consolidate (no HDBSCAN clusters + no repechage proposals).")
            return
        click.echo(f"Phase B: prepared consolidate batch: {path}")
        click.echo(
            f"\nDispatch ONE Sonnet subagent. Write the result to:"
            f"\n  runs/{session_id}/{_CONSOLIDATE_RESULTS_SUBDIR}/batch-000.json"
            f"\nThen run: python -m lib.steps.select --session {session_id} --prepare-reassign"
        )
        return

    # ── --prepare-reassign ────────────────────────────────────────────
    if prepare_reassign:
        paths = _prepare_reassign_batches(session_id, state, reassign_batch_size)
        click.echo(f"Phase C: prepared {len(paths)} reassign batch(es).")
        for p in paths:
            click.echo(f"  {p}")
        click.echo(
            f"\nDispatch one Haiku subagent per batch. Each must write its result to:"
            f"\n  runs/{session_id}/{_REASSIGN_RESULTS_SUBDIR}/batch-NNN.json"
            f"\nThen run: python -m lib.steps.select --session {session_id} "
            f"--apply-results runs/{session_id}"
        )
        return

    # ── --apply-results ───────────────────────────────────────────────
    if apply_results_dir:
        root = Path(apply_results_dir)
        assignments, problems = _load_reassign_results(root / _REASSIGN_RESULTS_SUBDIR)
        if problems:
            click.echo("Apply found problems:", err=True)
            for p in problems:
                click.echo(f"  - {p}", err=True)
        if not assignments:
            raise click.ClickException(
                "no reassign results found — cannot apply (see problems above)."
            )

        # Determine final_names from the consolidate result for the report.
        _final_names, _mapping, _cprobs = _load_consolidate_result(
            Path("runs") / session_id / _CONSOLIDATE_RESULTS_SUBDIR
        )
        n_selected, per_section = _apply_reassignments_and_mmr(
            state, assignments, top_k, mmr_lambda,
        )

        state.complete_step(
            "select",
            message=f"{n_selected} headlines across {len(per_section)} sections",
        )
        state.save_checkpoint("select")
        _write_select_report(
            session_id, n_selected, per_section, len(_final_names), top_k, mmr_lambda,
        )
        click.echo(
            f"Select: {n_selected} headlines across {len(per_section)} sections "
            f"(final_names={len(_final_names)})."
        )
        return

    # ── classic mode ──────────────────────────────────────────────────
    state.start_step("select")
    state.save_checkpoint("select")
    _consol_input, final_names, _mapping, assignments = _classic_run(
        state, engine, repechage_batch_size, reassign_batch_size,
    )
    n_selected, per_section = _apply_reassignments_and_mmr(
        state, assignments, top_k, mmr_lambda,
    )

    state.complete_step(
        "select",
        message=f"{n_selected} headlines across {len(per_section)} sections",
    )
    state.save_checkpoint("select")
    _write_select_report(
        session_id, n_selected, per_section, len(final_names), top_k, mmr_lambda,
    )
    click.echo(
        f"Select: {n_selected} headlines across {len(per_section)} sections "
        f"(final_names={len(final_names)})."
    )


if __name__ == "__main__":  # pragma: no cover
    sys.exit(cli())
