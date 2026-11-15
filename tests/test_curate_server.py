"""Tests for lib/steps/curate.py — curation HTTP server."""
import sqlite3
from contextlib import contextmanager

import httpx

from lib.db import init_db
from lib.steps.curate import start_server

SINCE = "2026-11-14-12-00-00"


def _insert(db, url, selected=1, short_summary="Headline here") -> int:
    init_db(db)
    with sqlite3.connect(db) as conn:
        return conn.execute(
            "INSERT INTO published_articles (session_id, url, short_summary, selected) "
            "VALUES (?, ?, ?, ?)",
            ("2026-11-15-07-00-14", url, short_summary, selected),
        ).lastrowid


def _selected(db, aid) -> int:
    with sqlite3.connect(db) as conn:
        return conn.execute(
            "SELECT selected FROM published_articles WHERE id = ?", (aid,)
        ).fetchone()[0]


@contextmanager
def _server(db):
    server = start_server(db, 0, SINCE)
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()


def test_index_renders_rows(tmp_db):
    _insert(tmp_db, "https://a.com/1", short_summary="Robots learn to fold laundry")
    with _server(tmp_db) as base:
        resp = httpx.get(f"{base}/")
    assert resp.status_code == 200
    assert "Robots learn to fold laundry" in resp.text


def test_bad_page_param_is_clamped(tmp_db):
    _insert(tmp_db, "https://a.com/1")
    with _server(tmp_db) as base:
        assert httpx.get(f"{base}/?page=abc").status_code == 200
        assert httpx.get(f"{base}/?page=999").status_code == 200


def test_toggle_flips_db(tmp_db):
    aid = _insert(tmp_db, "https://a.com/1", selected=1)
    with _server(tmp_db) as base:
        resp = httpx.post(f"{base}/toggle", params={"id": aid})
    assert resp.status_code == 200
    assert resp.json() == {"id": aid, "selected": 0}
    assert _selected(tmp_db, aid) == 0


def test_toggle_unknown_id_404(tmp_db):
    aid = _insert(tmp_db, "https://a.com/1", selected=1)
    with _server(tmp_db) as base:
        resp = httpx.post(f"{base}/toggle", params={"id": 999999})
    assert resp.status_code == 404
    assert _selected(tmp_db, aid) == 1


def test_toggle_bad_id_400(tmp_db):
    _insert(tmp_db, "https://a.com/1")
    with _server(tmp_db) as base:
        assert httpx.post(f"{base}/toggle", params={"id": "x"}).status_code == 400
        assert httpx.post(f"{base}/toggle").status_code == 400


def test_get_toggle_not_allowed(tmp_db):
    aid = _insert(tmp_db, "https://a.com/1", selected=1)
    with _server(tmp_db) as base:
        resp = httpx.get(f"{base}/toggle", params={"id": aid})
    assert resp.status_code in (404, 405)
    assert _selected(tmp_db, aid) == 1
