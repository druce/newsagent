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


def test_default_window_recomputed_per_request(tmp_db, monkeypatch):
    """With no --since, the window follows the clock instead of freezing at startup."""
    import lib.steps.curate as curate_step
    _insert(tmp_db, "https://a.com/1")
    stamps = iter(["2026-11-14-12-00-00", "2026-11-16-12-00-00"])
    monkeypatch.setattr(curate_step, "window_start", lambda now: next(stamps))
    server = start_server(tmp_db, 0, None)
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        assert "since 2026-11-14-12-00-00" in httpx.get(f"{base}/").text
        second = httpx.get(f"{base}/").text
        assert "since 2026-11-16-12-00-00" in second and "No articles since" in second
    finally:
        server.shutdown()


def test_cli_latest_rejects_since_and_empty_db(tmp_db):
    from click.testing import CliRunner
    from lib.steps.curate import cli
    r = CliRunner().invoke(cli, ["--db", tmp_db, "--latest",
                                 "--since", "2026-11-14-12-00-00"])
    assert r.exit_code == 2 and "mutually exclusive" in r.output
    r = CliRunner().invoke(cli, ["--db", tmp_db, "--latest"])
    assert r.exit_code == 1 and "No sessions" in r.output


# ── CRUD routes ──────────────────────────────────────────────────────────────

def _post(base, path, obj):
    return httpx.post(f"{base}{path}", json=obj)


def _row(db, aid):
    with sqlite3.connect(db) as conn:
        return conn.execute(
            "SELECT url, short_summary, site_name FROM published_articles WHERE id = ?",
            (aid,)).fetchone()


def test_update_route(tmp_db):
    aid = _insert(tmp_db, "https://a.com/1")
    with _server(tmp_db) as base:
        resp = _post(base, "/update", {"id": aid, "url": "https://b.com/2",
                                       "short_summary": "Fixed headline",
                                       "site_name": "b.com"})
    assert resp.status_code == 200
    j = resp.json()
    assert j["row"]["url"] == "https://b.com/2" and "Fixed headline" in j["html"]
    assert _row(tmp_db, aid) == ("https://b.com/2", "Fixed headline", "b.com")


def test_update_route_duplicate_409(tmp_db):
    _insert(tmp_db, "https://a.com/1")
    b = _insert(tmp_db, "https://a.com/2")
    with _server(tmp_db) as base:
        resp = _post(base, "/update", {"id": b, "url": "https://a.com/1",
                                       "short_summary": "X", "site_name": ""})
    assert resp.status_code == 409 and "already exists" in resp.json()["error"]
    assert _row(tmp_db, b)[0] == "https://a.com/2"


def test_update_route_bad_json_and_id_400(tmp_db):
    _insert(tmp_db, "https://a.com/1")
    with _server(tmp_db) as base:
        assert httpx.post(f"{base}/update", content=b"{nope",
                          headers={"Content-Type": "application/json"}).status_code == 400
        assert _post(base, "/update", {"id": "x", "url": "https://a.com/9",
                                       "short_summary": "H"}).status_code == 400
        assert _post(base, "/update", {"url": "https://a.com/9",
                                       "short_summary": "H"}).status_code == 400


def test_delete_route(tmp_db):
    aid = _insert(tmp_db, "https://a.com/1", selected=1)
    with _server(tmp_db) as base:
        resp = _post(base, "/delete", {"id": aid})
        assert resp.status_code == 200 and resp.json() == {"id": aid, "selected": 1}
        assert _post(base, "/delete", {"id": aid}).status_code == 404
    assert _row(tmp_db, aid) is None


def test_create_route(tmp_db):
    _insert(tmp_db, "https://a.com/1")
    with _server(tmp_db) as base:
        resp = _post(base, "/create", {"url": "https://www.added.com/s",
                                       "short_summary": "Hand-added story",
                                       "site_name": ""})
        assert resp.status_code == 200
        assert resp.json()["row"]["site_name"] == "added.com"
        page = httpx.get(f"{base}/").text
    assert "<h2>Other</h2>" in page and "Hand-added story" in page


def test_unknown_post_path_404(tmp_db):
    init_db(tmp_db)
    with _server(tmp_db) as base:
        assert _post(base, "/nope", {}).status_code == 404


def test_cross_origin_post_rejected(tmp_db):
    aid = _insert(tmp_db, "https://a.com/1", selected=1)
    with _server(tmp_db) as base:
        r = httpx.post(f"{base}/delete", content=f'{{"id": {aid}}}',
                       headers={"Content-Type": "text/plain"})
        assert r.status_code == 415
        r = httpx.post(f"{base}/delete", json={"id": aid},
                       headers={"Origin": "https://evil.example"})
        assert r.status_code == 403
        r = httpx.post(f"{base}/toggle", params={"id": aid},
                       headers={"Origin": "https://evil.example"})
        assert r.status_code == 403
        assert _post(base, "/delete", {"id": aid}).status_code == 200


def test_same_origin_header_allowed(tmp_db):
    aid = _insert(tmp_db, "https://a.com/1")
    with _server(tmp_db) as base:
        r = httpx.post(f"{base}/delete", json={"id": aid}, headers={"Origin": base})
    assert r.status_code == 200


def test_huge_id_400(tmp_db):
    init_db(tmp_db)
    with _server(tmp_db) as base:
        assert _post(base, "/delete", {"id": 99999999999999999999}).status_code == 400
