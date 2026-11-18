"""Bluesky share state on `published_articles` (used by `lib.steps.bsky_share_v2`).

Writes the `bsky_status / bsky_uri / bsky_error / bsky_attempts` columns. A row
is postable while `selected=1 AND bsky_status='none'`; posting flips it to
'posted' or 'failed', so each row is tried at most once per run (no retry).
v1's `bsky_queue` table is not read or written here.
"""
from __future__ import annotations

import sqlite3

_COLS = "id, url, title, short_summary, rating"
_WHERE = "session_id = ? AND bsky_status = 'none' AND selected = 1"
_ORDER = "rating DESC NULLS LAST, id"


def unposted(db_path: str, session_id: str) -> list[dict]:
    """Every postable row of the session, highest rating first."""
    with sqlite3.connect(db_path, timeout=30) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"SELECT {_COLS} FROM published_articles WHERE {_WHERE} ORDER BY {_ORDER}",
            (session_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def next_unposted(db_path: str, session_id: str) -> dict | None:
    """The highest-rated postable row of the session, or None."""
    with sqlite3.connect(db_path, timeout=30) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            f"SELECT {_COLS} FROM published_articles WHERE {_WHERE} "
            f"ORDER BY {_ORDER} LIMIT 1",
            (session_id,),
        ).fetchone()
    return dict(row) if row else None


def mark_posted(db_path: str, row_id: int, uri: str) -> None:
    with sqlite3.connect(db_path, timeout=30) as conn:
        conn.execute(
            "UPDATE published_articles SET bsky_status='posted', bsky_uri=?, "
            "bsky_error=NULL, bsky_attempts=bsky_attempts+1 WHERE id=?",
            (uri, row_id),
        )


def mark_failed(db_path: str, row_id: int, error: str) -> None:
    with sqlite3.connect(db_path, timeout=30) as conn:
        conn.execute(
            "UPDATE published_articles SET bsky_status='failed', bsky_error=?, "
            "bsky_attempts=bsky_attempts+1 WHERE id=?",
            (str(error)[:2000], row_id),
        )
