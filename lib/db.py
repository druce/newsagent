"""SQLite persistence layer for newsagent.

Schema mirrors legacy ~/projects/OpenAIAgentsSDK/db.py so parity diffs are possible.
Phase 0 only ports the AgentState dataclass + helpers; other tables are declared
via init_db() but their dataclasses will be added in later phases as needed.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional


# ---------- Schema (CREATE TABLE statements) ----------

_SCHEMA_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS urls (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        initial_url TEXT NOT NULL UNIQUE,
        final_url TEXT NOT NULL,
        title TEXT NOT NULL,
        source TEXT NOT NULL,
        isAI BOOLEAN,
        created_at TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS articles (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        url TEXT NOT NULL UNIQUE,
        title TEXT,
        text TEXT,
        source TEXT,
        published TEXT,
        summary TEXT,
        rating REAL,
        cluster INTEGER,
        is_ai BOOLEAN,
        created_at TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS sites (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        domain TEXT NOT NULL UNIQUE,
        name TEXT,
        reputation REAL DEFAULT 0.0,
        scrape_method TEXT,
        bright_data_enabled INTEGER NOT NULL DEFAULT 0,
        last_seen TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS newsletters (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,
        title TEXT,
        html TEXT,
        sent_at TEXT,
        created_at TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS published_articles (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,
        url TEXT NOT NULL,
        title TEXT,
        short_summary TEXT,
        embedding TEXT,
        published_at TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS agent_state (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,
        step_name TEXT NOT NULL,
        state_data TEXT NOT NULL,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(session_id, step_name)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS bsky_queue (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        url TEXT NOT NULL UNIQUE,
        title TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        post_uri TEXT,
        error TEXT,
        attempts INTEGER NOT NULL DEFAULT 0,
        created_at TEXT,
        updated_at TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_agent_state_session_id ON agent_state(session_id)",
    "CREATE INDEX IF NOT EXISTS idx_agent_state_updated_at ON agent_state(updated_at)",
    "CREATE INDEX IF NOT EXISTS idx_bsky_queue_status ON bsky_queue(status)",
    "CREATE INDEX IF NOT EXISTS idx_published_articles_published_at "
    "ON published_articles(published_at)",
]


# Domains routed through Bright Data Web Unlocker by default (paywalls or
# aggressive anti-bot). Mirrors legacy config.IGNORE_LIST. Each entry is the
# bare domain; users can flip the flag in the `sites` row at any time.
_BRIGHT_DATA_DEFAULT_DOMAINS = (
    "bloomberg.com", "www.bloomberg.com",
    "cnn.com", "www.cnn.com",
    "wsj.com", "www.wsj.com",
    "fastcompany.com", "www.fastcompany.com",
    "forbes.com", "www.forbes.com",
)


def _migrate_sites_bright_data_column(conn: sqlite3.Connection) -> None:
    """Add bright_data_enabled to an existing sites table if missing."""
    cols = [r[1] for r in conn.execute("PRAGMA table_info(sites)").fetchall()]
    if cols and "bright_data_enabled" not in cols:
        conn.execute(
            "ALTER TABLE sites ADD COLUMN bright_data_enabled "
            "INTEGER NOT NULL DEFAULT 0"
        )


def _seed_bright_data_defaults(conn: sqlite3.Connection) -> None:
    """Mark the default Bright-Data-enabled domains. Inserts new rows or
    flips the flag on existing rows; preserves any other columns."""
    for domain in _BRIGHT_DATA_DEFAULT_DOMAINS:
        conn.execute(
            "INSERT INTO sites (domain, bright_data_enabled) VALUES (?, 1) "
            "ON CONFLICT(domain) DO UPDATE SET bright_data_enabled = 1",
            (domain,),
        )


def init_db(db_path: str) -> None:
    """Create all tables and indexes for a fresh database, migrate existing
    schemas to the current shape, and seed Bright-Data-enabled domains."""
    with sqlite3.connect(db_path) as conn:
        for stmt in _SCHEMA_STATEMENTS:
            conn.execute(stmt)
        _migrate_sites_bright_data_column(conn)
        _seed_bright_data_defaults(conn)
        conn.commit()


# ---------- AgentState dataclass ----------

def _iso(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    if isinstance(dt, str):
        return dt
    return dt.isoformat()


def _parse(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    return datetime.fromisoformat(s)


@dataclass
class AgentState:
    session_id: str
    step_name: str
    state_data: str
    updated_at: Optional[datetime]
    id: Optional[int] = None

    def upsert(self, conn: sqlite3.Connection) -> None:
        cur = conn.execute(
            """
            INSERT INTO agent_state (session_id, step_name, state_data, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(session_id, step_name) DO UPDATE SET
                state_data = excluded.state_data,
                updated_at = excluded.updated_at
            """,
            (self.session_id, self.step_name, self.state_data, _iso(self.updated_at)),
        )
        if cur.lastrowid:
            self.id = cur.lastrowid
        else:
            row = conn.execute(
                "SELECT id FROM agent_state WHERE session_id=? AND step_name=?",
                (self.session_id, self.step_name),
            ).fetchone()
            if row:
                self.id = row[0]
        conn.commit()

    @classmethod
    def get_by_session_and_step(
        cls, conn: sqlite3.Connection, session_id: str, step_name: str
    ) -> Optional["AgentState"]:
        row = conn.execute(
            "SELECT id, session_id, step_name, state_data, updated_at "
            "FROM agent_state WHERE session_id=? AND step_name=?",
            (session_id, step_name),
        ).fetchone()
        if not row:
            return None
        return cls(
            id=row[0], session_id=row[1], step_name=row[2],
            state_data=row[3], updated_at=_parse(row[4]),
        )

    @classmethod
    def get_latest_by_session(
        cls, conn: sqlite3.Connection, session_id: str
    ) -> Optional["AgentState"]:
        row = conn.execute(
            "SELECT id, session_id, step_name, state_data, updated_at "
            "FROM agent_state WHERE session_id=? "
            "ORDER BY updated_at DESC LIMIT 1",
            (session_id,),
        ).fetchone()
        if not row:
            return None
        return cls(
            id=row[0], session_id=row[1], step_name=row[2],
            state_data=row[3], updated_at=_parse(row[4]),
        )

    @classmethod
    def get_all_by_session(
        cls, conn: sqlite3.Connection, session_id: str
    ) -> list["AgentState"]:
        rows = conn.execute(
            "SELECT id, session_id, step_name, state_data, updated_at "
            "FROM agent_state WHERE session_id=? ORDER BY updated_at ASC",
            (session_id,),
        ).fetchall()
        return [
            AgentState(id=r[0], session_id=r[1], step_name=r[2],
                       state_data=r[3], updated_at=_parse(r[4]))
            for r in rows
        ]

    @classmethod
    def list_sessions(
        cls,
        conn: sqlite3.Connection,
        updated_after: Optional[datetime] = None,
        n_records: Optional[int] = 10,
    ) -> list[str]:
        """Return distinct session_ids, newest first (by max updated_at per session)."""
        query = (
            "SELECT session_id, MAX(updated_at) AS last_update FROM agent_state "
        )
        params: list = []
        if updated_after is not None:
            query += "WHERE updated_at > ? "
            params.append(_iso(updated_after))
        query += "GROUP BY session_id ORDER BY last_update DESC "
        if n_records is not None:
            query += "LIMIT ?"
            params.append(n_records)
        rows = conn.execute(query, params).fetchall()
        return [r[0] for r in rows]

    @classmethod
    def delete_session(cls, conn: sqlite3.Connection, session_id: str) -> int:
        cur = conn.execute(
            "DELETE FROM agent_state WHERE session_id=?", (session_id,)
        )
        conn.commit()
        return cur.rowcount


@dataclass
class Site:
    domain: str
    name: Optional[str] = None
    reputation: float = 0.0
    scrape_method: Optional[str] = None  # None | "http" | "playwright"
    # Tri-state on upsert: None = "don't touch the column on conflict",
    # True/False = "set the column to that value". Reads always materialize
    # as bool.
    bright_data_enabled: Optional[bool] = None
    last_seen: Optional[str] = None
    id: Optional[int] = None

    def upsert(self, conn: sqlite3.Connection) -> None:
        bd_param = None if self.bright_data_enabled is None else int(
            self.bright_data_enabled
        )
        # Insert path needs a concrete value (NOT NULL column with default 0).
        bd_insert = 0 if bd_param is None else bd_param
        cur = conn.execute(
            """
            INSERT INTO sites (domain, name, reputation, scrape_method,
                               bright_data_enabled, last_seen)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(domain) DO UPDATE SET
                name = COALESCE(excluded.name, name),
                reputation = excluded.reputation,
                scrape_method = COALESCE(excluded.scrape_method, scrape_method),
                bright_data_enabled = COALESCE(?, bright_data_enabled),
                last_seen = COALESCE(excluded.last_seen, last_seen)
            """,
            (self.domain, self.name, self.reputation,
             self.scrape_method, bd_insert, self.last_seen, bd_param),
        )
        if cur.lastrowid:
            self.id = cur.lastrowid
        conn.commit()

    @classmethod
    def get_by_domain(cls, conn: sqlite3.Connection, domain: str) -> Optional["Site"]:
        row = conn.execute(
            "SELECT id, domain, name, reputation, scrape_method, "
            "bright_data_enabled, last_seen FROM sites WHERE domain=?",
            (domain,),
        ).fetchone()
        if not row:
            return None
        return cls(id=row[0], domain=row[1], name=row[2],
                   reputation=row[3] or 0.0,
                   scrape_method=row[4],
                   bright_data_enabled=bool(row[5]),
                   last_seen=row[6])


@dataclass
class PublishedArticle:
    """One article that survived into a final published newsletter.

    Backs cross-day story-level de-dup: `crossdedupe` compares today's
    candidates against the last N days of these rows. `embedding` is the
    OpenAI text-embedding-3-large vector of `title + short_summary`.
    """
    session_id: str
    url: str
    title: Optional[str] = None
    short_summary: Optional[str] = None
    embedding: Optional[List[float]] = None
    published_at: Optional[str] = None
    id: Optional[int] = None

    def insert(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            "INSERT INTO published_articles "
            "(session_id, url, title, short_summary, embedding, published_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                self.session_id, self.url, self.title, self.short_summary,
                json.dumps(self.embedding) if self.embedding is not None else None,
                self.published_at,
            ),
        )
        conn.commit()

    @classmethod
    def recent(
        cls, conn: sqlite3.Connection, since: datetime
    ) -> List["PublishedArticle"]:
        """Rows published at/after `since`, newest first. ISO timestamps sort
        lexicographically, so a string comparison is a correct date filter."""
        rows = conn.execute(
            "SELECT id, session_id, url, title, short_summary, embedding, "
            "published_at FROM published_articles WHERE published_at >= ? "
            "ORDER BY published_at DESC",
            (_iso(since),),
        ).fetchall()
        out: List["PublishedArticle"] = []
        for r in rows:
            emb = json.loads(r[5]) if r[5] else None
            out.append(cls(
                id=r[0], session_id=r[1], url=r[2], title=r[3],
                short_summary=r[4], embedding=emb, published_at=r[6],
            ))
        return out
