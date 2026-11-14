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
        url TEXT NOT NULL UNIQUE,
        title TEXT,
        short_summary TEXT,
        embedding TEXT,
        published_at TEXT,
        site_name TEXT,
        cat TEXT,
        selected INTEGER NOT NULL DEFAULT 1,
        summary TEXT,
        rating REAL,
        bsky_status TEXT NOT NULL DEFAULT 'none',
        bsky_uri TEXT,
        bsky_error TEXT,
        bsky_attempts INTEGER NOT NULL DEFAULT 0
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


# Columns added to published_articles after the original 7-column shape. All
# pre-existing rows were newsletter picks, so `selected` defaults to 1.
_PUBLISHED_ARTICLES_NEW_COLUMNS = (
    ("site_name", "TEXT"),
    ("cat", "TEXT"),
    ("selected", "INTEGER NOT NULL DEFAULT 1"),
    ("summary", "TEXT"),
    ("rating", "REAL"),
    ("bsky_status", "TEXT NOT NULL DEFAULT 'none'"),
    ("bsky_uri", "TEXT"),
    ("bsky_error", "TEXT"),
    ("bsky_attempts", "INTEGER NOT NULL DEFAULT 0"),
)


def _has_unique_url_index(conn: sqlite3.Connection) -> bool:
    for idx in conn.execute("PRAGMA index_list(published_articles)").fetchall():
        name, unique = idx[1], idx[2]
        if not unique:
            continue
        cols = [c[2] for c in conn.execute(f"PRAGMA index_info('{name}')").fetchall()]
        if cols == ["url"]:
            return True
    return False


def _migrate_published_articles(conn: sqlite3.Connection) -> None:
    """Bring an existing published_articles table to the current shape:
    add the editorial + bsky columns, then enforce one row per url (keeping,
    per url, the most recently published row; highest id breaks ties)."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(published_articles)").fetchall()}
    if not cols:
        return
    for name, decl in _PUBLISHED_ARTICLES_NEW_COLUMNS:
        if name not in cols:
            conn.execute(f"ALTER TABLE published_articles ADD COLUMN {name} {decl}")
    if not _has_unique_url_index(conn):
        conn.execute(
            """
            DELETE FROM published_articles WHERE id NOT IN (
                SELECT id FROM (
                    SELECT id, ROW_NUMBER() OVER (
                        PARTITION BY url
                        ORDER BY COALESCE(published_at, '') DESC, id DESC
                    ) AS rn FROM published_articles
                ) WHERE rn = 1
            )
            """
        )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_published_articles_url "
            "ON published_articles(url)"
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
        _migrate_published_articles(conn)
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
    """One rated article, one row per url (UNIQUE).

    Written by `select` for every rated article of a session (`selected=1` for
    the MMR picks, which alone carry an embedding); `send` stamps
    `published_at`. So "published" ⇔ `selected=1 AND published_at IS NOT NULL`
    — exactly what `recent()` returns to `crossdedupe`. `embedding` is the
    OpenAI text-embedding-3-large vector of `title + short_summary`.

    The `bsky_*` columns are placeholders: `bsky_queue` is still the share
    daemon's source of truth, and nothing here writes them yet.
    """
    session_id: str
    url: str
    title: Optional[str] = None
    short_summary: Optional[str] = None
    embedding: Optional[List[float]] = None
    published_at: Optional[str] = None
    site_name: Optional[str] = None
    cat: Optional[str] = None
    selected: bool = True
    summary: Optional[str] = None
    rating: Optional[float] = None
    bsky_status: str = "none"
    bsky_uri: Optional[str] = None
    bsky_error: Optional[str] = None
    bsky_attempts: int = 0
    id: Optional[int] = None

    _COLS = ("session_id, url, title, short_summary, embedding, published_at, "
             "site_name, cat, selected, summary, rating")

    def _values(self) -> tuple:
        return (
            self.session_id, self.url, self.title, self.short_summary,
            json.dumps(self.embedding) if self.embedding is not None else None,
            self.published_at, self.site_name, self.cat,
            1 if self.selected else 0, self.summary, self.rating,
        )

    def upsert(self, conn: sqlite3.Connection) -> None:
        """Insert, or update the existing row for this url — but only while
        that row is unpublished (`published_at IS NULL`): a published row is
        frozen at its first publication. Never touches `bsky_*`. No commit."""
        conn.execute(
            f"INSERT INTO published_articles ({self._COLS}) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(url) DO UPDATE SET "
            "session_id=excluded.session_id, title=excluded.title, "
            "short_summary=excluded.short_summary, embedding=excluded.embedding, "
            "published_at=excluded.published_at, site_name=excluded.site_name, "
            "cat=excluded.cat, selected=excluded.selected, summary=excluded.summary, "
            "rating=excluded.rating "
            "WHERE published_articles.published_at IS NULL",
            self._values(),
        )

    def insert(self, conn: sqlite3.Connection) -> None:
        """Upsert this row and commit."""
        self.upsert(conn)
        conn.commit()

    @classmethod
    def upsert_session(
        cls, conn: sqlite3.Connection, session_id: str, rows: List["PublishedArticle"]
    ) -> None:
        """Upsert a session's rated set, then prune this session's rows that
        dropped out of it — only if still unpublished and never queued to
        Bluesky. One transaction."""
        try:
            for r in rows:
                r.upsert(conn)
            urls = [r.url for r in rows]
            placeholders = ",".join("?" for _ in urls) or "NULL"
            conn.execute(
                "DELETE FROM published_articles WHERE session_id = ? "
                "AND published_at IS NULL AND bsky_status = 'none' "
                f"AND url NOT IN ({placeholders})",
                (session_id, *urls),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    @classmethod
    def mark_published(cls, conn: sqlite3.Connection, session_id: str, ts: str) -> int:
        """Stamp this session's selected, not-yet-stamped rows as published.
        Returns the number of rows stamped."""
        cur = conn.execute(
            "UPDATE published_articles SET published_at = ? "
            "WHERE session_id = ? AND selected = 1 AND published_at IS NULL",
            (ts, session_id),
        )
        conn.commit()
        return cur.rowcount

    @classmethod
    def session_count(cls, conn: sqlite3.Connection, session_id: str) -> int:
        return conn.execute(
            "SELECT COUNT(*) FROM published_articles WHERE session_id = ?",
            (session_id,),
        ).fetchone()[0]

    @classmethod
    def recent(
        cls, conn: sqlite3.Connection, since: datetime
    ) -> List["PublishedArticle"]:
        """Published rows (selected and sent) at/after `since`, newest first.
        ISO timestamps sort lexicographically, so a string comparison is a
        correct date filter; NULL `published_at` (selected but unsent) never
        matches."""
        rows = conn.execute(
            "SELECT id, session_id, url, title, short_summary, embedding, "
            "published_at, site_name, cat, selected, summary, rating, "
            "bsky_status, bsky_uri, bsky_error, bsky_attempts "
            "FROM published_articles WHERE published_at >= ? AND selected = 1 "
            "ORDER BY published_at DESC",
            (_iso(since),),
        ).fetchall()
        out: List["PublishedArticle"] = []
        for r in rows:
            out.append(cls(
                id=r[0], session_id=r[1], url=r[2], title=r[3],
                short_summary=r[4], embedding=json.loads(r[5]) if r[5] else None,
                published_at=r[6], site_name=r[7], cat=r[8], selected=bool(r[9]),
                summary=r[10], rating=r[11], bsky_status=r[12], bsky_uri=r[13],
                bsky_error=r[14], bsky_attempts=r[15],
            ))
        return out
