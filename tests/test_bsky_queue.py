"""Tests for lib/bsky_queue/queue.py — SQLite share queue helpers."""
import sqlite3

from lib.bsky_queue.queue import enqueue, mark_failed, mark_posted, next_pending
from lib.db import init_db


def test_enqueue_inserts_pending_row(tmp_db):
    init_db(tmp_db)
    assert enqueue(tmp_db, "https://e.com/1", "Title 1") is True
    row = next_pending(tmp_db)
    assert row is not None
    assert row["url"] == "https://e.com/1"
    assert row["title"] == "Title 1"
    assert row["status"] == "pending"


def test_enqueue_is_permanently_idempotent_on_url(tmp_db):
    init_db(tmp_db)
    assert enqueue(tmp_db, "https://e.com/1", "Title 1") is True
    # Second enqueue of the same url is a no-op while pending...
    assert enqueue(tmp_db, "https://e.com/1", "Different title") is False
    # ...and stays a no-op after it's been posted...
    row = next_pending(tmp_db)
    mark_posted(tmp_db, row["id"], "at://post/1")
    assert enqueue(tmp_db, "https://e.com/1", "Title 1") is False
    # ...and also after a failure.
    enqueue(tmp_db, "https://e.com/2", "Title 2")
    row2 = next_pending(tmp_db)
    mark_failed(tmp_db, row2["id"], "boom")
    assert enqueue(tmp_db, "https://e.com/2", "Title 2") is False
    with sqlite3.connect(tmp_db) as conn:
        n = conn.execute("SELECT COUNT(*) FROM bsky_queue").fetchone()[0]
    assert n == 2


def test_next_pending_is_fifo_and_skips_non_pending(tmp_db):
    init_db(tmp_db)
    enqueue(tmp_db, "https://e.com/1", "Title 1")
    enqueue(tmp_db, "https://e.com/2", "Title 2")
    first = next_pending(tmp_db)
    assert first["url"] == "https://e.com/1"
    mark_posted(tmp_db, first["id"], "at://post/1")
    second = next_pending(tmp_db)
    assert second["url"] == "https://e.com/2"


def test_next_pending_returns_none_when_empty(tmp_db):
    init_db(tmp_db)
    assert next_pending(tmp_db) is None


def test_mark_posted_and_failed_transitions(tmp_db):
    init_db(tmp_db)
    enqueue(tmp_db, "https://e.com/1", "Title 1")
    row = next_pending(tmp_db)
    mark_posted(tmp_db, row["id"], "at://post/abc")
    with sqlite3.connect(tmp_db) as conn:
        conn.row_factory = sqlite3.Row
        r = conn.execute("SELECT * FROM bsky_queue WHERE id=?", (row["id"],)).fetchone()
    assert r["status"] == "posted"
    assert r["post_uri"] == "at://post/abc"
    assert r["attempts"] == 1

    enqueue(tmp_db, "https://e.com/2", "Title 2")
    row2 = next_pending(tmp_db)
    mark_failed(tmp_db, row2["id"], "network exploded")
    with sqlite3.connect(tmp_db) as conn:
        conn.row_factory = sqlite3.Row
        r2 = conn.execute("SELECT * FROM bsky_queue WHERE id=?", (row2["id"],)).fetchone()
    assert r2["status"] == "failed"
    assert "network exploded" in r2["error"]
    assert r2["attempts"] == 1
