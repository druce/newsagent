"""SQLite persistence layer for news_agent.

Schema mirrors legacy ~/projects/OpenAIAgentsSDK/db.py so parity diffs are possible.
Phase 0 only ports the AgentState dataclass + helpers; other tables are declared
via init_db() but their dataclasses will be added in later phases as needed.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Optional


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
    CREATE TABLE IF NOT EXISTS agent_state (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,
        step_name TEXT NOT NULL,
        state_data TEXT NOT NULL,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(session_id, step_name)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_agent_state_session_id ON agent_state(session_id)",
    "CREATE INDEX IF NOT EXISTS idx_agent_state_updated_at ON agent_state(updated_at)",
]


def init_db(db_path: str) -> None:
    """Create all tables and indexes for a fresh database."""
    with sqlite3.connect(db_path) as conn:
        for stmt in _SCHEMA_STATEMENTS:
            conn.execute(stmt)
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
    last_seen: Optional[str] = None
    id: Optional[int] = None

    def upsert(self, conn: sqlite3.Connection) -> None:
        cur = conn.execute(
            """
            INSERT INTO sites (domain, name, reputation, scrape_method, last_seen)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(domain) DO UPDATE SET
                name = COALESCE(excluded.name, name),
                reputation = excluded.reputation,
                scrape_method = COALESCE(excluded.scrape_method, scrape_method),
                last_seen = COALESCE(excluded.last_seen, last_seen)
            """,
            (self.domain, self.name, self.reputation,
             self.scrape_method, self.last_seen),
        )
        if cur.lastrowid:
            self.id = cur.lastrowid
        conn.commit()

    @classmethod
    def get_by_domain(cls, conn: sqlite3.Connection, domain: str) -> Optional["Site"]:
        row = conn.execute(
            "SELECT id, domain, name, reputation, scrape_method, last_seen "
            "FROM sites WHERE domain=?",
            (domain,),
        ).fetchone()
        if not row:
            return None
        return cls(id=row[0], domain=row[1], name=row[2],
                   reputation=row[3] or 0.0,
                   scrape_method=row[4], last_seen=row[5])
