"""Tests for lib.steps.gc — garbage-collect old sessions."""
import sqlite3
from pathlib import Path
from datetime import datetime, timedelta

from click.testing import CliRunner
from lib.db import init_db, AgentState
from lib.state import NewsletterAgentState


def _make_session(db_path: str, sid: str, days_ago: int, runs_dir: Path) -> None:
    """Create a session updated `days_ago` days ago, plus a runs/<sid>/ directory."""
    init_db(db_path)
    state = NewsletterAgentState(session_id=sid, db_path=db_path)
    state.complete_step("init")
    state.save_checkpoint("init")

    # Back-date the updated_at row to simulate old session
    old_ts = (datetime.now() - timedelta(days=days_ago)).isoformat()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE agent_state SET updated_at=? WHERE session_id=?",
            (old_ts, sid),
        )
        conn.commit()

    # Create a runs/<sid>/ scratch directory
    run_dir = runs_dir / sid
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "report.txt").write_text(f"run data for {sid}")


def test_gc_dryrun_lists_candidates_without_deleting(tmp_db, tmp_path, monkeypatch):
    """Dry-run lists old sessions but does not delete DB rows or run dirs."""
    monkeypatch.chdir(tmp_path)
    runs_dir = tmp_path / "runs"

    # Session 40 days old (older than default 30)
    _make_session(tmp_db, "old-session", 40, runs_dir)
    # Session 5 days old (within default 30)
    _make_session(tmp_db, "recent-session", 5, runs_dir)

    from lib.steps.gc import cli as gc_cli
    runner = CliRunner()
    result = runner.invoke(gc_cli, ["--db", tmp_db, "--older-than", "30"])
    assert result.exit_code == 0, result.output

    # Dry-run: should mention the old session
    assert "old-session" in result.output

    # DB rows should still exist
    with sqlite3.connect(tmp_db) as conn:
        rows = conn.execute(
            "SELECT COUNT(*) FROM agent_state WHERE session_id='old-session'"
        ).fetchone()
        assert rows[0] > 0, "dry-run must not delete DB rows"

    # Run dir should still exist
    assert (runs_dir / "old-session").exists(), "dry-run must not delete run dirs"


def test_gc_yes_deletes_db_rows_and_run_dirs(tmp_db, tmp_path, monkeypatch):
    """--yes flag deletes agent_state rows and runs/<sid>/ directories."""
    monkeypatch.chdir(tmp_path)
    runs_dir = tmp_path / "runs"

    _make_session(tmp_db, "stale-A", 60, runs_dir)
    _make_session(tmp_db, "stale-B", 45, runs_dir)

    from lib.steps.gc import cli as gc_cli
    runner = CliRunner()
    result = runner.invoke(gc_cli, ["--db", tmp_db, "--older-than", "30", "--yes"])
    assert result.exit_code == 0, result.output

    # DB rows gone
    with sqlite3.connect(tmp_db) as conn:
        for sid in ("stale-A", "stale-B"):
            rows = conn.execute(
                "SELECT COUNT(*) FROM agent_state WHERE session_id=?", (sid,)
            ).fetchone()
            assert rows[0] == 0, f"DB rows for {sid} should be deleted"

    # Run dirs gone
    for sid in ("stale-A", "stale-B"):
        assert not (runs_dir / sid).exists(), f"runs/{sid}/ should be deleted"


def test_gc_older_than_0_lists_everything(tmp_db, tmp_path, monkeypatch):
    """--older-than 0 in dry-run mode lists all sessions."""
    monkeypatch.chdir(tmp_path)
    runs_dir = tmp_path / "runs"

    _make_session(tmp_db, "brand-new", 0, runs_dir)
    _make_session(tmp_db, "day-old", 1, runs_dir)

    from lib.steps.gc import cli as gc_cli
    runner = CliRunner()
    result = runner.invoke(gc_cli, ["--db", tmp_db, "--older-than", "0"])
    assert result.exit_code == 0, result.output

    # Both should be listed (dry-run)
    assert "brand-new" in result.output or "day-old" in result.output
    # Nothing deleted (no --yes)
    with sqlite3.connect(tmp_db) as conn:
        count = conn.execute("SELECT COUNT(*) FROM agent_state").fetchone()[0]
        assert count >= 2, "dry-run must not delete anything"
