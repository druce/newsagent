import sqlite3
from datetime import datetime, timedelta
from lib.db import init_db, AgentState, PublishedArticle


def test_init_db_creates_all_tables(tmp_db):
    init_db(tmp_db)
    with sqlite3.connect(tmp_db) as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        names = {r[0] for r in rows}
    assert {"urls", "articles", "sites", "newsletters", "agent_state",
            "published_articles"} <= names


def test_published_article_insert_and_recent_roundtrip(tmp_db):
    init_db(tmp_db)
    now = datetime(2026, 7, 6, 9, 0, 0)
    with sqlite3.connect(tmp_db) as conn:
        PublishedArticle(
            session_id="s1",
            url="https://fortune.com/x",
            title="CEO doubles revenue with Claude",
            short_summary="A CEO used Claude AI to double revenue despite a hallucination.",
            embedding=[0.1, 0.2, 0.3],
            published_at=now.isoformat(),
        ).insert(conn)
        recent = PublishedArticle.recent(conn, since=now - timedelta(days=4))
    assert len(recent) == 1
    row = recent[0]
    assert row.url == "https://fortune.com/x"
    assert row.short_summary.startswith("A CEO used Claude")
    # embedding round-trips as list[float]
    assert row.embedding == [0.1, 0.2, 0.3]
    assert all(isinstance(x, float) for x in row.embedding)


def test_published_article_recent_filters_window(tmp_db):
    init_db(tmp_db)
    now = datetime(2026, 7, 6, 9, 0, 0)
    with sqlite3.connect(tmp_db) as conn:
        PublishedArticle(
            session_id="old", url="https://e.com/old", title="old",
            short_summary="old", embedding=[1.0],
            published_at=(now - timedelta(days=10)).isoformat(),
        ).insert(conn)
        PublishedArticle(
            session_id="new", url="https://e.com/new", title="new",
            short_summary="new", embedding=[2.0],
            published_at=(now - timedelta(days=1)).isoformat(),
        ).insert(conn)
        recent = PublishedArticle.recent(conn, since=now - timedelta(days=4))
    urls = {r.url for r in recent}
    assert urls == {"https://e.com/new"}


def test_published_article_recent_handles_null_embedding(tmp_db):
    init_db(tmp_db)
    now = datetime(2026, 7, 6, 9, 0, 0)
    with sqlite3.connect(tmp_db) as conn:
        PublishedArticle(
            session_id="s1", url="https://e.com/a", title="a",
            short_summary="a", embedding=None,
            published_at=now.isoformat(),
        ).insert(conn)
        recent = PublishedArticle.recent(conn, since=now - timedelta(days=4))
    assert len(recent) == 1
    assert recent[0].embedding is None


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
