"""Curation over `published_articles`: window query, `selected` toggle, page HTML.

Pure functions over a db path, wrapped by the `lib.steps.curate` web server.
Each call opens its own connection (the server is threaded).
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

PER_PAGE = 50

_COLS = ("id", "session_id", "url", "title", "short_summary", "summary",
         "site_name", "cat", "rating", "selected")

# Session ids are start timestamps (YYYY-MM-DD-HH-MM-SS), so a string compare
# gives the time window. The GLOB keeps non-date ids ("test-…" sorts after
# "2026-…") out of it.
_WHERE = "session_id >= ? AND session_id GLOB '[0-9][0-9][0-9][0-9]-*'"

_ORDER = (
    "CASE WHEN cat IS NULL OR cat = '' THEN 2 WHEN cat = 'Other' THEN 1 ELSE 0 END, "
    "cat, rating DESC NULLS LAST, id"
)


def window_start(now: datetime) -> str:
    """Session-id lower bound: noon of the calendar day before `now`."""
    return f"{(now.date() - timedelta(days=1)):%Y-%m-%d}-12-00-00"


def fetch_page(db_path: str, since: str, page: int, per_page: int = PER_PAGE) -> dict:
    """One page of in-window rows, plus paging and selected counts."""
    with sqlite3.connect(db_path) as conn:
        total, selected = conn.execute(
            f"SELECT COUNT(*), COALESCE(SUM(selected), 0) "
            f"FROM published_articles WHERE {_WHERE}", (since,)
        ).fetchone()
        pages = max(1, -(-total // per_page))
        page = min(max(1, page), pages)
        rows = conn.execute(
            f"SELECT {', '.join(_COLS)} FROM published_articles WHERE {_WHERE} "
            f"ORDER BY {_ORDER} LIMIT ? OFFSET ?",
            (since, per_page, (page - 1) * per_page),
        ).fetchall()
    return {
        "rows": [dict(zip(_COLS, r)) for r in rows],
        "page": page, "pages": pages, "total": total, "selected": selected,
    }


def toggle_selected(db_path: str, article_id: int) -> int | None:
    """Flip `selected` for one row; return the new value, or None if no such id."""
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(
            "UPDATE published_articles SET selected = 1 - selected WHERE id = ?",
            (article_id,),
        )
        if cur.rowcount == 0:
            return None
        return conn.execute(
            "SELECT selected FROM published_articles WHERE id = ?", (article_id,)
        ).fetchone()[0]
