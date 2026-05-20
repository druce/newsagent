"""newsagent:cluster — UMAP+HDBSCAN topic clustering with LLM-based cluster naming.

Steps:
1. Load state. Filter to headlines with summary set.
2. For headlines missing embedding, embed them (concat title + summary).
3. Stack embeddings, load UMAP reducer, apply, run optimize_hdbscan.
4. Assign cluster_id to each headline.
5. For each non-noise cluster, sample top-5 by rating, call NAME_TOPIC,
   store cluster_name on each headline + in state.clusters.
6. Write runs/<SID>/cluster.json with per-cluster metrics.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import List

import click
import lib.prompts  # noqa: F401 — register all prompts
from lib.clustering import apply_umap, load_umap_reducer, optimize_hdbscan
from lib.embeddings import embed_texts
from lib.llm import call_prompt
from lib.state import NewsletterAgentState


@click.command()
@click.option("--db", "db_path", default="newsletter_agent.db")
@click.option("--session", "session_id", required=True)
@click.option("--n-trials", default=30, type=int, help="Optuna trials for HDBSCAN tuning")
@click.option("--umap-path", default="umap_reducer.pkl", help="Path to pretrained UMAP reducer")
def cli(db_path: str, session_id: str, n_trials: int, umap_path: str) -> None:
    state = NewsletterAgentState(session_id=session_id, db_path=db_path).load_latest_from_db()
    if state is None:
        raise click.ClickException(f"No state found for session {session_id}")

    # Filter to headlines with a summary
    candidates = [h for h in state.headline_data if h.get("summary")]
    if not candidates:
        click.echo("Nothing to cluster (no headlines with summaries).")
        state.start_step("cluster")
        state.complete_step("cluster", message="no headlines with summaries")
        state.save_checkpoint("cluster")
        return

    state.start_step("cluster")
    state.save_checkpoint("cluster")

    # Embed any headlines that are missing embeddings
    missing = [h for h in candidates if not h.get("embedding")]
    if missing:
        texts = [(h.get("title", "") + " " + h.get("summary", "")).strip() for h in missing]
        vectors = embed_texts(texts)
        for h, v in zip(missing, vectors):
            h["embedding"] = v

    # Apply UMAP
    embeddings: List[List[float]] = [h["embedding"] for h in candidates]
    reducer = load_umap_reducer(umap_path)
    reduced = apply_umap(embeddings, reducer)

    # Optuna-tuned HDBSCAN
    labels, metrics = optimize_hdbscan(reduced, n_trials=n_trials)

    # Assign cluster_id to each candidate headline
    for h, label in zip(candidates, labels):
        h["cluster_id"] = int(label)

    # Determine unique non-noise clusters
    non_noise_ids = sorted(set(int(l) for l in labels if l >= 0))

    # Build a map: cluster_int_id -> list of headline dicts in that cluster
    cluster_to_headlines: dict[int, List[dict]] = {}
    for h in candidates:
        cid = h["cluster_id"]
        if cid >= 0:
            cluster_to_headlines.setdefault(cid, []).append(h)

    # For each cluster, name it via NAME_TOPIC
    state.clusters = {}
    cluster_summary: dict[str, dict] = {}

    for cid in non_noise_ids:
        cluster_headlines = cluster_to_headlines[cid]
        # Sort by rating descending (fall back to original order if no rating)
        sorted_hl = sorted(cluster_headlines, key=lambda h: h.get("rating", 0.0), reverse=True)
        top5 = sorted_hl[:5]

        # Build prompt inputs
        entities = ", ".join(h.get("title", "")[:30] for h in top5)
        headlines_str = "\n".join(h.get("title", "") for h in top5)

        result = call_prompt("name_topic", {"entities": entities, "headlines": headlines_str})
        cluster_name = result.title

        # Store cluster_name on each headline in this cluster
        for h in cluster_headlines:
            h["cluster_name"] = cluster_name

        # Build state.clusters: {cluster_name: [url, ...]}
        state.clusters[cluster_name] = [h["url"] for h in cluster_headlines]

        cluster_summary[str(cid)] = {
            "name": cluster_name,
            "count": len(cluster_headlines),
            "sample_titles": [h.get("title", "") for h in top5[:3]],
        }

    # Mark noise headlines (cluster_id=-1) with no cluster_name
    noise_count = sum(1 for h in candidates if h.get("cluster_id") == -1)

    n_clusters = len(non_noise_ids)
    state.complete_step("cluster", message=f"{n_clusters} clusters, {noise_count} noise points")
    state.save_checkpoint("cluster")

    # Write run artifact
    runs_dir = Path("runs") / session_id
    runs_dir.mkdir(parents=True, exist_ok=True)
    (runs_dir / "cluster.json").write_text(json.dumps({
        "session_id": session_id,
        "completed_at": datetime.now().isoformat(),
        "n_candidates": len(candidates),
        "n_clusters": n_clusters,
        "noise_count": noise_count,
        "noise_ratio": metrics.get("noise_ratio", 0.0),
        "silhouette": metrics.get("silhouette"),
        "best_params": metrics.get("best_params", {}),
        "clusters": cluster_summary,
    }, indent=2))

    click.echo(
        f"Cluster: {n_clusters} clusters, {noise_count} noise points "
        f"({len(candidates)} candidates)."
    )


if __name__ == "__main__":  # pragma: no cover
    sys.exit(cli())
