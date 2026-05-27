from click.testing import CliRunner
from lib.db import init_db
from lib.state import NewsletterAgentState
from lib.steps.show import cli as show_cli


def _setup(tmp_db):
    init_db(tmp_db)
    state = NewsletterAgentState(session_id="x1", db_path=tmp_db,
                                 sources_file="sources.yaml",
                                 sources={"A": {"type": "rss"}})
    state.complete_step("start", message="setup")
    state.start_step("gather")
    state.save_checkpoint("start")
    state.save_checkpoint("gather")


def test_show_dumps_full_state(tmp_db):
    _setup(tmp_db)
    runner = CliRunner()
    result = runner.invoke(show_cli, ["x1", "--db", tmp_db])
    assert result.exit_code == 0
    assert "x1" in result.output
    assert "start" in result.output
    assert "gather" in result.output
    assert "complete" in result.output.lower()


def test_show_specific_step(tmp_db):
    _setup(tmp_db)
    runner = CliRunner()
    result = runner.invoke(show_cli, ["x1", "--db", tmp_db, "--step", "start"])
    assert result.exit_code == 0
    assert "start" in result.output


def test_show_missing_session(tmp_db):
    init_db(tmp_db)
    runner = CliRunner()
    result = runner.invoke(show_cli, ["nope", "--db", tmp_db])
    assert "No state" in result.output or "not found" in result.output.lower()
