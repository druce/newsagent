"""newsagent:dedupe — cosine-similarity near-duplicate removal on full article text.

Embeds the trafilatura-extracted body (truncated) instead of title+summary so
syndicated reprints (e.g. Reuters wire stories republished by multiple sites
with minor edits) cluster together correctly.
"""
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
# text-embedding-3-large supports up to 8192 tokens; ~4 chars/token ⇒ truncate
# bodies to ~24k chars to leave headroom and keep payloads small.
_MAX_TEXT_CHARS = 24_000


def _read_body(path: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _cosine_matrix(vectors: List[List[float]]) -> np.ndarray:
    """Pairwise cosine similarity matrix."""
    if not vectors:
        return np.zeros((0, 0))
    M = np.array(vectors)
    norms = np.linalg.norm(M, axis=1, keepdims=True)
    norms = np.where(norms == 0, 1.0, norms)
    N = M / norms
    return N @ N.T


def _drop_near_duplicates(
    headlines: List[dict], texts: List[str], sim: np.ndarray, threshold: float
) -> List[int]:
    """Return indices to drop. Keep the longer-bodied headline of each pair.

    Tie-breaks on body length first (more complete article wins); falls back
    to summary length then to insertion order (keep earlier).
    """
    n = len(headlines)
    drop: set[int] = set()
    for i in range(n):
        if i in drop:
            continue
        for j in range(i + 1, n):
            if j in drop:
                continue
            if sim[i, j] >= threshold:
                li = len(texts[i])
                lj = len(texts[j])
                if li == lj:
                    li = len(headlines[i].get("summary", ""))
                    lj = len(headlines[j].get("summary", ""))
                # Keep the longer; if tied keep i (earlier wins)
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

    # Candidates need a downloaded body; readable text_path is required.
    candidates: list[dict] = []
    texts: list[str] = []
    for h in state.headline_data:
        path = h.get("text_path")
        if not path:
            continue
        body = _read_body(path)
        if not body:
            continue
        candidates.append(h)
        # Prepend title so very short bodies still get topical signal.
        texts.append((h.get("title", "") + "\n\n" + body)[:_MAX_TEXT_CHARS])

    state.start_step("dedupe")
    state.save_checkpoint("dedupe")

    if not candidates:
        state.complete_step("dedupe", message="nothing to dedupe")
        state.save_checkpoint("dedupe")
        click.echo("Nothing to dedupe (no downloaded article bodies).")
        return

    vectors = embed_texts(texts)

    # Attach embeddings to headlines for downstream cluster/select reuse.
    for h, v in zip(candidates, vectors):
        h["embedding"] = v

    sim = _cosine_matrix(vectors)
    drop_indices = _drop_near_duplicates(candidates, texts, sim, threshold)

    drop_urls = {candidates[i]["url"] for i in drop_indices}
    state.headline_data = [h for h in state.headline_data if h["url"] not in drop_urls]

    state.complete_step(
        "dedupe",
        message=f"dropped {len(drop_indices)}/{len(candidates)} duplicates",
    )
    state.save_checkpoint("dedupe")

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
