"""Tests for lib/steps/bsky_share_v2.py — timed posting from published_articles."""
import sqlite3

import httpx
import pytest
from click.testing import CliRunner

import lib.steps.bsky_share_v2 as v2
from lib.db import init_db

SID = "2026-11-15-18-00-07"
OLD = "2026-11-15-07-00-14"
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


def _row(db, row_id):
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        return dict(conn.execute(
            "SELECT * FROM published_articles WHERE id=?", (row_id,)).fetchone())


@pytest.fixture
def posts(monkeypatch):
    """Capture post_item calls; returns the list of items posted."""
    calls = []

    def fake(session, item, image_dir):
        calls.append(item)
        return f"at://x/{len(calls)}"
    monkeypatch.setattr(v2, "post_item", fake)
    return calls


@pytest.fixture
def cli_env(monkeypatch):
    """Env + fake login + no-op sleep for CLI tests; returns call records."""
    rec = {"logins": 0, "sleeps": []}
    monkeypatch.setenv("BSKY_USERNAME", "u")
    monkeypatch.setenv("BSKY_SECRET", "s")

    def login(user, secret):
        rec["logins"] += 1
        return {"accessJwt": "j", "did": "d"}
    monkeypatch.setattr(v2, "bsky_login", login)
    monkeypatch.setattr(v2.time, "sleep", lambda s: rec["sleeps"].append(s))
    return rec


def test_post_next_uses_short_summary_then_title(tmp_db, posts):
    a = _insert(tmp_db, rating=2.0, short_summary="S")
    b = _insert(tmp_db, rating=1.0, short_summary=None, title="T")
    s = {}
    assert v2.post_next(tmp_db, SID, s)["ok"] is True
    assert v2.post_next(tmp_db, SID, s)["ok"] is True
    assert v2.post_next(tmp_db, SID, s) is None
    assert [p["title"] for p in posts] == ["S", "T"]
    assert _row(tmp_db, a)["bsky_status"] == _row(tmp_db, b)["bsky_status"] == "posted"
    assert _row(tmp_db, a)["bsky_uri"] == "at://x/1"


def test_post_next_marks_failed_and_moves_on(tmp_db, monkeypatch):
    bad = _insert(tmp_db, rating=2.0, url="https://bad.com/x")
    good = _insert(tmp_db, rating=1.0)

    def maybe(session, item, image_dir):
        if "bad.com" in item["url"]:
            raise RuntimeError("kaboom")
        return "at://ok"
    monkeypatch.setattr(v2, "post_item", maybe)
    assert v2.post_next(tmp_db, SID, {})["ok"] is False
    assert v2.post_next(tmp_db, SID, {})["ok"] is True
    assert v2.post_next(tmp_db, SID, {}) is None
    r = _row(tmp_db, bad)
    assert r["bsky_status"] == "failed" and "kaboom" in r["bsky_error"]
    assert _row(tmp_db, good)["bsky_status"] == "posted"


def test_post_next_relogins_once_on_auth_error(tmp_db, monkeypatch):
    rid = _insert(tmp_db)
    auth_exc = httpx.HTTPStatusError(
        "expired", request=httpx.Request("POST", "http://x"),
        response=httpx.Response(401))
    state = {"calls": 0, "relogins": 0}

    def flaky(session, item, image_dir):
        state["calls"] += 1
        if state["calls"] == 1:
            raise auth_exc
        return "at://ok"

    def relogin():
        state["relogins"] += 1
        return {"accessJwt": "new"}
    monkeypatch.setattr(v2, "post_item", flaky)
    session = {"accessJwt": "old"}
    assert v2.post_next(tmp_db, SID, session, relogin=relogin)["ok"] is True
    assert state["relogins"] == 1 and session["accessJwt"] == "new"
    r = _row(tmp_db, rid)
    assert (r["bsky_status"], r["bsky_uri"]) == ("posted", "at://ok")


def test_cli_posts_all_then_exits_sleeping_between(tmp_db, posts, cli_env):
    for rating in (1.0, 3.0, 2.0):
        _insert(tmp_db, rating=rating, short_summary=f"r{rating}")
    r = CliRunner().invoke(v2.cli, ["--db", tmp_db])
    assert r.exit_code == 0, r.output
    assert [p["title"] for p in posts] == ["r3.0", "r2.0", "r1.0"]
    assert cli_env["sleeps"] == [180, 180]
    assert "0 left" in r.output


def test_cli_defaults_to_latest_session_and_session_overrides(tmp_db, posts, cli_env):
    _insert(tmp_db, session_id=SID, short_summary="new")
    _insert(tmp_db, session_id=OLD, short_summary="old")
    assert CliRunner().invoke(v2.cli, ["--db", tmp_db]).exit_code == 0
    assert [p["title"] for p in posts] == ["new"]
    assert CliRunner().invoke(v2.cli, ["--db", tmp_db, "--session", OLD]).exit_code == 0
    assert [p["title"] for p in posts] == ["new", "old"]


def test_cli_empty_session_exits_without_login(tmp_db, posts, cli_env):
    _insert(tmp_db)
    r = CliRunner().invoke(v2.cli, ["--db", tmp_db, "--session", "2099-01-01-00-00-00"])
    assert r.exit_code == 0 and "Nothing to post" in r.output
    assert cli_env["logins"] == 0 and posts == []


def test_cli_dry_run_lists_without_login_or_posting(tmp_db, posts, cli_env):
    a = _insert(tmp_db, rating=1.0, short_summary="low")
    _insert(tmp_db, rating=5.0, short_summary="high")
    r = CliRunner().invoke(v2.cli, ["--db", tmp_db, "--dry-run"])
    assert r.exit_code == 0
    assert r.output.index("high") < r.output.index("low")
    assert cli_env["logins"] == 0 and posts == []
    assert _row(tmp_db, a)["bsky_status"] == "none"


def test_cli_requires_credentials(tmp_db, posts, monkeypatch):
    _insert(tmp_db)
    monkeypatch.delenv("BSKY_USERNAME", raising=False)
    monkeypatch.delenv("BSKY_SECRET", raising=False)
    r = CliRunner().invoke(v2.cli, ["--db", tmp_db])
    assert r.exit_code == 1 and "BSKY_USERNAME" in r.output


def _flaky_mark(monkeypatch, exc):
    """Make v2.mark_posted raise `exc` on its first call, then behave normally."""
    real = v2.mark_posted
    state = {"calls": 0}

    def wrapped(db, rid, uri):
        state["calls"] += 1
        if state["calls"] == 1:
            raise exc
        real(db, rid, uri)
    monkeypatch.setattr(v2, "mark_posted", wrapped)
    monkeypatch.setattr(v2.time, "sleep", lambda s: None)
    return state


def test_post_next_retries_mark_posted_on_db_lock(tmp_db, posts, monkeypatch):
    rid = _insert(tmp_db)
    state = _flaky_mark(monkeypatch, sqlite3.OperationalError("database is locked"))
    assert v2.post_next(tmp_db, SID, {})["ok"] is True
    assert state["calls"] == 2 and len(posts) == 1
    assert _row(tmp_db, rid)["bsky_status"] == "posted"


def test_ctrl_c_while_recording_still_marks_posted(tmp_db, posts, monkeypatch):
    rid = _insert(tmp_db)
    _flaky_mark(monkeypatch, KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt):
        v2.post_next(tmp_db, SID, {})
    assert _row(tmp_db, rid)["bsky_status"] == "posted"  # no double post on re-run
    assert v2.post_next(tmp_db, SID, {}) is None


def test_cli_refuses_second_instance(tmp_db, posts, cli_env):
    import fcntl
    _insert(tmp_db)
    with open(v2.lock_path(tmp_db), "w") as held:
        held.write("12345")
        held.flush()
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        r = CliRunner().invoke(v2.cli, ["--db", tmp_db])
    assert r.exit_code == 1 and "already running" in r.output and "12345" in r.output
    assert posts == [] and cli_env["logins"] == 0


def test_cli_lock_released_after_run_and_dry_run_ignores_it(tmp_db, posts, cli_env):
    import fcntl
    _insert(tmp_db)
    assert CliRunner().invoke(v2.cli, ["--db", tmp_db]).exit_code == 0
    with open(v2.lock_path(tmp_db), "w") as f:  # free again after a normal exit
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert CliRunner().invoke(v2.cli, ["--db", tmp_db, "--dry-run"]).exit_code == 0


def test_no_content_short_summary_falls_back_to_title(tmp_db, posts):
    _insert(tmp_db, short_summary="no content", title="Real Headline")
    v2.post_next(tmp_db, SID, {})
    assert [p["title"] for p in posts] == ["Real Headline"]
