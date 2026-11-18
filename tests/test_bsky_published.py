"""Tests for lib/bsky_queue/published.py — bsky_* helpers over published_articles."""
import sqlite3

from lib.bsky_queue.published import mark_failed, mark_posted, next_unposted, unposted
from lib.db import init_db

SID = "2026-11-15-18-00-07"
_n = 0


def _insert(db, **cols) -> int:
    global _n
    init_db(db)
    _n += 1
    row = {"session_id": SID, "url": f"https://ex.com/{_n}", "title": f"T{_n}",
           "short_summary": f"S{_n}", "selected": 1, "rating": 1.0}
    row.update(cols)
    keys = ", ".join(row)
    qs = ", ".join("?" for _ in row)
    with sqlite3.connect(db) as conn:
        return conn.execute(
            f"INSERT INTO published_articles ({keys}) VALUES ({qs})",
            tuple(row.values()),
        ).lastrowid


def _bsky(db, row_id):
    with sqlite3.connect(db) as conn:
        return conn.execute(
            "SELECT bsky_status, bsky_uri, bsky_error, bsky_attempts "
            "FROM published_articles WHERE id=?", (row_id,)).fetchone()


def test_next_unposted_picks_highest_rated_selected_none(tmp_db):
    _insert(tmp_db, rating=3.0)
    best = _insert(tmp_db, rating=9.0)
    _insert(tmp_db, rating=99.0, selected=0)
    _insert(tmp_db, rating=99.0, session_id="2026-11-15-07-00-14")
    _insert(tmp_db, rating=99.0, bsky_status="posted")
    _insert(tmp_db, rating=99.0, bsky_status="failed")
    row = next_unposted(tmp_db, SID)
    assert row["id"] == best
    assert set(row) >= {"id", "url", "title", "short_summary", "rating"}


def test_null_rating_sorts_last(tmp_db):
    _insert(tmp_db, rating=None)
    rated = _insert(tmp_db, rating=1.0)
    assert next_unposted(tmp_db, SID)["id"] == rated


def test_next_unposted_none_when_exhausted(tmp_db):
    init_db(tmp_db)
    assert next_unposted(tmp_db, SID) is None
    assert unposted(tmp_db, SID) == []


def test_unposted_lists_in_rating_order(tmp_db):
    a = _insert(tmp_db, rating=1.0)
    b = _insert(tmp_db, rating=5.0)
    assert [r["id"] for r in unposted(tmp_db, SID)] == [b, a]


def test_mark_posted_and_failed_update_row_and_drop_it_from_queue(tmp_db):
    a = _insert(tmp_db, rating=2.0)
    b = _insert(tmp_db, rating=1.0)
    mark_posted(tmp_db, a, "at://x/1")
    mark_failed(tmp_db, b, "boom" * 1000)
    assert _bsky(tmp_db, a) == ("posted", "at://x/1", None, 1)
    status, uri, err, attempts = _bsky(tmp_db, b)
    assert (status, uri, len(err), attempts) == ("failed", None, 2000, 1)
    assert unposted(tmp_db, SID) == []
