from click.testing import CliRunner
from lib.steps.init import cli as init_cli
from lib.steps.status import cli as status_cli
from lib.db import init_db


def test_status_uses_most_recent_session_when_no_sid(tmp_db, sample_sources_yaml):
    init_db(tmp_db)
    runner = CliRunner()
    runner.invoke(init_cli, [
        "--db", tmp_db, "--sources", sample_sources_yaml, "--session", "sess-A",
    ])
    result = runner.invoke(status_cli, ["--db", tmp_db])
    assert result.exit_code == 0, result.output
    assert "sess-A" in result.output
    assert "init" in result.output
    assert "complete" in result.output.lower()


def test_status_specific_session(tmp_db, sample_sources_yaml):
    init_db(tmp_db)
    runner = CliRunner()
    runner.invoke(init_cli, [
        "--db", tmp_db, "--sources", sample_sources_yaml, "--session", "alpha",
    ])
    runner.invoke(init_cli, [
        "--db", tmp_db, "--sources", sample_sources_yaml, "--session", "beta",
    ])
    result = runner.invoke(status_cli, ["--db", tmp_db, "--session", "alpha"])
    assert result.exit_code == 0
    assert "alpha" in result.output


def test_status_no_sessions(tmp_db):
    init_db(tmp_db)
    runner = CliRunner()
    result = runner.invoke(status_cli, ["--db", tmp_db])
    assert result.exit_code == 0
    assert "no sessions" in result.output.lower()


def test_status_reports_progress_percentage(tmp_db, sample_sources_yaml):
    init_db(tmp_db)
    runner = CliRunner()
    runner.invoke(init_cli, [
        "--db", tmp_db, "--sources", sample_sources_yaml, "--session", "s",
    ])
    result = runner.invoke(status_cli, ["--db", tmp_db])
    assert "%" in result.output
