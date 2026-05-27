"""Tests for lib/run_summary.py — run summary generator."""
import json
from pathlib import Path

import pytest

from lib.db import init_db
from lib.state import NewsletterAgentState


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _seed_empty_session(tmp_db: str, session_id: str = "empty-sess") -> None:
    """Create a session with no steps completed."""
    init_db(tmp_db)
    state = NewsletterAgentState(session_id=session_id, db_path=tmp_db)
    # Don't complete any steps — just save initial checkpoint
    state.save_checkpoint("start")


def _seed_partial_session(tmp_db: str, session_id: str = "partial-sess") -> None:
    """Create a session with init and gather completed."""
    init_db(tmp_db)
    state = NewsletterAgentState(session_id=session_id, db_path=tmp_db)
    state.complete_step("start", message="loaded 5 sources")
    state.save_checkpoint("start")
    state.complete_step("gather", message="42 headlines from 5 sources")
    state.headline_data = [{"title": f"story {i}", "link": f"https://ex.com/{i}"}
                           for i in range(42)]
    state.save_checkpoint("gather")


# ---------------------------------------------------------------------------
# Test 1: generate_summary for an empty session — minimal output
# ---------------------------------------------------------------------------

def test_generate_summary_empty_session(tmp_db, tmp_path, monkeypatch):
    """generate_summary returns non-empty markdown even when no steps are complete."""
    monkeypatch.chdir(tmp_path)
    _seed_empty_session(tmp_db, session_id="empty-sess")

    from lib.run_summary import generate_summary

    summary = generate_summary("empty-sess", db_path=tmp_db, runs_dir="runs")

    assert isinstance(summary, str)
    assert len(summary) > 0
    # Must mention the session id
    assert "empty-sess" in summary
    # Must contain the workflow table header
    assert "Step" in summary
    # All steps should be not_started
    assert "not_started" in summary


# ---------------------------------------------------------------------------
# Test 2: generate_summary for partial session — shows correct statuses
# ---------------------------------------------------------------------------

def test_generate_summary_partial_session(tmp_db, tmp_path, monkeypatch):
    """generate_summary shows complete for init/gather, not_started for later steps."""
    monkeypatch.chdir(tmp_path)
    _seed_partial_session(tmp_db, session_id="partial-sess")

    from lib.run_summary import generate_summary

    summary = generate_summary("partial-sess", db_path=tmp_db, runs_dir="runs")

    assert "partial-sess" in summary
    # init and gather should show complete
    assert "complete" in summary
    # Some steps should still be not_started
    assert "not_started" in summary
    # Gather status message should appear
    assert "42 headlines" in summary or "gather" in summary


# ---------------------------------------------------------------------------
# Test 3: write_summary actually writes the file and returns correct path
# ---------------------------------------------------------------------------

def test_write_summary_writes_file(tmp_db, tmp_path, monkeypatch):
    """write_summary creates runs/<SID>/summary.md and returns its path."""
    monkeypatch.chdir(tmp_path)
    _seed_partial_session(tmp_db, session_id="write-sess")

    from lib.run_summary import write_summary

    path_str = write_summary("write-sess", db_path=tmp_db, runs_dir="runs")

    # Returns a path string
    assert isinstance(path_str, str)
    assert "write-sess" in path_str
    assert path_str.endswith("summary.md")

    # File actually exists
    p = Path(path_str)
    assert p.exists(), f"summary.md not found at {p}"

    # File content is the markdown
    content = p.read_text()
    assert "write-sess" in content
    assert "Step" in content
