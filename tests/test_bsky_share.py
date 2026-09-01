"""Tests for lib/steps/bsky_share.py — enqueue HTTP server + worker."""
import httpx

import lib.steps.bsky_share as share
from lib.bsky_queue.queue import enqueue, next_pending
from lib.db import init_db


def test_enqueue_endpoint_decodes_query_and_inserts(tmp_db):
    init_db(tmp_db)
    server = share.start_server(tmp_db, 0)  # ephemeral port
    try:
        port = server.server_address[1]
        resp = httpx.get(
            f"http://127.0.0.1:{port}/enqueue",
            params={"u": "https://news.com/a story?x=1", "t": "My Title & More"},
        )
        assert resp.status_code == 200
        assert "Queued" in resp.text
        assert resp.headers["access-control-allow-origin"] == "*"
    finally:
        server.shutdown()

    row = next_pending(tmp_db)
    assert row["url"] == "https://news.com/a story?x=1"
    assert row["title"] == "My Title & More"


def test_enqueue_endpoint_reports_already_queued(tmp_db):
    init_db(tmp_db)
    enqueue(tmp_db, "https://news.com/dup", "Dup")
    server = share.start_server(tmp_db, 0)
    try:
        port = server.server_address[1]
        resp = httpx.get(f"http://127.0.0.1:{port}/enqueue",
                         params={"u": "https://news.com/dup", "t": "Dup"})
        assert "Already queued" in resp.text
    finally:
        server.shutdown()


def test_unknown_path_returns_404_with_cors(tmp_db):
    """Errors carry CORS too — the file:// page must be able to READ the
    failure, otherwise it cannot tell 404 from 'daemon down'."""
    init_db(tmp_db)
    server = share.start_server(tmp_db, 0)
    try:
        port = server.server_address[1]
        resp = httpx.get(f"http://127.0.0.1:{port}/nope")
        assert resp.status_code == 404
        assert resp.headers["access-control-allow-origin"] == "*"
    finally:
        server.shutdown()


def test_options_preflight_returns_204_with_cors_and_pna(tmp_db):
    """Chrome's Private Network Access preflight from a file:// page must be
    answered. Stock http.server replies 501 Unsupported method, which blocks
    the fetch and is the silent-failure bug."""
    init_db(tmp_db)
    server = share.start_server(tmp_db, 0)
    try:
        port = server.server_address[1]
        resp = httpx.request(
            "OPTIONS",
            f"http://127.0.0.1:{port}/enqueue",
            headers={
                "Origin": "null",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Private-Network": "true",
            },
        )
        assert resp.status_code == 204
        assert resp.headers["access-control-allow-origin"] == "*"
        assert "GET" in resp.headers["access-control-allow-methods"]
        assert resp.headers["access-control-allow-headers"] == "*"
        assert resp.headers["access-control-allow-private-network"] == "true"
    finally:
        server.shutdown()


def test_enqueue_without_u_is_a_readonly_health_probe(tmp_db):
    """The rendered pages probe the daemon with a `u`-less /enqueue: 200 + CORS
    and NOTHING written. Chosen over a new /health path because it is also what
    an already-running pre-upgrade daemon answers, so the banner cannot lie.

    Do NOT 'tighten' this into a 400 — that would make the banner report a
    healthy daemon as down, which is the exact bug this fix removes."""
    init_db(tmp_db)
    server = share.start_server(tmp_db, 0)
    try:
        port = server.server_address[1]
        resp = httpx.get(f"http://127.0.0.1:{port}/enqueue")
        assert resp.status_code == 200
        assert resp.headers["access-control-allow-origin"] == "*"
    finally:
        server.shutdown()

    assert next_pending(tmp_db) is None  # probe wrote nothing


def test_health_endpoint_returns_json_ok(tmp_db):
    init_db(tmp_db)
    server = share.start_server(tmp_db, 0)
    try:
        port = server.server_address[1]
        resp = httpx.get(f"http://127.0.0.1:{port}/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
        assert resp.headers["access-control-allow-origin"] == "*"
    finally:
        server.shutdown()


def test_enqueue_logs_one_line_per_click(tmp_db, monkeypatch):
    """Server-side visibility: a real enqueue prints one line, a duplicate says
    so, and the writeless probe prints nothing (it fires on every tab focus)."""
    init_db(tmp_db)
    lines: list[str] = []
    monkeypatch.setattr(share.click, "echo", lambda msg, *a, **k: lines.append(str(msg)))

    server = share.start_server(tmp_db, 0)
    try:
        port = server.server_address[1]
        base = f"http://127.0.0.1:{port}/enqueue"
        httpx.get(base, params={"u": "https://news.com/x", "t": "Title X"})
        httpx.get(base, params={"u": "https://news.com/x", "t": "Title X"})
        httpx.get(base)  # probe — must stay silent
    finally:
        server.shutdown()

    assert any('Queued: "Title X"' in ln for ln in lines)
    assert any('Dup: "Title X"' in ln for ln in lines)
    assert len(lines) == 2


def test_process_one_posts_pending_item(tmp_db, monkeypatch):
    init_db(tmp_db)
    enqueue(tmp_db, "https://news.com/a", "Headline A")

    monkeypatch.setattr(share, "post_item",
                        lambda session, item, image_dir: "at://x/post/1")
    did = share.process_one(tmp_db, {"accessJwt": "t", "did": "d"}, image_dir="x")
    assert did is True
    assert next_pending(tmp_db) is None  # nothing left pending


def test_process_one_returns_false_on_empty_queue(tmp_db):
    init_db(tmp_db)
    assert share.process_one(tmp_db, {"accessJwt": "t"}, image_dir="x") is False


def test_process_one_marks_failed_on_error(tmp_db, monkeypatch):
    init_db(tmp_db)
    enqueue(tmp_db, "https://news.com/a", "Headline A")

    def boom(session, item, image_dir):
        raise RuntimeError("kaboom")
    monkeypatch.setattr(share, "post_item", boom)

    assert share.process_one(tmp_db, {"accessJwt": "t"}, image_dir="x") is True
    import sqlite3
    with sqlite3.connect(tmp_db) as conn:
        conn.row_factory = sqlite3.Row
        r = conn.execute("SELECT * FROM bsky_queue").fetchone()
    assert r["status"] == "failed"
    assert "kaboom" in r["error"]


def test_process_one_relogins_on_auth_error_then_succeeds(tmp_db, monkeypatch):
    init_db(tmp_db)
    enqueue(tmp_db, "https://news.com/a", "Headline A")

    auth_exc = httpx.HTTPStatusError(
        "expired", request=httpx.Request("POST", "http://x"),
        response=httpx.Response(400),
    )
    state = {"calls": 0}

    def flaky(session, item, image_dir):
        state["calls"] += 1
        if state["calls"] == 1:
            raise auth_exc
        return "at://x/post/9"
    monkeypatch.setattr(share, "post_item", flaky)

    session = {"accessJwt": "old", "did": "d"}
    did = share.process_one(tmp_db, session, image_dir="x",
                            relogin=lambda: {"accessJwt": "new"})
    assert did is True
    assert session["accessJwt"] == "new"  # session refreshed in place
    import sqlite3
    with sqlite3.connect(tmp_db) as conn:
        conn.row_factory = sqlite3.Row
        r = conn.execute("SELECT * FROM bsky_queue").fetchone()
    assert r["status"] == "posted"
    assert r["post_uri"] == "at://x/post/9"
