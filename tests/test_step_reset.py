from click.testing import CliRunner
from lib.db import init_db
from lib.state import NewsletterAgentState, StepStatus
from lib.steps.reset import cli as reset_cli


def _setup(tmp_db):
    init_db(tmp_db)
    state = NewsletterAgentState(session_id="r1", db_path=tmp_db)
    state.complete_step("start")
    state.error_step("gather", error_message="boom")
    state.save_checkpoint("start")
    state.save_checkpoint("gather")


def test_reset_errors_clears_error_state(tmp_db):
    _setup(tmp_db)
    runner = CliRunner()
    result = runner.invoke(reset_cli, ["r1", "--db", tmp_db, "--errors", "--yes"])
    assert result.exit_code == 0
    state = NewsletterAgentState(session_id="r1", db_path=tmp_db).load_latest_from_db()
    assert state.get_step("gather").status == StepStatus.NOT_STARTED
    assert state.get_step("gather").error_message == ""


def test_reset_from_step_resets_step_and_after(tmp_db):
    init_db(tmp_db)
    state = NewsletterAgentState(session_id="r2", db_path=tmp_db)
    state.complete_step("start")
    state.complete_step("gather")
    state.complete_step("filter")
    state.save_checkpoint("filter")
    runner = CliRunner()
    runner.invoke(reset_cli, ["r2", "--db", tmp_db, "--from", "gather", "--yes"])

    state = NewsletterAgentState(session_id="r2", db_path=tmp_db).load_latest_from_db()
    assert state.get_step("start").status == StepStatus.COMPLETE
    assert state.get_step("gather").status == StepStatus.NOT_STARTED
    assert state.get_step("filter").status == StepStatus.NOT_STARTED


def test_reset_all(tmp_db):
    _setup(tmp_db)
    runner = CliRunner()
    runner.invoke(reset_cli, ["r1", "--db", tmp_db, "--all", "--yes"])
    state = NewsletterAgentState(session_id="r1", db_path=tmp_db).load_latest_from_db()
    assert all(s.status == StepStatus.NOT_STARTED for s in state.steps)


def test_reset_requires_one_action_flag(tmp_db):
    _setup(tmp_db)
    runner = CliRunner()
    result = runner.invoke(reset_cli, ["r1", "--db", tmp_db, "--yes"])
    assert result.exit_code != 0
    assert "one of" in result.output.lower() or "--errors" in result.output
