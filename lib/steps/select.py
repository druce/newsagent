"""newsagent:select — LLM noise assignment + cluster merge + MMR diversity selection.

Three modes (mirror of lib/steps/filter.py):
  1. classic (default): in-process call_prompt loop for noise-assign and merge.
  2. --prepare-batches: write
       runs/<SID>/select-assign-batches/batch-NNN.json  (noise headlines, sharded)
       runs/<SID>/select-merge-batches/batch-000.json   (all merge-candidate pairs)
  3. --apply-results DIR: read result JSONs from
       <DIR>/../select-assign-results/ and <DIR>/../select-merge-results/,
     apply assignments + merges, then run MMR in-process.

For --apply-results the user passes the SESSION runs dir (e.g. runs/<SID>);
the step looks for both `select-assign-results/` and `select-merge-results/`
inside it.
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import click
import numpy as np
from pydantic import ValidationError

import lib.prompts  # noqa: F401 — register all prompts
from lib.embeddings import embed_texts
from lib.llm import call_prompt, get_prompt
from lib.mmr import mmr_select
from lib.prompts.assign_noise_batch import (
    AssignNoiseBatchInput,
    AssignNoiseBatchOutput,
)
from lib.prompts.assign_noise import ClusterDescriptor
from lib.prompts.merge_clusters_batch import (
    MergeClustersBatchInput,
    MergeClustersBatchOutput,
    PairToDecide,
)
from lib.prompts.merge_clusters import ClusterPair
from lib.state import NewsletterAgentState


_DEFAULT_K_PER_CLUSTER = 5
_MMR_LAMBDA = 0.7
_MERGE_SIM_THRESHOLD = 0.85
_DEFAULT_ASSIGN_BATCH = 25
_ASSIGN_BATCHES_SUBDIR = "select-assign-batches"
_ASSIGN_RESULTS_SUBDIR = "select-assign-results"
_MERGE_BATCHES_SUBDIR = "select-merge-batches"
_MERGE_RESULTS_SUBDIR = "select-merge-results"


# ──────────────────────────────────────────────────────────────────────
# Helpers shared by all three modes
# ──────────────────────────────────────────────────────────────────────

def _build_cluster_snapshot(state: NewsletterAgentState):
    by_cluster: Dict[int, List[int]] = defaultdict(list)
    for i, h in enumerate(state.headline_data):
        cid = h.get("cluster_id", -1)
        by_cluster[cid].append(i)
    cluster_names: Dict[int, str] = {}
    for cid, indices in by_cluster.items():
        if cid < 0:
            continue
        name = state.headline_data[indices[0]].get("cluster_name", f"Cluster {cid}")
        cluster_names[cid] = name
    return by_cluster, cluster_names


def _cluster_descriptors(state, by_cluster, cluster_names) -> list[dict]:
    out = []
    for cid in sorted(cluster_names.keys()):
        sample_titles = [state.headline_data[i]["title"] for i in by_cluster[cid][:3]]
        out.append({
            "id": str(cid),
            "name": cluster_names[cid],
            "sample_headlines": sample_titles,
        })
    return out


def _find_merge_candidates(state, by_cluster, cluster_names) -> list[tuple]:
    """Cosine-similar pairs of cluster names above _MERGE_SIM_THRESHOLD."""
    cids = sorted([cid for cid in by_cluster.keys() if cid >= 0])
    if len(cids) < 2:
        return []
    names_list = [cluster_names.get(cid, "") for cid in cids]
    name_embs = embed_texts(names_list)
    M = np.asarray(name_embs, dtype=np.float64)
    norms = np.linalg.norm(M, axis=1, keepdims=True)
    norms = np.where(norms == 0, 1.0, norms)
    N = M / norms
    sim = N @ N.T
    pairs: list[tuple] = []
    for i in range(len(cids)):
        for j in range(i + 1, len(cids)):
            if sim[i, j] >= _MERGE_SIM_THRESHOLD:
                pairs.append((cids[i], cids[j]))
    return pairs


def _run_mmr_and_finalize(
    state: NewsletterAgentState,
    by_cluster: Dict[int, List[int]],
    cluster_names: Dict[int, str],
    k_per_cluster: int,
    mmr_lambda: float,
):
    sections: List[dict] = []
    for cid, indices in sorted(by_cluster.items()):
        if cid < 0 or not indices:
            continue
        embs = [state.headline_data[i].get("embedding") for i in indices]
        has_all_embs = all(e is not None and len(e) > 0 for e in embs)
        if has_all_embs:
            relevance = [state.headline_data[i].get("rating", 0.0) for i in indices]
            chosen_local = mmr_select(embs, relevance, k=k_per_cluster, lambda_=mmr_lambda)
            picks = [indices[j] for j in chosen_local]
        else:
            sorted_by_rating = sorted(
                indices,
                key=lambda i: state.headline_data[i].get("rating", 0.0),
                reverse=True,
            )
            picks = sorted_by_rating[:k_per_cluster]
        cluster_label = cluster_names.get(cid, f"Cluster {cid}")
        for idx in picks:
            h = state.headline_data[idx]
            sections.append({
                "cat": cluster_label,
                "headline": h.get("title", ""),
                "link": h.get("url", ""),
                "rating": h.get("rating", 0.0),
                "summary": h.get("summary", ""),
                "id": idx,
            })
    state.newsletter_section_data = sections
    state.clusters = {}
    for cid, indices in by_cluster.items():
        if cid < 0:
            continue
        name = cluster_names.get(cid, f"Cluster {cid}")
        state.clusters[name] = [str(i) for i in indices]
    return sections


# ──────────────────────────────────────────────────────────────────────
# Prepare mode
# ──────────────────────────────────────────────────────────────────────

def _render_assign_prompts(headlines: list[dict], clusters: list[dict], allow_new: bool):
    cfg = get_prompt("assign_noise_batch")
    validated = AssignNoiseBatchInput.model_validate({
        "headlines": headlines,
        "clusters": clusters,
        "allow_new": allow_new,
    })
    user = cfg.user_prompt.format(**validated.model_dump())
    return cfg.system_prompt, user


def _render_merge_prompts(pairs: list[dict]):
    cfg = get_prompt("merge_clusters_batch")
    validated = MergeClustersBatchInput.model_validate({"pairs": pairs})
    user = cfg.user_prompt.format(**validated.model_dump())
    return cfg.system_prompt, user


def _prepare_assign_batches(
    session_id: str, state: NewsletterAgentState,
    by_cluster, cluster_names, batch_size: int,
) -> list[Path]:
    noise_indices = list(by_cluster.get(-1, []))
    if not noise_indices or not cluster_names:
        return []
    clusters = _cluster_descriptors(state, by_cluster, cluster_names)
    runs_dir = Path("runs") / session_id
    batches_dir = runs_dir / _ASSIGN_BATCHES_SUBDIR
    if batches_dir.exists():
        for p in batches_dir.glob("batch-*.json"):
            p.unlink()
    batches_dir.mkdir(parents=True, exist_ok=True)

    items = [
        {
            "id": str(idx),  # state.headline_data index, stringified
            "title": state.headline_data[idx].get("title", ""),
            "summary": state.headline_data[idx].get("summary", ""),
        }
        for idx in noise_indices
    ]
    paths: list[Path] = []
    for batch_idx, start in enumerate(range(0, len(items), batch_size)):
        chunk = items[start:start + batch_size]
        system, user = _render_assign_prompts(chunk, clusters, allow_new=True)
        payload = {
            "batch_id": batch_idx,
            "session_id": session_id,
            "ids": [it["id"] for it in chunk],
            "headlines": chunk,
            "clusters": clusters,
            "allow_new": True,
            "system_prompt": system,
            "user_prompt": user,
            "output_schema": AssignNoiseBatchOutput.model_json_schema(),
        }
        path = batches_dir / f"batch-{batch_idx:03d}.json"
        path.write_text(json.dumps(payload, indent=2))
        paths.append(path)
    return paths


def _prepare_merge_batch(
    session_id: str, state: NewsletterAgentState,
    by_cluster, cluster_names,
) -> Optional[Path]:
    candidate_pairs = _find_merge_candidates(state, by_cluster, cluster_names)
    if not candidate_pairs:
        return None
    pairs_payload = []
    for i, (cid_a, cid_b) in enumerate(candidate_pairs):
        a_titles = [state.headline_data[ix]["title"] for ix in by_cluster[cid_a][:5]]
        b_titles = [state.headline_data[ix]["title"] for ix in by_cluster[cid_b][:5]]
        pairs_payload.append({
            "pair_id": str(i),
            "a": {"id": str(cid_a), "name": cluster_names[cid_a], "top_headlines": a_titles},
            "b": {"id": str(cid_b), "name": cluster_names[cid_b], "top_headlines": b_titles},
        })
    runs_dir = Path("runs") / session_id
    batches_dir = runs_dir / _MERGE_BATCHES_SUBDIR
    if batches_dir.exists():
        for p in batches_dir.glob("batch-*.json"):
            p.unlink()
    batches_dir.mkdir(parents=True, exist_ok=True)
    system, user = _render_merge_prompts(pairs_payload)
    payload = {
        "batch_id": 0,
        "session_id": session_id,
        "ids": [p["pair_id"] for p in pairs_payload],
        "pairs": pairs_payload,
        "system_prompt": system,
        "user_prompt": user,
        "output_schema": MergeClustersBatchOutput.model_json_schema(),
    }
    path = batches_dir / "batch-000.json"
    path.write_text(json.dumps(payload, indent=2))
    return path


# ──────────────────────────────────────────────────────────────────────
# Apply mode
# ──────────────────────────────────────────────────────────────────────

def _load_assign_results(results_dir: Path) -> tuple[dict[str, str], list[str]]:
    out: dict[str, str] = {}
    problems: list[str] = []
    if not results_dir.exists():
        problems.append(f"assign results dir not found: {results_dir}")
        return {}, problems
    for f in sorted(results_dir.glob("batch-*.json")):
        try:
            raw = json.loads(f.read_text())
            parsed = AssignNoiseBatchOutput.model_validate(raw)
        except (json.JSONDecodeError, ValidationError) as exc:
            problems.append(f"{f.name}: {exc}")
            continue
        for a in parsed.assignments:
            out[a.id] = a.assignment
    return out, problems


def _load_merge_results(results_dir: Path) -> tuple[dict[str, tuple[bool, Optional[str]]], list[str]]:
    out: dict[str, tuple[bool, Optional[str]]] = {}
    problems: list[str] = []
    if not results_dir.exists():
        # Merge results are optional (no candidate pairs → no dir → no problem)
        return {}, problems
    for f in sorted(results_dir.glob("batch-*.json")):
        try:
            raw = json.loads(f.read_text())
            parsed = MergeClustersBatchOutput.model_validate(raw)
        except (json.JSONDecodeError, ValidationError) as exc:
            problems.append(f"{f.name}: {exc}")
            continue
        for d in parsed.decisions:
            out[d.pair_id] = (d.merge, d.merged_name)
    return out, problems


def _apply_assignments(
    state: NewsletterAgentState,
    by_cluster: Dict[int, List[int]],
    cluster_names: Dict[int, str],
    assignments: Dict[str, str],
) -> int:
    """Apply noise assignments. Returns count of headlines newly attached to a cluster."""
    noise_assigned = 0
    next_new_cid = (max(list(by_cluster.keys()) + [-1]) + 1)
    for idx_str, assignment in assignments.items():
        try:
            idx = int(idx_str)
        except ValueError:
            continue
        if idx >= len(state.headline_data):
            continue
        h = state.headline_data[idx]
        if h.get("cluster_id", -2) != -1:
            continue  # only operate on noise points

        if assignment == "none":
            h["cluster_id"] = -2  # drop marker
        elif assignment == "new":
            new_cid = next_new_cid
            next_new_cid += 1
            label = h.get("title", "")[:60]
            h["cluster_id"] = new_cid
            h["cluster_name"] = label
            by_cluster[new_cid].append(idx)
            cluster_names[new_cid] = label
            noise_assigned += 1
        else:
            try:
                target_cid = int(assignment)
            except ValueError:
                h["cluster_id"] = -2
                continue
            if target_cid in cluster_names:
                h["cluster_id"] = target_cid
                h["cluster_name"] = cluster_names[target_cid]
                by_cluster[target_cid].append(idx)
                noise_assigned += 1
            else:
                h["cluster_id"] = -2

    # Drop -2 markers
    state.headline_data = [h for h in state.headline_data if h.get("cluster_id", -2) != -2]

    # Rebuild by_cluster from cleaned headline list
    by_cluster.clear()
    for i, h in enumerate(state.headline_data):
        cid = h.get("cluster_id", -1)
        by_cluster[cid].append(i)
    return noise_assigned


def _apply_merges(
    state: NewsletterAgentState,
    by_cluster: Dict[int, List[int]],
    cluster_names: Dict[int, str],
    pairs_payload: list[dict],
    decisions: Dict[str, tuple[bool, Optional[str]]],
) -> int:
    merges_done = 0
    for pair in pairs_payload:
        pair_id = pair["pair_id"]
        cid_a = int(pair["a"]["id"])
        cid_b = int(pair["b"]["id"])
        if pair_id not in decisions:
            continue
        merge, merged_name_opt = decisions[pair_id]
        if not merge:
            continue
        if cid_a not in by_cluster or cid_b not in by_cluster:
            continue
        merged_name = merged_name_opt or cluster_names[cid_a]
        for idx in by_cluster[cid_b]:
            state.headline_data[idx]["cluster_id"] = cid_a
            state.headline_data[idx]["cluster_name"] = merged_name
        for idx in by_cluster[cid_a]:
            state.headline_data[idx]["cluster_name"] = merged_name
        by_cluster[cid_a].extend(by_cluster[cid_b])
        del by_cluster[cid_b]
        cluster_names[cid_a] = merged_name
        if cid_b in cluster_names:
            del cluster_names[cid_b]
        merges_done += 1
    return merges_done


# ──────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────

@click.command()
@click.option("--db", "db_path", default="newsletter_agent.db")
@click.option("--session", "session_id", required=True)
@click.option("--k", "k_per_cluster", default=_DEFAULT_K_PER_CLUSTER, type=int)
@click.option("--lambda", "mmr_lambda", default=_MMR_LAMBDA, type=float)
@click.option("--engine", default=None, help="Override LLM engine for classic mode")
@click.option("--no-noise-assign", is_flag=True, help="Skip LLM noise-point assignment")
@click.option("--no-merge", is_flag=True, help="Skip LLM cluster merge step")
@click.option("--prepare-batches", is_flag=True,
              help="Write batches to runs/<SID>/select-{assign,merge}-batches/ for subagent dispatch")
@click.option("--apply-results", "apply_results_dir", default=None,
              help="Read results from runs/<SID>/select-{assign,merge}-results/ and apply")
@click.option("--batch-size", type=int, default=_DEFAULT_ASSIGN_BATCH,
              show_default=True, help="Noise headlines per assign batch (prepare mode)")
def cli(
    db_path: str,
    session_id: str,
    k_per_cluster: int,
    mmr_lambda: float,
    engine: Optional[str],
    no_noise_assign: bool,
    no_merge: bool,
    prepare_batches: bool,
    apply_results_dir: Optional[str],
    batch_size: int,
) -> None:
    if prepare_batches and apply_results_dir:
        raise click.UsageError("--prepare-batches and --apply-results are mutually exclusive")

    state = NewsletterAgentState(session_id=session_id, db_path=db_path).load_latest_from_db()
    if state is None:
        raise click.ClickException(f"No state found for session {session_id}")

    by_cluster, cluster_names = _build_cluster_snapshot(state)

    # ── prepare mode ───────────────────────────────────────────────
    if prepare_batches:
        state.start_step("select")
        state.save_checkpoint("select")

        assign_paths = (
            [] if no_noise_assign else
            _prepare_assign_batches(session_id, state, by_cluster, cluster_names, batch_size)
        )
        merge_path = (
            None if no_merge else
            _prepare_merge_batch(session_id, state, by_cluster, cluster_names)
        )

        click.echo(f"Prepared {len(assign_paths)} assign batch(es).")
        for p in assign_paths:
            click.echo(f"  {p}")
        if merge_path is not None:
            click.echo(f"Prepared merge batch: {merge_path}")
        else:
            click.echo("No merge-candidate pairs found; merge batch skipped.")
        click.echo(f"\nDispatch one Haiku subagent per batch. Write each result to:")
        click.echo(f"  runs/{session_id}/{_ASSIGN_RESULTS_SUBDIR}/batch-NNN.json (assign)")
        click.echo(f"  runs/{session_id}/{_MERGE_RESULTS_SUBDIR}/batch-000.json (merge)")
        click.echo(f"Then run: python -m lib.steps.select --session {session_id} "
                   f"--apply-results runs/{session_id}")
        return

    # ── apply mode ─────────────────────────────────────────────────
    if apply_results_dir:
        root = Path(apply_results_dir)
        assignments, a_problems = _load_assign_results(root / _ASSIGN_RESULTS_SUBDIR)
        decisions, m_problems = _load_merge_results(root / _MERGE_RESULTS_SUBDIR)
        problems = a_problems + m_problems
        if problems:
            click.echo("Apply found problems:", err=True)
            for p in problems:
                click.echo(f"  - {p}", err=True)

        noise_assigned = _apply_assignments(state, by_cluster, cluster_names, assignments)

        # Re-derive merge pairs payload by reading the merge batch file
        merge_batch_file = Path("runs") / session_id / _MERGE_BATCHES_SUBDIR / "batch-000.json"
        pairs_payload = []
        if merge_batch_file.exists():
            pairs_payload = json.loads(merge_batch_file.read_text()).get("pairs", [])
        merges_done = _apply_merges(state, by_cluster, cluster_names, pairs_payload, decisions)

        sections = _run_mmr_and_finalize(state, by_cluster, cluster_names, k_per_cluster, mmr_lambda)

        state.complete_step(
            "select",
            message=f"{len(sections)} headlines across {len(state.clusters)} sections",
        )
        state.save_checkpoint("select")

        runs_dir = Path("runs") / session_id
        runs_dir.mkdir(parents=True, exist_ok=True)
        (runs_dir / "select.json").write_text(json.dumps({
            "session_id": session_id,
            "completed_at": datetime.now().isoformat(),
            "noise_assigned": noise_assigned,
            "merges_done": merges_done,
            "sections_count": len(state.clusters),
            "headlines_selected": len(sections),
            "k_per_cluster": k_per_cluster,
            "mmr_lambda": mmr_lambda,
        }, indent=2))
        click.echo(
            f"Select: {len(sections)} headlines across {len(state.clusters)} sections "
            f"(noise_assigned={noise_assigned}, merges={merges_done})."
        )
        return

    # ── classic mode ───────────────────────────────────────────────
    # (unchanged behavior — kept verbatim from current select.py for cron/CI)
    state.start_step("select")
    state.save_checkpoint("select")

    noise_indices = list(by_cluster.get(-1, []))
    noise_assigned = 0
    if not no_noise_assign and noise_indices and cluster_names:
        existing = _cluster_descriptors(state, by_cluster, cluster_names)
        for idx in noise_indices:
            h = state.headline_data[idx]
            try:
                result = call_prompt("assign_noise", {
                    "headline_title": h.get("title", ""),
                    "headline_summary": h.get("summary", ""),
                    "clusters": existing,
                    "allow_new": True,
                }, engine=engine)
                assignment = result.assignment
            except Exception:
                assignment = "none"

            if assignment == "none":
                h["cluster_id"] = -2
            elif assignment == "new":
                all_cids = list(by_cluster.keys())
                new_cid = max(all_cids) + 1 if all_cids else 0
                label = h.get("title", "")[:60]
                h["cluster_id"] = new_cid
                h["cluster_name"] = label
                by_cluster[new_cid].append(idx)
                cluster_names[new_cid] = label
                noise_assigned += 1
            else:
                try:
                    target_cid = int(assignment)
                except ValueError:
                    h["cluster_id"] = -2
                    continue
                if target_cid in cluster_names:
                    h["cluster_id"] = target_cid
                    h["cluster_name"] = cluster_names[target_cid]
                    by_cluster[target_cid].append(idx)
                    noise_assigned += 1
                else:
                    h["cluster_id"] = -2

    state.headline_data = [h for h in state.headline_data if h.get("cluster_id", -2) != -2]
    by_cluster.clear()
    for i, h in enumerate(state.headline_data):
        cid = h.get("cluster_id", -1)
        by_cluster[cid].append(i)

    merges_done = 0
    if not no_merge and len(cluster_names) >= 2:
        candidate_pairs = _find_merge_candidates(state, by_cluster, cluster_names)
        for cid_a, cid_b in candidate_pairs:
            if cid_a not in by_cluster or cid_b not in by_cluster:
                continue
            a_titles = [state.headline_data[i]["title"] for i in by_cluster[cid_a][:5]]
            b_titles = [state.headline_data[i]["title"] for i in by_cluster[cid_b][:5]]
            try:
                result = call_prompt("merge_clusters", {
                    "a": {"id": str(cid_a), "name": cluster_names[cid_a], "top_headlines": a_titles},
                    "b": {"id": str(cid_b), "name": cluster_names[cid_b], "top_headlines": b_titles},
                }, engine=engine)
            except Exception:
                continue
            if result.merge:
                merged_name = result.merged_name or cluster_names[cid_a]
                for idx in by_cluster[cid_b]:
                    state.headline_data[idx]["cluster_id"] = cid_a
                    state.headline_data[idx]["cluster_name"] = merged_name
                for idx in by_cluster[cid_a]:
                    state.headline_data[idx]["cluster_name"] = merged_name
                by_cluster[cid_a].extend(by_cluster[cid_b])
                del by_cluster[cid_b]
                cluster_names[cid_a] = merged_name
                if cid_b in cluster_names:
                    del cluster_names[cid_b]
                merges_done += 1

    sections = _run_mmr_and_finalize(state, by_cluster, cluster_names, k_per_cluster, mmr_lambda)

    state.complete_step("select", message=f"{len(sections)} headlines across {len(state.clusters)} sections")
    state.save_checkpoint("select")

    runs_dir = Path("runs") / session_id
    runs_dir.mkdir(parents=True, exist_ok=True)
    (runs_dir / "select.json").write_text(json.dumps({
        "session_id": session_id,
        "completed_at": datetime.now().isoformat(),
        "noise_assigned": noise_assigned,
        "merges_done": merges_done,
        "sections_count": len(state.clusters),
        "headlines_selected": len(sections),
        "k_per_cluster": k_per_cluster,
        "mmr_lambda": mmr_lambda,
    }, indent=2))
    click.echo(
        f"Select: {len(sections)} headlines across {len(state.clusters)} sections "
        f"(noise_assigned={noise_assigned}, merges={merges_done})."
    )


if __name__ == "__main__":  # pragma: no cover
    sys.exit(cli())
