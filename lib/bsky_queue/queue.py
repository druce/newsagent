"""SQLite-backed queue for share-to-Bluesky.

Small pure-SQLite helpers over the `bsky_queue` table (declared in lib/db.py).
Dedup is permanent: a URL is queued at most once, ever — `url` is UNIQUE and
`enqueue` is a no-op if any row already exists for that URL (any status).
"""
from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Optional


def _now() -> str:
    return datetime.now().isoformat()


def enqueue(db_path: str, url: str, title: str) -> bool:
    """Insert a pending row for (url, title).

    Returns True if a new row was inserted, False if the URL was already in the
    queue (in any status) — permanent dedup via the UNIQUE(url) constraint.
    """
    now = _now()
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(
            """
            INSERT INTO bsky_queue (url, title, status, created_at, updated_at)
            VALUES (?, ?, 'pending', ?, ?)
            ON CONFLICT(url) DO NOTHING
            """,
            (url, title, now, now),
        )
        conn.commit()
        return cur.rowcount > 0


def next_pending(db_path: str) -> Optional[dict]:
    """Return the oldest pending row as a dict, or None if the queue is empty."""
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM bsky_queue WHERE status='pending' "
            "ORDER BY id ASC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None


def pending_count(db_path: str) -> int:
    """Return how many rows are still pending."""
    with sqlite3.connect(db_path) as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM bsky_queue WHERE status='pending'"
        ).fetchone()[0]


def mark_posted(db_path: str, item_id: int, post_uri: str) -> None:
    """Flip a row to posted, record its at:// uri, bump attempts."""
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE bsky_queue SET status='posted', post_uri=?, error=NULL, "
            "attempts=attempts+1, updated_at=? WHERE id=?",
            (post_uri, _now(), item_id),
        )
        conn.commit()


def mark_failed(db_path: str, item_id: int, error: str) -> None:
    """Flip a row to failed, record the error, bump attempts."""
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE bsky_queue SET status='failed', error=?, "
            "attempts=attempts+1, updated_at=? WHERE id=?",
            (str(error)[:2000], _now(), item_id),
        )
        conn.commit()
