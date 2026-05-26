import json
import sqlite3
from click.testing import CliRunner
from lib.steps.start import cli
from lib.db import init_db, AgentState


def test_init_creates_session_row(tmp_db, sample_sources_yaml):
    init_db(tmp_db)
    runner = CliRunner()
    result = runner.invoke(cli, [
        "--db", tmp_db,
        "--sources", sample_sources_yaml,
        "--session", "sess-X",
    ])
    assert result.exit_code == 0, result.output
    assert "sess-X" in result.output
    with sqlite3.connect(tmp_db) as conn:
        rec = AgentState.get_by_session_and_step(conn, "sess-X", "start")
    assert rec is not None
    data = json.loads(rec.state_data)
    assert data["session_id"] == "sess-X"
    assert "example_rss" in data["sources"]


def test_init_autogenerates_session_id(tmp_db, sample_sources_yaml):
    init_db(tmp_db)
    runner = CliRunner()
    result = runner.invoke(cli, ["--db", tmp_db, "--sources", sample_sources_yaml])
    assert result.exit_code == 0, result.output
    with sqlite3.connect(tmp_db) as conn:
        sessions = AgentState.list_sessions(conn, n_records=5)
    assert len(sessions) == 1
    assert sessions[0].startswith("2026")  # date-prefixed


def test_init_dry_run_summary_lists_enabled_sources(tmp_db, sample_sources_yaml):
    init_db(tmp_db)
    runner = CliRunner()
    result = runner.invoke(cli, [
        "--db", tmp_db, "--sources", sample_sources_yaml, "--session", "s2",
    ])
    assert "example_rss" in result.output
    # example_html is disabled — should still appear but marked
    assert "example_html" in result.output


def test_init_fails_cleanly_on_missing_sources_file(tmp_db):
    init_db(tmp_db)
    runner = CliRunner()
    result = runner.invoke(cli, [
        "--db", tmp_db, "--sources", "/nonexistent.yaml", "--session", "s3",
    ])
    assert result.exit_code != 0
    assert "sources" in result.output.lower()
