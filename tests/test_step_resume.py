from click.testing import CliRunner
from lib.db import init_db
from lib.state import NewsletterAgentState, StepStatus
from lib.steps.resume import cli as resume_cli


def _setup(tmp_db):
    init_db(tmp_db)
    state = NewsletterAgentState(session_id="rs1", db_path=tmp_db)
    state.complete_step("init")
    state.error_step("gather", "boom")
    state.save_checkpoint("gather")


def test_resume_clears_errors(tmp_db):
    _setup(tmp_db)
    runner = CliRunner()
    result = runner.invoke(resume_cli, ["rs1", "--db", tmp_db])
    assert result.exit_code == 0
    state = NewsletterAgentState(session_id="rs1", db_path=tmp_db).load_latest_from_db()
    assert state.get_step("gather").status == StepStatus.NOT_STARTED


def test_resume_prints_next_step(tmp_db):
    _setup(tmp_db)
    runner = CliRunner()
    result = runner.invoke(resume_cli, ["rs1", "--db", tmp_db])
    assert "gather" in result.output


def test_resume_all_complete(tmp_db):
    init_db(tmp_db)
    state = NewsletterAgentState(session_id="done", db_path=tmp_db)
    for sid, *_ in [
        ("init",), ("gather",), ("filter",), ("download",), ("summarize",),
        ("rate",), ("cluster",), ("select",), ("draft",), ("rewrite",), ("send",),
    ]:
        state.complete_step(sid)
    state.save_checkpoint("send")

    runner = CliRunner()
    result = runner.invoke(resume_cli, ["done", "--db", tmp_db])
    assert "complete" in result.output.lower()


def test_resume_missing_session(tmp_db):
    init_db(tmp_db)
    runner = CliRunner()
    result = runner.invoke(resume_cli, ["nope", "--db", tmp_db])
    assert "No state" in result.output or "not found" in result.output.lower()
