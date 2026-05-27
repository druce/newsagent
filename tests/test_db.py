import sqlite3
from datetime import datetime
from lib.db import init_db, AgentState


def test_init_db_creates_all_tables(tmp_db):
    init_db(tmp_db)
    with sqlite3.connect(tmp_db) as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        names = {r[0] for r in rows}
    assert {"urls", "articles", "sites", "newsletters", "agent_state"} <= names


def test_agent_state_upsert_and_get(tmp_db):
    init_db(tmp_db)
    with sqlite3.connect(tmp_db) as conn:
        rec = AgentState(
            session_id="sess-A",
            step_name="start",
            state_data='{"hello": "world"}',
            updated_at=datetime(2026, 5, 17, 12, 0, 0),
        )
        rec.upsert(conn)
        fetched = AgentState.get_by_session_and_step(conn, "sess-A", "start")
    assert fetched is not None
    assert fetched.state_data == '{"hello": "world"}'
    assert fetched.id is not None


def test_agent_state_upsert_is_idempotent(tmp_db):
    init_db(tmp_db)
    with sqlite3.connect(tmp_db) as conn:
        AgentState(
            session_id="sess-A", step_name="start",
            state_data='{"v": 1}', updated_at=datetime(2026, 5, 17, 12, 0, 0),
        ).upsert(conn)
        AgentState(
            session_id="sess-A", step_name="start",
            state_data='{"v": 2}', updated_at=datetime(2026, 5, 17, 12, 1, 0),
        ).upsert(conn)
        rows = conn.execute(
            "SELECT state_data FROM agent_state WHERE session_id=? AND step_name=?",
            ("sess-A", "start"),
        ).fetchall()
    assert len(rows) == 1
    assert rows[0][0] == '{"v": 2}'


def test_agent_state_list_sessions_orders_newest_first(tmp_db):
    init_db(tmp_db)
    with sqlite3.connect(tmp_db) as conn:
        AgentState(
            session_id="old", step_name="start",
            state_data="{}", updated_at=datetime(2026, 5, 1),
        ).upsert(conn)
        AgentState(
            session_id="new", step_name="start",
            state_data="{}", updated_at=datetime(2026, 5, 17),
        ).upsert(conn)
        sessions = AgentState.list_sessions(conn, n_records=10)
    assert sessions == ["new", "old"]


def test_agent_state_get_latest_by_session(tmp_db):
    init_db(tmp_db)
    with sqlite3.connect(tmp_db) as conn:
        AgentState(
            session_id="s1", step_name="start",
            state_data="{}", updated_at=datetime(2026, 5, 17, 10, 0),
        ).upsert(conn)
        AgentState(
            session_id="s1", step_name="gather",
            state_data="{}", updated_at=datetime(2026, 5, 17, 11, 0),
        ).upsert(conn)
        latest = AgentState.get_latest_by_session(conn, "s1")
    assert latest is not None
    assert latest.step_name == "gather"
