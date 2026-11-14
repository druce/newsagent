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


def _target_key(h: dict) -> str:
    """The article a headline actually points at: post-redirect final_url, else
    the downloaded-body path (sha256 of final_url), else the original url."""
    return h.get("final_url") or h.get("text_path") or h.get("url") or ""


def _collapse_same_target(headlines: List[dict]) -> tuple[List[dict], List[dict]]:
    """Collapse headlines whose different original urls resolve to the same
    article (aggregator / feed redirects, t.co, Google News …).

    Keeps one per target key — the direct link (url == final_url) if present,
    else the earliest in gather order — and preserves the original order.
    Returns (kept, dropped).
    """
    chosen: dict[str, int] = {}
    for i, h in enumerate(headlines):
        key = _target_key(h)
        if not key:
            continue
        if key not in chosen:
            chosen[key] = i
            continue
        cur = headlines[chosen[key]]
        cur_direct = bool(cur.get("final_url")) and cur.get("url") == cur.get("final_url")
        new_direct = bool(h.get("final_url")) and h.get("url") == h.get("final_url")
        if new_direct and not cur_direct:
            chosen[key] = i
    keep_idx = set(chosen.values())
    kept, dropped = [], []
    for i, h in enumerate(headlines):
        if not _target_key(h) or i in keep_idx:
            kept.append(h)
        else:
            dropped.append(h)
    return kept, dropped


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

    # Several original urls can resolve to the same article; keep one of each
    # before spending embedding calls (and so downstream has one row per url).
    state.headline_data, collapsed = _collapse_same_target(state.headline_data)
    for h in collapsed:
        click.echo(f"  collapsed same-url: {h.get('url')} -> {_target_key(h)}")

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
        msg = "nothing to dedupe"
        if collapsed:
            msg = f"collapsed {len(collapsed)} same-url; {msg}"
        state.complete_step("dedupe", message=msg)
        state.save_checkpoint("dedupe")
        click.echo("Nothing to dedupe (no downloaded article bodies).")
        return

    vectors = embed_texts(texts)

    # Attach embeddings to headlines for downstream cluster/select reuse.
    for h, v in zip(candidates, vectors):
        h["embedding"] = v

    sim = _cosine_matrix(vectors)
    drop_indices = _drop_near_duplicates(candidates, texts, sim, threshold)

    # Drop by identity, not by original url: distinct headlines can share an
    # original (aggregator) url while resolving to different articles.
    drop_ids = {id(candidates[i]) for i in drop_indices}
    state.headline_data = [h for h in state.headline_data if id(h) not in drop_ids]

    state.complete_step(
        "dedupe",
        message=(f"collapsed {len(collapsed)} same-url, "
                 f"dropped {len(drop_indices)}/{len(candidates)} duplicates"),
    )
    state.save_checkpoint("dedupe")

    runs_dir = Path("runs") / session_id
    runs_dir.mkdir(parents=True, exist_ok=True)
    (runs_dir / "dedupe.json").write_text(json.dumps({
        "session_id": session_id,
        "completed_at": datetime.now().isoformat(),
        "collapsed_same_url": len(collapsed),
        "total_candidates": len(candidates),
        "dropped": len(drop_indices),
        "kept": len(state.headline_data),
        "threshold": threshold,
    }, indent=2))

    click.echo(f"Dedupe: dropped {len(drop_indices)}/{len(candidates)} near-duplicates "
               f"(threshold={threshold}). {len(state.headline_data)} headlines remain.")


if __name__ == "__main__":  # pragma: no cover
    sys.exit(cli())
