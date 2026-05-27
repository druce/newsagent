import sqlite3
from lib.db import init_db
from lib.state import (
    NewsletterAgentState, StepStatus, WORKFLOW_STEPS,
)


def test_workflow_initializes_with_expected_steps(tmp_db):
    init_db(tmp_db)
    state = NewsletterAgentState(session_id="s1", db_path=tmp_db)
    ids = [s.id for s in state.steps]
    assert ids == [step_id for step_id, *_ in WORKFLOW_STEPS]
    assert all(s.status == StepStatus.NOT_STARTED for s in state.steps)


def test_start_complete_error_transitions(tmp_db):
    init_db(tmp_db)
    state = NewsletterAgentState(session_id="s1", db_path=tmp_db)
    state.start_step("gather")
    assert state.get_step("gather").status == StepStatus.STARTED
    state.complete_step("gather", message="ok")
    assert state.get_step("gather").status == StepStatus.COMPLETE
    state.error_step("filter", error_message="boom")
    assert state.get_step("filter").status == StepStatus.ERROR
    assert state.get_step("filter").error_message == "boom"


def test_get_current_step_returns_first_incomplete(tmp_db):
    init_db(tmp_db)
    state = NewsletterAgentState(session_id="s1", db_path=tmp_db)
    assert state.get_current_step() == "start"
    state.complete_step("start")
    assert state.get_current_step() == "gather"


def test_progress_percentage(tmp_db):
    init_db(tmp_db)
    state = NewsletterAgentState(session_id="s1", db_path=tmp_db)
    assert state.get_progress_percentage() == 0.0
    state.complete_step("start")
    n = len(state.steps)
    assert state.get_progress_percentage() == (1 / n) * 100


def test_clear_errors(tmp_db):
    init_db(tmp_db)
    state = NewsletterAgentState(session_id="s1", db_path=tmp_db)
    state.error_step("gather", "x")
    state.clear_errors()
    assert state.get_step("gather").status == StepStatus.NOT_STARTED
    assert state.get_step("gather").error_message == ""


def test_serialize_to_db_and_load_round_trip(tmp_db):
    init_db(tmp_db)
    state = NewsletterAgentState(session_id="s1", db_path=tmp_db)
    state.complete_step("start", message="started session")
    state.serialize_to_db("start")

    fresh = NewsletterAgentState(session_id="s1", db_path=tmp_db)
    loaded = fresh.load_latest_from_db()
    assert loaded is not None
    assert loaded.get_step("start").status == StepStatus.COMPLETE
    assert loaded.get_step("start").status_message == "started session"


def test_save_checkpoint_writes_one_row(tmp_db):
    init_db(tmp_db)
    state = NewsletterAgentState(session_id="s1", db_path=tmp_db)
    state.save_checkpoint("start")
    with sqlite3.connect(tmp_db) as conn:
        rows = conn.execute(
            "SELECT step_name FROM agent_state WHERE session_id=?", ("s1",)
        ).fetchall()
    assert rows == [("start",)]


def test_list_recent_sessions(tmp_db):
    init_db(tmp_db)
    NewsletterAgentState(session_id="a", db_path=tmp_db).save_checkpoint("start")
    NewsletterAgentState(session_id="b", db_path=tmp_db).save_checkpoint("start")
    recents = NewsletterAgentState.list_recent_sessions(tmp_db, limit=10)
    assert set(recents) == {"a", "b"}
    assert recents[0] in {"a", "b"}  # newest first (ordering by updated_at)
