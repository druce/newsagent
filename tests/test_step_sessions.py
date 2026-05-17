from click.testing import CliRunner
from lib.steps.init import cli as init_cli
from lib.steps.sessions import cli as sessions_cli
from lib.db import init_db


def test_sessions_empty_db(tmp_db):
    init_db(tmp_db)
    runner = CliRunner()
    result = runner.invoke(sessions_cli, ["--db", tmp_db])
    assert result.exit_code == 0
    assert "no sessions" in result.output.lower()


def test_sessions_lists_created_sessions(tmp_db, sample_sources_yaml):
    init_db(tmp_db)
    runner = CliRunner()
    runner.invoke(init_cli, [
        "--db", tmp_db, "--sources", sample_sources_yaml, "--session", "alpha",
    ])
    runner.invoke(init_cli, [
        "--db", tmp_db, "--sources", sample_sources_yaml, "--session", "beta",
    ])
    result = runner.invoke(sessions_cli, ["--db", tmp_db])
    assert result.exit_code == 0
    assert "alpha" in result.output
    assert "beta" in result.output


def test_sessions_limit_flag(tmp_db, sample_sources_yaml):
    init_db(tmp_db)
    runner = CliRunner()
    for name in ["a", "b", "c"]:
        runner.invoke(init_cli, [
            "--db", tmp_db, "--sources", sample_sources_yaml, "--session", name,
        ])
    result = runner.invoke(sessions_cli, ["--db", tmp_db, "--limit", "2"])
    # Parse the data rows (skip header and separator)
    data_lines = [
        ln for ln in result.output.splitlines()
        if ln and not ln.startswith("SESSION") and not ln.startswith("-")
    ]
    shown = [ln.split()[0] for ln in data_lines if ln.split()]
    assert "c" in shown
    assert "b" in shown
    assert "a" not in shown  # oldest dropped by --limit 2
