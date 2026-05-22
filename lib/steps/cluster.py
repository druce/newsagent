"""newsagent:cluster — UMAP+HDBSCAN topic clustering with LLM-based cluster naming.

Three modes (mirror of lib/steps/filter.py):
  1. classic (default): single in-process NAME_TOPIC call per cluster.
     Use --engine to override.
  2. --prepare-batches: run UMAP+HDBSCAN, then write one
     runs/<SID>/cluster-batches/batch-000.json containing all clusters for
     a single Haiku Agent to name in one call.
  3. --apply-results DIR: read result JSON from DIR, write cluster_name back
     to state, complete the step.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Optional

import click
from pydantic import ValidationError

import lib.prompts  # noqa: F401 — register all prompts
from lib.clustering import apply_umap, load_umap_reducer, normalize_l2, optimize_hdbscan
from lib.embeddings import embed_texts
from lib.llm import call_prompt, get_prompt
from lib.prompts.name_topic_batch import NameTopicBatchInput, NameTopicBatchOutput
from lib.state import NewsletterAgentState


_BATCHES_SUBDIR = "cluster-batches"
_RESULTS_SUBDIR = "cluster-results"


def _do_clustering(
    state: NewsletterAgentState,
    umap_path: str,
    n_trials: int,
    use_umap: bool = True,
):
    """Run UMAP+HDBSCAN (or HDBSCAN on L2-normalized embeddings) and assign cluster_id.

    Returns (cluster_to_headlines, metrics, candidates).
    cluster_to_headlines maps int cluster_id -> list of headline dicts (non-noise only).
    """
    candidates = [h for h in state.headline_data if h.get("summary")]
    if not candidates:
        return {}, {}, []

    missing = [h for h in candidates if not h.get("embedding")]
    if missing:
        texts = [(h.get("title", "") + " " + h.get("summary", "")).strip() for h in missing]
        vectors = embed_texts(texts)
        for h, v in zip(missing, vectors):
            h["embedding"] = v

    embeddings: List[List[float]] = [h["embedding"] for h in candidates]
    if use_umap:
        reducer = load_umap_reducer(umap_path)
        reduced = apply_umap(embeddings, reducer)
    else:
        # Skip UMAP; cluster directly on L2-normalized embeddings.
        # Euclidean on unit vectors ranks neighbors identically to cosine.
        reduced = normalize_l2(embeddings)
    labels, metrics = optimize_hdbscan(reduced, n_trials=n_trials)

    for h, label in zip(candidates, labels):
        h["cluster_id"] = int(label)

    cluster_to_headlines: dict[int, list[dict]] = {}
    for h in candidates:
        cid = h["cluster_id"]
        if cid >= 0:
            cluster_to_headlines.setdefault(cid, []).append(h)
    return cluster_to_headlines, metrics, candidates


def _build_batch_clusters(cluster_to_headlines: dict[int, list[dict]]) -> list[dict]:
    """Build the per-cluster input items for the batch JSON."""
    out: list[dict] = []
    for cid in sorted(cluster_to_headlines.keys()):
        hl = cluster_to_headlines[cid]
        sorted_hl = sorted(hl, key=lambda h: h.get("rating", 0.0), reverse=True)
        top5 = sorted_hl[:5]
        out.append({
            "cluster_id": str(cid),
            "entities": ", ".join(h.get("title", "")[:30] for h in top5),
            "headlines": "\n".join(h.get("title", "") for h in top5),
        })
    return out


def _render_prompts(clusters: list[dict]) -> tuple[str, str]:
    cfg = get_prompt("name_topic_batch")
    validated = NameTopicBatchInput.model_validate({"clusters": clusters})
    user = cfg.user_prompt.format(**validated.model_dump())
    return cfg.system_prompt, user


def _write_batch(session_id: str, clusters: list[dict]) -> Path:
    runs_dir = Path("runs") / session_id
    batches_dir = runs_dir / _BATCHES_SUBDIR
    if batches_dir.exists():
        for p in batches_dir.glob("batch-*.json"):
            p.unlink()
    batches_dir.mkdir(parents=True, exist_ok=True)
    system, user = _render_prompts(clusters)
    payload = {
        "batch_id": 0,
        "session_id": session_id,
        "ids": [c["cluster_id"] for c in clusters],
        "clusters": clusters,
        "system_prompt": system,
        "user_prompt": user,
        "output_schema": NameTopicBatchOutput.model_json_schema(),
    }
    path = batches_dir / "batch-000.json"
    path.write_text(json.dumps(payload, indent=2))
    return path


def _load_results(results_dir: Path, expected_ids: set[str]) -> tuple[dict[str, str], list[str]]:
    """Returns (names: cluster_id -> name, problems)."""
    names: dict[str, str] = {}
    problems: list[str] = []
    if not results_dir.exists():
        problems.append(f"results dir not found: {results_dir}")
        return {}, problems
    files = sorted(results_dir.glob("batch-*.json"))
    if not files:
        problems.append(f"no batch-*.json files in {results_dir}")
        return {}, problems
    seen: set[str] = set()
    for f in files:
        try:
            raw = json.loads(f.read_text())
        except json.JSONDecodeError as exc:
            problems.append(f"{f.name}: invalid JSON ({exc})")
            continue
        try:
            parsed = NameTopicBatchOutput.model_validate(raw)
        except ValidationError as exc:
            problems.append(f"{f.name}: schema mismatch ({exc.error_count()} errors)")
            continue
        for n in parsed.names:
            if n.cluster_id in seen:
                problems.append(f"{f.name}: duplicate cluster_id {n.cluster_id!r}")
            seen.add(n.cluster_id)
            names[n.cluster_id] = n.name
    missing = expected_ids - seen
    extra = seen - expected_ids
    if missing:
        problems.append(f"missing names for cluster_ids: {sorted(missing)[:10]}...")
    if extra:
        problems.append(f"unexpected cluster_ids in results: {sorted(extra)[:10]}...")
    return names, problems


def _apply_names(
    state: NewsletterAgentState,
    names: dict[str, str],
) -> tuple[int, int]:
    """Apply names to headlines + state.clusters. Returns (n_clusters, noise_count)."""
    state.clusters = {}
    cluster_to_headlines: dict[int, list[dict]] = {}
    for h in state.headline_data:
        cid = h.get("cluster_id")
        if cid is None:
            continue
        if cid >= 0:
            cluster_to_headlines.setdefault(cid, []).append(h)

    for cid_int, hl in cluster_to_headlines.items():
        name = names.get(str(cid_int))
        if name is None:
            continue
        for h in hl:
            h["cluster_name"] = name
        state.clusters[name] = [h["url"] for h in hl]

    n_clusters = len(state.clusters)
    noise_count = sum(1 for h in state.headline_data if h.get("cluster_id") == -1)
    return n_clusters, noise_count


def _write_report(
    session_id: str, n_candidates: int, n_clusters: int,
    noise_count: int, metrics: dict, cluster_summary: dict,
) -> None:
    runs_dir = Path("runs") / session_id
    runs_dir.mkdir(parents=True, exist_ok=True)
    (runs_dir / "cluster.json").write_text(json.dumps({
        "session_id": session_id,
        "completed_at": datetime.now().isoformat(),
        "n_candidates": n_candidates,
        "n_clusters": n_clusters,
        "noise_count": noise_count,
        "noise_ratio": metrics.get("noise_ratio", 0.0),
        "silhouette": metrics.get("silhouette"),
        "best_params": metrics.get("best_params", {}),
        "clusters": cluster_summary,
    }, indent=2))


@click.command()
@click.option("--db", "db_path", default="newsletter_agent.db")
@click.option("--session", "session_id", required=True)
@click.option("--n-trials", default=50, type=int, help="Optuna trials for HDBSCAN tuning")
@click.option("--umap-path", default="umap_reducer.pkl", help="Path to pretrained UMAP reducer")
@click.option("--no-umap", "no_umap", is_flag=True,
              help="Skip UMAP; run HDBSCAN directly on L2-normalized embeddings")
@click.option("--engine", default=None,
              help="Override engine for classic mode")
@click.option("--prepare-batches", is_flag=True,
              help="Write batches to runs/<SID>/cluster-batches/ for subagent dispatch")
@click.option("--apply-results", "apply_results", default=None,
              help="Read result JSON from this dir and apply to state")
def cli(
    db_path: str,
    session_id: str,
    n_trials: int,
    umap_path: str,
    no_umap: bool,
    engine: Optional[str],
    prepare_batches: bool,
    apply_results: Optional[str],
) -> None:
    if prepare_batches and apply_results:
        raise click.UsageError("--prepare-batches and --apply-results are mutually exclusive")

    state = NewsletterAgentState(session_id=session_id, db_path=db_path).load_latest_from_db()
    if state is None:
        raise click.ClickException(f"No state found for session {session_id}")

    # ── prepare mode ───────────────────────────────────────────────
    if prepare_batches:
        state.start_step("cluster")
        state.save_checkpoint("cluster")
        cluster_to_headlines, metrics, candidates = _do_clustering(state, umap_path, n_trials, use_umap=not no_umap)
        if not cluster_to_headlines:
            click.echo("Nothing to cluster (no non-noise clusters).")
            state.complete_step("cluster", message="no non-noise clusters")
            state.save_checkpoint("cluster")
            return
        # Persist cluster_id assignments now so apply can read them.
        state.save_checkpoint("cluster")
        clusters_for_batch = _build_batch_clusters(cluster_to_headlines)
        path = _write_batch(session_id, clusters_for_batch)
        click.echo(f"Prepared 1 batch ({len(clusters_for_batch)} clusters): {path}")
        click.echo(f"\nDispatch one Haiku subagent. Write result to:")
        click.echo(f"  runs/{session_id}/{_RESULTS_SUBDIR}/batch-000.json")
        click.echo(f"Then run: python -m lib.steps.cluster --session {session_id} "
                   f"--apply-results runs/{session_id}/{_RESULTS_SUBDIR}")
        return

    # ── apply mode ─────────────────────────────────────────────────
    if apply_results:
        # cluster_ids were already assigned during prepare; we just need to
        # know which ones are non-noise.
        expected_ids = {
            str(h["cluster_id"])
            for h in state.headline_data
            if h.get("cluster_id") is not None and h["cluster_id"] >= 0
        }
        names, problems = _load_results(Path(apply_results), expected_ids)
        if problems:
            click.echo("Apply found problems:", err=True)
            for p in problems:
                click.echo(f"  - {p}", err=True)
            if not names:
                raise click.ClickException("no usable names; redispatch failed batches")

        n_clusters, noise_count = _apply_names(state, names)
        state.complete_step("cluster", message=f"{n_clusters} clusters, {noise_count} noise points")
        state.save_checkpoint("cluster")

        cluster_summary = {
            name: {
                "name": name,
                "count": len(urls),
                "sample_titles": [],
            }
            for name, urls in state.clusters.items()
        }
        _write_report(session_id, len(state.headline_data), n_clusters, noise_count, {}, cluster_summary)
        click.echo(f"Cluster: {n_clusters} clusters, {noise_count} noise points.")
        return

    # ── classic mode ───────────────────────────────────────────────
    state.start_step("cluster")
    state.save_checkpoint("cluster")
    cluster_to_headlines, metrics, candidates = _do_clustering(state, umap_path, n_trials, use_umap=not no_umap)
    if not cluster_to_headlines:
        click.echo("Nothing to cluster.")
        state.complete_step("cluster", message="no non-noise clusters")
        state.save_checkpoint("cluster")
        return

    state.clusters = {}
    cluster_summary: dict[str, dict] = {}
    for cid in sorted(cluster_to_headlines.keys()):
        hl = cluster_to_headlines[cid]
        sorted_hl = sorted(hl, key=lambda h: h.get("rating", 0.0), reverse=True)
        top5 = sorted_hl[:5]
        entities = ", ".join(h.get("title", "")[:30] for h in top5)
        headlines_str = "\n".join(h.get("title", "") for h in top5)
        result = call_prompt("name_topic", {"entities": entities, "headlines": headlines_str}, engine=engine)
        cluster_name = result.title
        for h in hl:
            h["cluster_name"] = cluster_name
        state.clusters[cluster_name] = [h["url"] for h in hl]
        cluster_summary[str(cid)] = {
            "name": cluster_name,
            "count": len(hl),
            "sample_titles": [h.get("title", "") for h in top5[:3]],
        }

    n_clusters = len(cluster_to_headlines)
    noise_count = sum(1 for h in candidates if h.get("cluster_id") == -1)
    state.complete_step("cluster", message=f"{n_clusters} clusters, {noise_count} noise points")
    state.save_checkpoint("cluster")
    _write_report(session_id, len(candidates), n_clusters, noise_count, metrics, cluster_summary)
    click.echo(
        f"Cluster: {n_clusters} clusters, {noise_count} noise points ({len(candidates)} candidates)."
    )


if __name__ == "__main__":  # pragma: no cover
    sys.exit(cli())
