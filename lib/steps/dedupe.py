"""news:dedupe — cosine-similarity near-duplicate removal."""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import List

import click
import numpy as np

from lib.embeddings import embed_texts
from lib.state import NewsletterAgentState


_SIM_THRESHOLD = 0.95


def _cosine_matrix(vectors: List[List[float]]) -> np.ndarray:
    """Pairwise cosine similarity matrix."""
    if not vectors:
        return np.zeros((0, 0))
    M = np.array(vectors)
    norms = np.linalg.norm(M, axis=1, keepdims=True)
    norms = np.where(norms == 0, 1.0, norms)
    N = M / norms
    return N @ N.T


def _drop_near_duplicates(headlines: List[dict], sim: np.ndarray, threshold: float) -> List[int]:
    """Return indices to drop. Keep the longer-text headline of each near-duplicate pair."""
    n = len(headlines)
    drop: set[int] = set()
    for i in range(n):
        if i in drop:
            continue
        for j in range(i + 1, n):
            if j in drop:
                continue
            if sim[i, j] >= threshold:
                li = len(headlines[i].get("summary", ""))
                lj = len(headlines[j].get("summary", ""))
                # Keep the longer; if tied keep i
                drop.add(j if li >= lj else i)
                if i in drop:
                    break
    return sorted(drop)


@click.command()
@click.option("--db", "db_path", default="newsletter_agent.db")
@click.option("--session", "session_id", required=True)
@click.option("--threshold", default=_SIM_THRESHOLD, type=float)
def cli(db_path: str, session_id: str, threshold: float) -> None:
    state = NewsletterAgentState(session_id=session_id, db_path=db_path).load_latest_from_db()
    if state is None:
        raise click.ClickException(f"No state found for session {session_id}")

    candidates = [h for h in state.headline_data if h.get("summary")]
    if not candidates:
        click.echo("Nothing to dedupe (no summaries yet).")
        # NB: dedupe is not a canonical workflow step in WORKFLOW_STEPS.
        # Skip step transitions; just return.
        return

    texts = [(h.get("title", "") + " " + h.get("summary", "")) for h in candidates]
    vectors = embed_texts(texts)

    # Attach embeddings to headlines
    for h, v in zip(candidates, vectors):
        h["embedding"] = v

    sim = _cosine_matrix(vectors)
    drop_indices = _drop_near_duplicates(candidates, sim, threshold)

    drop_urls = {candidates[i]["url"] for i in drop_indices}
    state.headline_data = [h for h in state.headline_data if h["url"] not in drop_urls]

    # Persist via serialize_to_db — dedupe is not a registered workflow step,
    # so we can't call start_step/complete_step. Use a custom checkpoint row.
    state.serialize_to_db("dedupe")

    runs_dir = Path("runs") / session_id
    runs_dir.mkdir(parents=True, exist_ok=True)
    (runs_dir / "dedupe.json").write_text(json.dumps({
        "session_id": session_id,
        "completed_at": datetime.now().isoformat(),
        "total_candidates": len(candidates),
        "dropped": len(drop_indices),
        "kept": len(state.headline_data),
        "threshold": threshold,
    }, indent=2))

    click.echo(f"Dedupe: dropped {len(drop_indices)}/{len(candidates)} near-duplicates "
               f"(threshold={threshold}). {len(state.headline_data)} headlines remain.")


if __name__ == "__main__":  # pragma: no cover
    sys.exit(cli())
