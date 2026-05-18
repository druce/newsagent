"""news:select — LLM noise assignment + cluster merge + MMR diversity selection.

Steps:
1. Load state. Group headlines by cluster_id.
2. Assign HDBSCAN noise points (cluster_id=-1) to existing clusters via assign_noise
   prompt, or drop them if assignment is "none", or create a new singleton cluster
   if assignment is "new".
3. Merge near-duplicate clusters: embed cluster names, find pairs with cosine
   similarity >= _MERGE_SIM_THRESHOLD, call merge_clusters prompt for confirmation.
4. MMR top-K per cluster: run mmr_select with ratings as relevance.
5. Write state.newsletter_section_data + state.clusters.
6. Mark select complete, write runs/<SID>/select.json.
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

import lib.prompts  # noqa: F401 — register all prompts
from lib.embeddings import embed_texts
from lib.llm import call_prompt
from lib.mmr import mmr_select
from lib.state import NewsletterAgentState


_DEFAULT_K_PER_CLUSTER = 5
_MMR_LAMBDA = 0.7
_MERGE_SIM_THRESHOLD = 0.85


@click.command()
@click.option("--db", "db_path", default="newsletter_agent.db")
@click.option("--session", "session_id", required=True)
@click.option("--k", "k_per_cluster", default=_DEFAULT_K_PER_CLUSTER, type=int,
              help="Max headlines per cluster section (default 5)")
@click.option("--lambda", "mmr_lambda", default=_MMR_LAMBDA, type=float,
              help="MMR lambda: 1.0=pure-relevance, 0.0=pure-diversity")
@click.option("--engine", default=None, help="Override LLM engine for all prompts")
@click.option("--no-noise-assign", is_flag=True, help="Skip LLM noise-point assignment")
@click.option("--no-merge", is_flag=True, help="Skip LLM cluster merge step")
def cli(
    db_path: str,
    session_id: str,
    k_per_cluster: int,
    mmr_lambda: float,
    engine: Optional[str],
    no_noise_assign: bool,
    no_merge: bool,
) -> None:
    state = NewsletterAgentState(session_id=session_id, db_path=db_path).load_latest_from_db()
    if state is None:
        raise click.ClickException(f"No state found for session {session_id}")

    state.start_step("select")
    state.save_checkpoint("select")

    # ----- Step 1: build current cluster snapshot -----
    by_cluster: Dict[int, List[int]] = defaultdict(list)
    for i, h in enumerate(state.headline_data):
        cid = h.get("cluster_id", -1)
        by_cluster[cid].append(i)

    # Build cluster_id -> name mapping (skip noise)
    cluster_names: Dict[int, str] = {}
    for cid, indices in by_cluster.items():
        if cid < 0:
            continue
        name = state.headline_data[indices[0]].get("cluster_name", f"Cluster {cid}")
        cluster_names[cid] = name

    # ----- Step 2: assign noise points -----
    noise_indices = list(by_cluster.get(-1, []))
    noise_assigned = 0
    if not no_noise_assign and noise_indices and cluster_names:
        # Build cluster descriptors for the prompt
        existing = []
        for cid in sorted(cluster_names.keys()):
            name = cluster_names[cid]
            sample_titles = [
                state.headline_data[i]["title"]
                for i in by_cluster[cid][:3]
            ]
            existing.append({
                "id": str(cid),
                "name": name,
                "sample_headlines": sample_titles,
            })

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
                # Mark for dropping
                h["cluster_id"] = -2
            elif assignment == "new":
                # Create a singleton cluster with a new id
                all_cids = list(by_cluster.keys())
                new_cid = max(all_cids) + 1 if all_cids else 0
                cluster_label = h.get("title", "")[:60]
                h["cluster_id"] = new_cid
                h["cluster_name"] = cluster_label
                by_cluster[new_cid].append(idx)
                cluster_names[new_cid] = cluster_label
                noise_assigned += 1
            else:
                # Try to parse as int cluster id
                try:
                    target_cid = int(assignment)
                except ValueError:
                    h["cluster_id"] = -2  # unrecognised -> drop
                    continue
                if target_cid in cluster_names:
                    h["cluster_id"] = target_cid
                    h["cluster_name"] = cluster_names[target_cid]
                    by_cluster[target_cid].append(idx)
                    noise_assigned += 1
                else:
                    h["cluster_id"] = -2  # unknown cluster id -> drop

    # Drop headlines marked as -2 (noise with "none" assignment)
    state.headline_data = [h for h in state.headline_data if h.get("cluster_id", -2) != -2]

    # Rebuild by_cluster from the cleaned headline list
    by_cluster = defaultdict(list)
    for i, h in enumerate(state.headline_data):
        cid = h.get("cluster_id", -1)
        if cid >= 0:
            by_cluster[cid].append(i)

    # ----- Step 3: merge near-duplicate clusters by name similarity -----
    merges_done = 0
    if not no_merge and len(cluster_names) >= 2:
        cids = sorted([cid for cid in by_cluster.keys() if cid >= 0])
        names_list = [cluster_names.get(cid, "") for cid in cids]
        if len(cids) >= 2:
            name_embs = embed_texts(names_list)
            M = np.asarray(name_embs, dtype=np.float64)
            norms = np.linalg.norm(M, axis=1, keepdims=True)
            norms = np.where(norms == 0, 1.0, norms)
            N = M / norms
            sim = N @ N.T  # (n_clusters, n_clusters) cosine similarity matrix

            # Collect candidate pairs above threshold (upper triangle only)
            pairs_to_check: List[tuple] = []
            for i in range(len(cids)):
                for j in range(i + 1, len(cids)):
                    if sim[i, j] >= _MERGE_SIM_THRESHOLD:
                        pairs_to_check.append((cids[i], cids[j]))

            for cid_a, cid_b in pairs_to_check:
                # Either may have been merged away already
                if cid_a not in by_cluster or cid_b not in by_cluster:
                    continue
                a_titles = [
                    state.headline_data[i]["title"]
                    for i in by_cluster[cid_a][:5]
                ]
                b_titles = [
                    state.headline_data[i]["title"]
                    for i in by_cluster[cid_b][:5]
                ]
                try:
                    result = call_prompt("merge_clusters", {
                        "a": {
                            "id": str(cid_a),
                            "name": cluster_names[cid_a],
                            "top_headlines": a_titles,
                        },
                        "b": {
                            "id": str(cid_b),
                            "name": cluster_names[cid_b],
                            "top_headlines": b_titles,
                        },
                    }, engine=engine)
                except Exception:
                    continue

                if result.merge:
                    merged_name = result.merged_name or cluster_names[cid_a]
                    # Reassign all of cid_b's headlines to cid_a
                    for idx in by_cluster[cid_b]:
                        state.headline_data[idx]["cluster_id"] = cid_a
                        state.headline_data[idx]["cluster_name"] = merged_name
                    # Update cid_a's headlines to use merged_name too
                    for idx in by_cluster[cid_a]:
                        state.headline_data[idx]["cluster_name"] = merged_name
                    by_cluster[cid_a].extend(by_cluster[cid_b])
                    del by_cluster[cid_b]
                    cluster_names[cid_a] = merged_name
                    if cid_b in cluster_names:
                        del cluster_names[cid_b]
                    merges_done += 1

    # ----- Step 4: MMR top-K per cluster -----
    sections: List[dict] = []
    for cid, indices in sorted(by_cluster.items()):
        if cid < 0:
            continue
        if not indices:
            continue

        embs = [state.headline_data[i].get("embedding") for i in indices]
        has_all_embs = all(e is not None and len(e) > 0 for e in embs)

        if has_all_embs:
            relevance = [state.headline_data[i].get("rating", 0.0) for i in indices]
            chosen_local = mmr_select(embs, relevance, k=k_per_cluster, lambda_=mmr_lambda)
            picks = [indices[j] for j in chosen_local]
        else:
            # Fallback: sort by rating descending
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

    # Rebuild state.clusters (name -> list of headline index strings)
    state.clusters = {}
    for cid, indices in by_cluster.items():
        if cid < 0:
            continue
        name = cluster_names.get(cid, f"Cluster {cid}")
        state.clusters[name] = [str(i) for i in indices]

    state.complete_step(
        "select",
        message=f"{len(sections)} headlines across {len(state.clusters)} sections",
    )
    state.save_checkpoint("select")

    # ----- Step 5: write run artifact -----
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
