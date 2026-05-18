"""Tests for lib.steps.diff — compare two sessions."""
import sqlite3
from click.testing import CliRunner
from lib.db import init_db, AgentState
from lib.state import NewsletterAgentState


def _make_session(tmp_db: str, sid: str, section_data: list) -> None:
    """Create a minimal session with the given newsletter_section_data."""
    init_db(tmp_db)
    state = NewsletterAgentState(session_id=sid, db_path=tmp_db)
    state.complete_step("init")
    state.complete_step("gather")
    state.newsletter_section_data = section_data
    state.newsletter_title = f"Newsletter for {sid}"
    state.save_checkpoint("gather")


def test_diff_with_overlap(tmp_db):
    """Two sessions with overlapping URLs → output includes overlap %."""
    shared_url = "https://example.com/shared-article"
    _make_session(tmp_db, "sid-A", [
        {"link": shared_url, "headline": "Shared Article"},
        {"link": "https://example.com/only-in-A", "headline": "Only in A"},
    ])
    _make_session(tmp_db, "sid-B", [
        {"link": shared_url, "headline": "Shared Article"},
        {"link": "https://example.com/only-in-B", "headline": "Only in B"},
    ])

    from lib.steps.diff import cli as diff_cli
    runner = CliRunner()
    result = runner.invoke(diff_cli, ["sid-A", "sid-B", "--db", tmp_db])
    assert result.exit_code == 0, result.output
    # Should include overlap percentage (Jaccard = 1/3 ≈ 33%)
    assert "%" in result.output or "overlap" in result.output.lower()
    # Should mention both sessions
    assert "sid-A" in result.output
    assert "sid-B" in result.output


def test_diff_missing_session(tmp_db):
    """If one or both sessions are missing → exit code != 0."""
    init_db(tmp_db)
    _make_session(tmp_db, "real-session", [])

    from lib.steps.diff import cli as diff_cli
    runner = CliRunner()
    result = runner.invoke(diff_cli, ["real-session", "nonexistent-session", "--db", tmp_db])
    assert result.exit_code != 0


def test_diff_both_empty(tmp_db):
    """Both sessions exist but have no section data → minimal diff, no crash."""
    _make_session(tmp_db, "empty-A", [])
    _make_session(tmp_db, "empty-B", [])

    from lib.steps.diff import cli as diff_cli
    runner = CliRunner()
    result = runner.invoke(diff_cli, ["empty-A", "empty-B", "--db", tmp_db])
    assert result.exit_code == 0, result.output
    # Should mention both sessions without crashing
    assert "empty-A" in result.output
    assert "empty-B" in result.output
