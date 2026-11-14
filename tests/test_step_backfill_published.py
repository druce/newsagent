"""Tests for lib.steps.backfill_published — rebuild the published_articles
cross-day dedup store from past sessions' `select` checkpoints."""
import json
import sqlite3
from datetime import datetime, timedelta
from unittest.mock import patch

from click.testing import CliRunner

from lib.db import init_db
from lib.state import NewsletterAgentState
from lib.steps.backfill_published import cli as backfill_cli


def _seed_sent_session(db_path: str, sid: str, days_ago: int = 1,
                       with_select: bool = True) -> str:
    """A session that reached send: a `select` checkpoint holding the
    per-article list and a `newsletters` row stamped `days_ago` days back.
    Returns the newsletters.created_at timestamp."""
    init_db(db_path)
    state = NewsletterAgentState(session_id=sid, db_path=db_path)
    state.headline_data = [
        {"title": f"{sid} title 0", "url": f"https://e.com/{sid}/0",
         "short_summary": "Story zero."},
        {"title": f"{sid} title 1", "url": f"https://e.com/{sid}/1",
         "short_summary": "Story one."},
    ]
    state.newsletter_section_data = [
        {"cat": "AI", "headline": f"{sid} title 0", "link": f"https://e.com/{sid}/0", "id": 0},
        {"cat": "AI", "headline": f"{sid} title 1", "link": f"https://e.com/{sid}/1", "id": 1},
    ]
    for step in ("start", "gather", "select"):
        state.complete_step(step)
    if with_select:
        state.save_checkpoint("select")
    # draft's legacy overwrite — the latest checkpoint is useless for backfill
    state.newsletter_section_data = [{"cat": "AI", "section_markdown": "## AI"}]
    state.complete_step("draft")
    state.save_checkpoint("draft")

    created = (datetime.now() - timedelta(days=days_ago)).isoformat()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO newsletters (session_id, title, html, sent_at, created_at) "
            "VALUES (?, ?, ?, ?, ?)", (sid, "T", "<p/>", None, created))
        conn.commit()
    return created


def _rows(db_path: str):
    with sqlite3.connect(db_path) as conn:
        return conn.execute(
            "SELECT session_id, url, title, short_summary, embedding, published_at "
            "FROM published_articles ORDER BY session_id, url").fetchall()


def test_backfill_dry_run_lists_sessions_without_writing(tmp_db):
    _seed_sent_session(tmp_db, "2026-09-10-07-00-00", days_ago=2)
    result = CliRunner().invoke(backfill_cli, ["--db", tmp_db])
    assert result.exit_code == 0, result.output
    assert "DRY-RUN" in result.output
    assert "2026-09-10-07-00-00" in result.output
    assert "would record 2 selected article(s)" in result.output
    assert _rows(tmp_db) == []


def test_backfill_yes_writes_rows_stamped_with_the_newsletter_time(tmp_db):
    created = _seed_sent_session(tmp_db, "2026-09-10-07-00-00", days_ago=2)
    with patch("lib.published.embed_texts", side_effect=lambda t: [[0.1, 0.2] for _ in t]):
        result = CliRunner().invoke(backfill_cli, ["--db", tmp_db, "--yes"])
    assert result.exit_code == 0, result.output
    assert "recorded 2 article(s)" in result.output

    rows = _rows(tmp_db)
    assert [r[1] for r in rows] == [
        "https://e.com/2026-09-10-07-00-00/0", "https://e.com/2026-09-10-07-00-00/1"]
    assert rows[0][0] == "2026-09-10-07-00-00"
    assert rows[0][2] == "2026-09-10-07-00-00 title 0"
    assert rows[0][3] == "Story zero."            # short_summary from headline_data
    assert json.loads(rows[0][4]) == [0.1, 0.2]
    assert rows[0][5] == created                  # not "now": crossdedupe's window


def test_backfill_is_idempotent(tmp_db):
    _seed_sent_session(tmp_db, "2026-09-10-07-00-00", days_ago=2)
    with patch("lib.published.embed_texts", side_effect=lambda t: [[0.1] for _ in t]):
        r1 = CliRunner().invoke(backfill_cli, ["--db", tmp_db, "--yes"])
        r2 = CliRunner().invoke(backfill_cli, ["--db", tmp_db, "--yes"])
    assert r1.exit_code == 0 and r2.exit_code == 0, r1.output + r2.output
    assert "already recorded" in r2.output
    assert len(_rows(tmp_db)) == 2


def test_backfill_respects_the_days_window(tmp_db):
    _seed_sent_session(tmp_db, "2026-08-01-07-00-00", days_ago=40)
    _seed_sent_session(tmp_db, "2026-09-10-07-00-00", days_ago=2)
    with patch("lib.published.embed_texts", side_effect=lambda t: [[0.1] for _ in t]):
        result = CliRunner().invoke(backfill_cli, ["--db", tmp_db, "--yes", "--days", "14"])
    assert result.exit_code == 0, result.output
    assert {r[0] for r in _rows(tmp_db)} == {"2026-09-10-07-00-00"}


def test_backfill_reports_a_missing_select_checkpoint(tmp_db):
    _seed_sent_session(tmp_db, "2026-09-10-07-00-00", days_ago=2, with_select=False)
    with patch("lib.published.embed_texts", side_effect=lambda t: [[0.1] for _ in t]):
        result = CliRunner().invoke(backfill_cli, ["--db", tmp_db, "--yes"])
    assert result.exit_code == 0, result.output
    assert "no select checkpoint" in result.output
    assert _rows(tmp_db) == []


def test_backfill_can_target_one_session(tmp_db):
    _seed_sent_session(tmp_db, "2026-09-09-07-00-00", days_ago=3)
    _seed_sent_session(tmp_db, "2026-09-10-07-00-00", days_ago=2)
    with patch("lib.published.embed_texts", side_effect=lambda t: [[0.1] for _ in t]):
        result = CliRunner().invoke(
            backfill_cli, ["--db", tmp_db, "--yes", "--session", "2026-09-09-07-00-00"])
    assert result.exit_code == 0, result.output
    assert {r[0] for r in _rows(tmp_db)} == {"2026-09-09-07-00-00"}
