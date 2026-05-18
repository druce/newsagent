"""Tests for lib.steps.checkpoint — manual state persist."""
import sqlite3
from click.testing import CliRunner
from lib.db import init_db, AgentState
from lib.state import NewsletterAgentState


def _make_session(tmp_db: str, sid: str) -> None:
    """Create a session with init complete."""
    init_db(tmp_db)
    state = NewsletterAgentState(session_id=sid, db_path=tmp_db)
    state.complete_step("init")
    state.save_checkpoint("init")


def test_checkpoint_persists_state_at_step(tmp_db):
    """Running checkpoint for an existing session persists an agent_state row."""
    _make_session(tmp_db, "cp-session")

    from lib.steps.checkpoint import cli as checkpoint_cli
    runner = CliRunner()
    result = runner.invoke(checkpoint_cli, ["cp-session", "gather", "--db", tmp_db])
    assert result.exit_code == 0, result.output

    # Verify a row exists for this session + step
    with sqlite3.connect(tmp_db) as conn:
        row = AgentState.get_by_session_and_step(conn, "cp-session", "gather")
    assert row is not None, "agent_state row for gather should exist after checkpoint"
    assert row.session_id == "cp-session"
    assert row.step_name == "gather"


def test_checkpoint_unknown_session_errors(tmp_db):
    """Checkpoint for a nonexistent session exits with non-zero code."""
    init_db(tmp_db)

    from lib.steps.checkpoint import cli as checkpoint_cli
    runner = CliRunner()
    result = runner.invoke(checkpoint_cli, ["no-such-session", "init", "--db", tmp_db])
    assert result.exit_code != 0
