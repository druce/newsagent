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


# ── published_articles: widened schema, one row per url ──────────────────

_NEW_PA_COLS = {"site_name", "cat", "selected", "summary", "rating",
                "bsky_status", "bsky_uri", "bsky_error", "bsky_attempts"}


def _pa_cols(conn):
    return {r[1] for r in conn.execute("PRAGMA table_info(published_articles)")}


def _has_unique_url(conn):
    for idx in conn.execute("PRAGMA index_list(published_articles)").fetchall():
        name, unique = idx[1], idx[2]
        if unique and [c[2] for c in conn.execute(f"PRAGMA index_info('{name}')")] == ["url"]:
            return True
    return False


def test_fresh_published_articles_schema(tmp_db):
    init_db(tmp_db)
    with sqlite3.connect(tmp_db) as conn:
        assert _NEW_PA_COLS <= _pa_cols(conn)
        assert _has_unique_url(conn)


def test_migrate_old_published_articles(tmp_db):
    """Old 7-column table with cross- and same-session duplicate urls."""
    with sqlite3.connect(tmp_db) as conn:
        conn.execute("""CREATE TABLE published_articles (
            id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL,
            url TEXT NOT NULL, title TEXT, short_summary TEXT, embedding TEXT,
            published_at TEXT)""")
        rows = [
            ("s1", "https://e.com/a", "a-old", "2026-09-01T07:00:00"),
            ("s2", "https://e.com/a", "a-new", "2026-09-03T07:00:00"),
            ("s3", "https://e.com/b", "b-1", "2026-09-04T07:00:00"),
            ("s3", "https://e.com/b", "b-2", "2026-09-04T07:00:00"),  # tie → higher id
            ("s3", "https://e.com/c", "c", "2026-09-04T07:00:00"),
        ]
        conn.executemany(
            "INSERT INTO published_articles (session_id, url, title, published_at) "
            "VALUES (?, ?, ?, ?)", rows)
        conn.commit()

    init_db(tmp_db)
    init_db(tmp_db)  # idempotent

    with sqlite3.connect(tmp_db) as conn:
        assert _NEW_PA_COLS <= _pa_cols(conn)
        assert _has_unique_url(conn)
        got = conn.execute(
            "SELECT url, title, selected, bsky_status, bsky_attempts "
            "FROM published_articles ORDER BY url").fetchall()
    assert got == [
        ("https://e.com/a", "a-new", 1, "none", 0),
        ("https://e.com/b", "b-2", 1, "none", 0),
        ("https://e.com/c", "c", 1, "none", 0),
    ]


def test_recent_excludes_unselected_and_unsent(tmp_db):
    init_db(tmp_db)
    now = datetime(2026, 7, 6, 9, 0, 0)
    ts = (now - timedelta(days=1)).isoformat()
    with sqlite3.connect(tmp_db) as conn:
        PublishedArticle(session_id="s", url="https://e.com/sent", embedding=[1.0],
                         published_at=ts).insert(conn)
        PublishedArticle(session_id="s", url="https://e.com/unselected", selected=False,
                         published_at=None).insert(conn)
        PublishedArticle(session_id="s", url="https://e.com/unsent", embedding=[1.0],
                         published_at=None).insert(conn)
        recent = PublishedArticle.recent(conn, since=now - timedelta(days=4))
    assert [r.url for r in recent] == ["https://e.com/sent"]


def test_upsert_overwrites_unpublished_keeps_published_and_bsky(tmp_db):
    init_db(tmp_db)
    with sqlite3.connect(tmp_db) as conn:
        PublishedArticle(session_id="old", url="https://e.com/u", title="u-old",
                         selected=False, rating=1.0).insert(conn)
        PublishedArticle(session_id="old", url="https://e.com/p", title="p-old",
                         published_at="2026-09-01T07:00:00").insert(conn)
        conn.execute("UPDATE published_articles SET bsky_status='posted', "
                     "bsky_uri='at://x', bsky_attempts=1 WHERE url='https://e.com/u'")
        conn.commit()

        PublishedArticle(session_id="new", url="https://e.com/u", title="u-new",
                         selected=True, rating=5.0, embedding=[0.5]).upsert(conn)
        PublishedArticle(session_id="new", url="https://e.com/p", title="p-new",
                         selected=False).upsert(conn)
        conn.commit()
        rows = {r[0]: r[1:] for r in conn.execute(
            "SELECT url, session_id, title, selected, rating, published_at, "
            "bsky_status, bsky_uri, bsky_attempts FROM published_articles")}
    assert rows["https://e.com/u"] == ("new", "u-new", 1, 5.0, None, "posted", "at://x", 1)
    assert rows["https://e.com/p"] == ("old", "p-old", 1, None, "2026-09-01T07:00:00",
                                       "none", None, 0)


def test_upsert_session_prunes_stale_unpublished_rows(tmp_db):
    init_db(tmp_db)
    with sqlite3.connect(tmp_db) as conn:
        PublishedArticle.upsert_session(conn, "s1", [
            PublishedArticle(session_id="s1", url="https://e.com/1", selected=True),
            PublishedArticle(session_id="s1", url="https://e.com/2", selected=False),
            PublishedArticle(session_id="s1", url="https://e.com/3", selected=False),
        ])
        conn.execute("UPDATE published_articles SET bsky_status='posted' "
                     "WHERE url='https://e.com/3'")
        conn.commit()
        # re-select: article 2 and 3 no longer in the rated set
        PublishedArticle.upsert_session(conn, "s1", [
            PublishedArticle(session_id="s1", url="https://e.com/1", selected=False),
        ])
        got = conn.execute(
            "SELECT url, selected FROM published_articles ORDER BY url").fetchall()
    # 2 pruned; 3 kept because it carries bsky state
    assert got == [("https://e.com/1", 0), ("https://e.com/3", 0)]


def test_mark_published_stamps_only_session_selected_unstamped(tmp_db):
    init_db(tmp_db)
    with sqlite3.connect(tmp_db) as conn:
        PublishedArticle(session_id="s1", url="https://e.com/1").insert(conn)
        PublishedArticle(session_id="s1", url="https://e.com/2", selected=False).insert(conn)
        PublishedArticle(session_id="s1", url="https://e.com/3",
                         published_at="2026-01-01T00:00:00").insert(conn)
        PublishedArticle(session_id="s2", url="https://e.com/4").insert(conn)
        n = PublishedArticle.mark_published(conn, "s1", "2026-09-10T07:00:00")
        assert PublishedArticle.session_count(conn, "s1") == 3
        got = dict(conn.execute("SELECT url, published_at FROM published_articles"))
    assert n == 1
    assert got == {
        "https://e.com/1": "2026-09-10T07:00:00",
        "https://e.com/2": None,
        "https://e.com/3": "2026-01-01T00:00:00",
        "https://e.com/4": None,
    }
