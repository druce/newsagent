"""Record a session's rated articles into `published_articles`.

Called at the end of `select` (after rate → cluster → MMR, before draft):
one row per rated article, `selected=1` for the newsletter picks. Only
selected rows are embedded (`title + short_summary`, text-embedding-3-large —
the same text `crossdedupe` embeds for candidates). `published_at` stays NULL
until `send` stamps it, so an unsent session is invisible to `crossdedupe`.

Also used by `send` (fallback for sessions selected before this existed) and
`backfill_published` (which passes the original newsletter timestamp).
"""
from __future__ import annotations

import sqlite3
import sys
from typing import Optional

from lib.db import PublishedArticle
from lib.embeddings import embed_texts
from lib.state import NewsletterAgentState


def record_session_articles(
    state: NewsletterAgentState,
    session_id: str,
    db_path: str,
    published_at: Optional[str] = None,
) -> tuple[int, int]:
    """Upsert every rated article of the session (plus every selected one,
    rated or not). Returns (rows written, of which selected)."""
    selected_items: dict[int, dict] = {}
    for it in state.newsletter_section_data or []:
        idx = it.get("id")
        if isinstance(idx, int) and 0 <= idx < len(state.headline_data):
            selected_items.setdefault(idx, it)

    rows: list[PublishedArticle] = []
    seen: set[str] = set()
    to_embed: list[tuple[PublishedArticle, str]] = []
    for idx, h in enumerate(state.headline_data):
        it = selected_items.get(idx)
        if it is None and h.get("rating") is None:
            continue  # unrated and not picked
        if it is not None:
            url = it.get("link") or h.get("final_url") or h.get("url") or ""
            title = it.get("headline") or h.get("title")
            cat = it.get("cat") or h.get("cluster_name")
            rating = h.get("rating", it.get("rating"))
            site_name = h.get("site_name") or it.get("site_name")
        else:
            url = h.get("final_url") or h.get("url") or ""
            title = h.get("title")
            cat = h.get("cluster_name")
            rating = h.get("rating")
            site_name = h.get("site_name")
        if not url:
            continue
        if url in seen:
            print(f"Warning: published_articles: duplicate url in session "
                  f"{session_id}, keeping the first: {url}", file=sys.stderr)
            continue
        seen.add(url)

        row = PublishedArticle(
            session_id=session_id,
            url=url,
            title=title,
            short_summary=h.get("short_summary"),
            summary=h.get("summary"),
            site_name=site_name,
            cat=cat,
            rating=rating,
            selected=it is not None,
            published_at=published_at if it is not None else None,
        )
        rows.append(row)
        if it is not None:
            to_embed.append((row, ((title or "") + "\n" + (row.short_summary or "")).strip()))

    if to_embed:
        vectors = embed_texts([t for _, t in to_embed])
        for (row, _), vec in zip(to_embed, vectors):
            row.embedding = vec

    with sqlite3.connect(db_path) as conn:
        PublishedArticle.upsert_session(conn, session_id, rows)
    return len(rows), len(to_embed)
