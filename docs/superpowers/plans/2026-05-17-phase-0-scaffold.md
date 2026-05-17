# Phase 0 — Scaffold Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up the `news_agent` Claude Code plugin skeleton: plugin manifest, ported state/DB modules, and three read-only skills (`news:init`, `news:status`, `news:sessions`) that exercise session create + read paths end-to-end on an empty SQLite DB.

**Architecture:** Project-local but **publishable-shaped** plugin (`plugin.json` with name/version/repo metadata). Python helpers under `lib/`; each Claude Code skill is a `SKILL.md` plus a CLI-callable `lib/steps/<name>.py main()`. State storage is SQLite (`newsletter_agent.db`) using the legacy `agent_state` schema verbatim (one row per session × step_name, JSON-encoded Pydantic state in `state_data`). Per [CLAUDE_REFACTOR.md](../../../CLAUDE_REFACTOR.md), we port `WorkflowStep`/`WorkflowState`/`NewsletterAgentState` from legacy but drop the `_migrate_old_state_format` migration code and the RAG `ensure_rag_steps` branch — clean slate.

**Tech Stack:** Python 3.11+, Pydantic v2, SQLite (stdlib `sqlite3`), pytest, Click (CLI), PyYAML.

---

## File Structure

| Path | Purpose |
|---|---|
| `plugin.json` | Plugin manifest (name, version, description, skills list). |
| `pyproject.toml` | Python project metadata + dependencies. |
| `requirements.txt` | Pinned runtime deps for non-poetry installs. |
| `lib/__init__.py` | Package marker. |
| `lib/db.py` | SQLite schema + dataclasses for `urls`, `articles`, `sites`, `newsletters`, `agent_state`. Phase 0 implements only `AgentState` + `init_db()`; the other tables are declared but Phase 0 doesn't need their dataclasses yet. |
| `lib/state.py` | `StepStatus`, `WorkflowStep`, `WorkflowState`, `NewsletterAgentState` (Pydantic), serialize/load/checkpoint, plus new `list_recent_sessions()` helper. |
| `lib/steps/__init__.py` | Package marker. |
| `lib/steps/init.py` | CLI: create session, load sources.yaml, validate, print dry-run summary. |
| `lib/steps/status.py` | CLI: show step list, progress %, error messages for a session. |
| `lib/steps/sessions.py` | CLI: list recent sessions. |
| `skills/init/SKILL.md` | Agent-facing contract for `/news:init`. |
| `skills/status/SKILL.md` | Agent-facing contract for `/news:status`. |
| `skills/sessions/SKILL.md` | Agent-facing contract for `/news:sessions`. |
| `tests/__init__.py` | Package marker. |
| `tests/conftest.py` | Fixtures: temp DB path, sample sources.yaml. |
| `tests/test_db.py` | Schema creation, AgentState CRUD/upsert/list_sessions. |
| `tests/test_state.py` | Workflow building, serialize/load round-trip, list_recent_sessions. |
| `tests/test_step_init.py` | `lib/steps/init.py main()` smoke test. |
| `tests/test_step_status.py` | `lib/steps/status.py main()` smoke test. |
| `tests/test_step_sessions.py` | `lib/steps/sessions.py main()` smoke test. |

**Decomposition rationale:** `lib/db.py` is the pure persistence layer (no Pydantic, no business logic). `lib/state.py` is the workflow/business layer (Pydantic models, calls into `db.py`). Skills are thin CLI wrappers; logic lives in `lib/steps/*.py main()`. Each file ≤ ~400 LOC; if `state.py` grows past that we split off `workflow.py`.

**Workflow steps (12) we register in `NewsletterAgentState._initialize_workflow`:**
`gather → filter → download → summarize → rate → cluster → select → draft → rewrite → send` plus the orchestration-only `init` step at the front. (`init` is recorded as a step so progress % includes it.) Total 11 steps + we intentionally split `draft` and `rewrite` (legacy had `draft_sections` + `finalize_newsletter`). The standalone `bluesky` pipeline is NOT in this 11; it's a separate flow. (Note: per CLAUDE_REFACTOR.md "12 steps" includes the implicit bluesky pipeline as #12; the main run is 11 steps + 1 alt-flow.)

---

## Task 1: Initialize git + project skeleton

**Files:**
- Create: `pyproject.toml`
- Create: `requirements.txt`
- Create: `.gitignore`
- Create: `lib/__init__.py`
- Create: `lib/steps/__init__.py`
- Create: `tests/__init__.py`

- [ ] **Step 1: Initialize git and create directory skeleton**

Run:
```bash
cd /Users/drucev/projects/news_agent
git init
mkdir -p lib/steps lib/prompts tests skills/init skills/status skills/sessions agents runs out download
```

- [ ] **Step 2: Write `.gitignore`**

Create `/Users/drucev/projects/news_agent/.gitignore`:
```gitignore
__pycache__/
*.pyc
.pytest_cache/
.env
*.db
*.db-journal
download/
runs/
out/
*.pkl
umap_reducer.pkl
.venv/
venv/
.coverage
htmlcov/
```

- [ ] **Step 3: Write `pyproject.toml`**

Create `/Users/drucev/projects/news_agent/pyproject.toml`:
```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "news-agent"
version = "0.1.0"
description = "Daily AI newsletter agent as a Claude Code skill plugin"
readme = "README.md"
requires-python = ">=3.11"
authors = [{name = "Druce Vertes", email = "drucev@gmail.com"}]
dependencies = [
  "pydantic>=2.7",
  "pyyaml>=6.0",
  "click>=8.1",
]

[project.optional-dependencies]
dev = [
  "pytest>=8.0",
  "pytest-cov>=5.0",
]

[tool.setuptools.packages.find]
include = ["lib*"]

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-v"
```

- [ ] **Step 4: Write `requirements.txt`**

Create `/Users/drucev/projects/news_agent/requirements.txt`:
```
pydantic>=2.7
pyyaml>=6.0
click>=8.1
pytest>=8.0
pytest-cov>=5.0
```

- [ ] **Step 5: Create empty package markers**

Create `/Users/drucev/projects/news_agent/lib/__init__.py` (empty file).
Create `/Users/drucev/projects/news_agent/lib/steps/__init__.py` (empty file).
Create `/Users/drucev/projects/news_agent/tests/__init__.py` (empty file).

- [ ] **Step 6: Install dependencies and verify pytest runs**

Run:
```bash
cd /Users/drucev/projects/news_agent
pip install -e ".[dev]"
pytest --version
```
Expected: pytest 8.x reported, no errors.

- [ ] **Step 7: Commit**

```bash
cd /Users/drucev/projects/news_agent
git add .gitignore pyproject.toml requirements.txt lib/__init__.py lib/steps/__init__.py tests/__init__.py
git commit -m "chore: project skeleton (pyproject, deps, package markers)"
```

---

## Task 2: Write `lib/db.py` — AgentState table + init_db

**Files:**
- Create: `lib/db.py`
- Create: `tests/conftest.py`
- Create: `tests/test_db.py`

**Note on scope:** Phase 0 only needs the `agent_state` table to function end-to-end. We declare CREATE TABLE statements for `urls`, `articles`, `sites`, `newsletters` so `init_db()` produces a complete schema (matching legacy), but we only port the **dataclass** + helper methods for `AgentState`. Other dataclasses are added in later phases as their step skills need them.

- [ ] **Step 1: Write the failing tests (conftest + test_db)**

Create `/Users/drucev/projects/news_agent/tests/conftest.py`:
```python
import pytest
from pathlib import Path


@pytest.fixture
def tmp_db(tmp_path: Path) -> str:
    """Return a path to a fresh SQLite DB file in a temp dir."""
    return str(tmp_path / "test_newsletter.db")


@pytest.fixture
def sample_sources_yaml(tmp_path: Path) -> str:
    """Write a minimal sources.yaml and return its path."""
    content = """
sources:
  example_rss:
    type: rss
    url: https://example.com/feed.xml
    enabled: true
  example_html:
    type: html
    url: https://example.com/news
    enabled: false
"""
    p = tmp_path / "sources.yaml"
    p.write_text(content)
    return str(p)
```

Create `/Users/drucev/projects/news_agent/tests/test_db.py`:
```python
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
            step_name="init",
            state_data='{"hello": "world"}',
            updated_at=datetime(2026, 5, 17, 12, 0, 0),
        )
        rec.upsert(conn)
        fetched = AgentState.get_by_session_and_step(conn, "sess-A", "init")
    assert fetched is not None
    assert fetched.state_data == '{"hello": "world"}'
    assert fetched.id is not None


def test_agent_state_upsert_is_idempotent(tmp_db):
    init_db(tmp_db)
    with sqlite3.connect(tmp_db) as conn:
        AgentState(
            session_id="sess-A", step_name="init",
            state_data='{"v": 1}', updated_at=datetime(2026, 5, 17, 12, 0, 0),
        ).upsert(conn)
        AgentState(
            session_id="sess-A", step_name="init",
            state_data='{"v": 2}', updated_at=datetime(2026, 5, 17, 12, 1, 0),
        ).upsert(conn)
        rows = conn.execute(
            "SELECT state_data FROM agent_state WHERE session_id=? AND step_name=?",
            ("sess-A", "init"),
        ).fetchall()
    assert len(rows) == 1
    assert rows[0][0] == '{"v": 2}'


def test_agent_state_list_sessions_orders_newest_first(tmp_db):
    init_db(tmp_db)
    with sqlite3.connect(tmp_db) as conn:
        AgentState(
            session_id="old", step_name="init",
            state_data="{}", updated_at=datetime(2026, 5, 1),
        ).upsert(conn)
        AgentState(
            session_id="new", step_name="init",
            state_data="{}", updated_at=datetime(2026, 5, 17),
        ).upsert(conn)
        sessions = AgentState.list_sessions(conn, n_records=10)
    assert sessions == ["new", "old"]


def test_agent_state_get_latest_by_session(tmp_db):
    init_db(tmp_db)
    with sqlite3.connect(tmp_db) as conn:
        AgentState(
            session_id="s1", step_name="init",
            state_data="{}", updated_at=datetime(2026, 5, 17, 10, 0),
        ).upsert(conn)
        AgentState(
            session_id="s1", step_name="gather",
            state_data="{}", updated_at=datetime(2026, 5, 17, 11, 0),
        ).upsert(conn)
        latest = AgentState.get_latest_by_session(conn, "s1")
    assert latest is not None
    assert latest.step_name == "gather"
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
cd /Users/drucev/projects/news_agent
pytest tests/test_db.py -v
```
Expected: 5 errors, "No module named 'lib.db'".

- [ ] **Step 3: Implement `lib/db.py`**

Create `/Users/drucev/projects/news_agent/lib/db.py`:
```python
"""SQLite persistence layer for news_agent.

Schema mirrors legacy ~/projects/OpenAIAgentsSDK/db.py so parity diffs are possible.
Phase 0 only ports the AgentState dataclass + helpers; other tables are declared
via init_db() but their dataclasses will be added in later phases as needed.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
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
    updated_at: datetime
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
            cls(id=r[0], session_id=r[1], step_name=r[2],
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run:
```bash
cd /Users/drucev/projects/news_agent
pytest tests/test_db.py -v
```
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
cd /Users/drucev/projects/news_agent
git add lib/db.py tests/conftest.py tests/test_db.py
git commit -m "feat(db): port agent_state schema + AgentState dataclass"
```

---

## Task 3: Write `lib/state.py` — Pydantic workflow state

**Files:**
- Create: `lib/state.py`
- Create: `tests/test_state.py`

- [ ] **Step 1: Write the failing tests**

Create `/Users/drucev/projects/news_agent/tests/test_state.py`:
```python
import json
import sqlite3
from datetime import datetime
from lib.db import init_db, AgentState
from lib.state import (
    NewsletterAgentState, StepStatus, WORKFLOW_STEPS,
)


def test_workflow_initializes_with_expected_steps(tmp_db):
    init_db(tmp_db)
    state = NewsletterAgentState(session_id="s1", db_path=tmp_db)
    ids = [s.id for s in state.steps]
    assert ids == [step_id for step_id, _name, _desc in WORKFLOW_STEPS]
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
    assert state.get_current_step() == "init"
    state.complete_step("init")
    assert state.get_current_step() == "gather"


def test_progress_percentage(tmp_db):
    init_db(tmp_db)
    state = NewsletterAgentState(session_id="s1", db_path=tmp_db)
    assert state.get_progress_percentage() == 0.0
    state.complete_step("init")
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
    state.complete_step("init", message="started session")
    state.serialize_to_db("init")

    fresh = NewsletterAgentState(session_id="s1", db_path=tmp_db)
    loaded = fresh.load_latest_from_db()
    assert loaded is not None
    assert loaded.get_step("init").status == StepStatus.COMPLETE
    assert loaded.get_step("init").status_message == "started session"


def test_save_checkpoint_writes_one_row(tmp_db, capsys):
    init_db(tmp_db)
    state = NewsletterAgentState(session_id="s1", db_path=tmp_db)
    state.save_checkpoint("init")
    with sqlite3.connect(tmp_db) as conn:
        rows = conn.execute(
            "SELECT step_name FROM agent_state WHERE session_id=?", ("s1",)
        ).fetchall()
    assert rows == [("init",)]


def test_list_recent_sessions(tmp_db):
    init_db(tmp_db)
    NewsletterAgentState(session_id="a", db_path=tmp_db).save_checkpoint("init")
    NewsletterAgentState(session_id="b", db_path=tmp_db).save_checkpoint("init")
    recents = NewsletterAgentState.list_recent_sessions(tmp_db, limit=10)
    assert set(recents) == {"a", "b"}
    assert recents[0] in {"a", "b"}  # newest first (ordering by updated_at)
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
cd /Users/drucev/projects/news_agent
pytest tests/test_state.py -v
```
Expected: errors with "No module named 'lib.state'".

- [ ] **Step 3: Implement `lib/state.py`**

Create `/Users/drucev/projects/news_agent/lib/state.py`:
```python
"""Pydantic workflow state for news_agent.

Ported from legacy ~/projects/OpenAIAgentsSDK/newsletter_state.py with the
following deliberate omissions:
- _migrate_old_state_format (we start clean)
- ensure_rag_steps / _RAG_STEPS (no RAG flow)
- pandas DataFrame conveniences (deferred until a step actually needs them)
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from lib.db import AgentState


# ---------- Workflow definition ----------

# (step_id, human_name, description)
WORKFLOW_STEPS: list[tuple[str, str, str]] = [
    ("init",      "Initialize",        "Create session and validate sources"),
    ("gather",    "Gather URLs",       "Fetch headlines/URLs from configured sources"),
    ("filter",    "Filter URLs",       "Classify AI-relevance and drop dupes vs DB"),
    ("download",  "Download Articles", "Fetch full article HTML and extract text"),
    ("summarize", "Summarize",         "Bullet-point summaries + one-line headlines"),
    ("rate",      "Rate Articles",     "Multi-axis rating + Bradley-Terry composite"),
    ("cluster",   "Cluster Topics",    "UMAP+HDBSCAN clustering and naming"),
    ("select",    "Select Sections",   "MMR-diverse top-K per cluster"),
    ("draft",     "Draft Sections",    "Parallel section drafters with critic loop"),
    ("rewrite",   "Rewrite Newsletter","Whole-newsletter critic-optimizer pass"),
    ("send",      "Send",              "Render HTML, write to out/, optionally email"),
]


class StepStatus(str, Enum):
    NOT_STARTED = "not_started"
    STARTED = "started"
    COMPLETE = "complete"
    ERROR = "error"
    SKIPPED = "skipped"


# ---------- Step + workflow models ----------

class WorkflowStep(BaseModel):
    id: str
    name: str
    description: str
    status: StepStatus = StepStatus.NOT_STARTED
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    error_message: str = ""
    status_message: str = ""
    retry_count: int = 0
    metadata: Dict[str, Any] = Field(default_factory=dict)

    def start(self) -> None:
        self.status = StepStatus.STARTED
        self.started_at = datetime.now()

    def complete(self, message: str = "") -> None:
        self.status = StepStatus.COMPLETE
        self.completed_at = datetime.now()
        if message:
            self.status_message = message

    def error(self, message: str) -> None:
        self.status = StepStatus.ERROR
        self.error_message = message
        self.retry_count += 1


class WorkflowState(BaseModel):
    steps: List[WorkflowStep] = Field(default_factory=list)
    current_step_name: str = ""

    def get_step(self, step_id: str) -> Optional[WorkflowStep]:
        return next((s for s in self.steps if s.id == step_id), None)

    def get_step_index(self, step_id: str) -> int:
        for i, s in enumerate(self.steps):
            if s.id == step_id:
                return i
        return -1

    def start_step(self, step_id: str) -> None:
        step = self.get_step(step_id)
        if step is None:
            raise ValueError(f"Unknown step: {step_id}")
        step.start()
        self.current_step_name = step_id

    def complete_step(self, step_id: str, message: str = "") -> None:
        step = self.get_step(step_id)
        if step is None:
            raise ValueError(f"Unknown step: {step_id}")
        step.complete(message=message)

    def error_step(self, step_id: str, error_message: str = "Unknown error") -> None:
        step = self.get_step(step_id)
        if step is None:
            raise ValueError(f"Unknown step: {step_id}")
        step.error(error_message)

    def get_current_step(self) -> Optional[str]:
        for s in self.steps:
            if s.status != StepStatus.COMPLETE:
                return s.id
        return None

    def get_progress_percentage(self) -> float:
        if not self.steps:
            return 0.0
        done = sum(1 for s in self.steps if s.status == StepStatus.COMPLETE)
        return (done / len(self.steps)) * 100

    def get_completed_steps(self) -> List[str]:
        return [s.id for s in self.steps if s.status == StepStatus.COMPLETE]

    def get_failed_steps(self) -> List[str]:
        return [s.id for s in self.steps if s.status == StepStatus.ERROR]

    def get_started_steps(self) -> List[str]:
        return [s.id for s in self.steps if s.status == StepStatus.STARTED]

    def all_complete(self) -> bool:
        return all(s.status == StepStatus.COMPLETE for s in self.steps)

    def has_errors(self) -> bool:
        return any(s.status == StepStatus.ERROR for s in self.steps)

    def clear_errors(self) -> None:
        for s in self.steps:
            if s.status == StepStatus.ERROR:
                s.status = StepStatus.NOT_STARTED
                s.error_message = ""

    def reset(self) -> None:
        for s in self.steps:
            s.status = StepStatus.NOT_STARTED
            s.started_at = None
            s.completed_at = None
            s.error_message = ""
            s.status_message = ""
            s.retry_count = 0
        self.current_step_name = ""

    def get_workflow_status_report(self, title: str = "Workflow Status") -> str:
        lines = [
            title.upper(),
            f"Progress: {self.get_progress_percentage():.1f}% "
            f"({len(self.get_completed_steps())}/{len(self.steps)} complete)",
        ]
        cur = self.get_current_step()
        if cur:
            step = self.get_step(cur)
            idx = self.get_step_index(cur)
            lines.append(f"Next Step: Step {idx}: {step.name if step else ''}")
        else:
            lines.append("Status: All steps complete")
        lines.append("\nStep Details:")
        for i, step in enumerate(self.steps):
            lines.append(f"  Step {i}: {step.name}: {step.status.value}")
            if step.error_message:
                lines.append(f"    Error: {step.error_message}")
            if step.status_message:
                lines.append(f"    Status: {step.status_message}")
        return "\n".join(lines)


# ---------- Newsletter-specific state ----------

class NewsletterAgentState(WorkflowState):
    """Workflow state + newsletter-specific data, persisted to SQLite."""

    headline_data: List[Dict[str, Any]] = Field(default_factory=list)
    cluster_names: List[str] = Field(default_factory=list)
    clusters: Dict[str, List[str]] = Field(default_factory=dict)
    newsletter_section_data: List[Dict[str, Any]] = Field(default_factory=list)
    newsletter_title: str = ""
    final_newsletter: str = ""

    sources_file: str = "sources.yaml"
    sources: Dict[str, Any] = Field(default_factory=dict)

    session_id: str = ""
    db_path: str = "newsletter_agent.db"

    max_edits: int = 2
    concurrency: int = 8
    do_download: bool = True
    reprocess_since: Optional[datetime] = None

    def __init__(self, **data: Any) -> None:
        super().__init__(**data)
        if not self.steps:
            for step_id, name, desc in WORKFLOW_STEPS:
                self.steps.append(WorkflowStep(id=step_id, name=name, description=desc))

    # ---- persistence ----

    def serialize_to_db(self, step_name: str) -> None:
        with sqlite3.connect(self.db_path) as conn:
            rec = AgentState(
                session_id=self.session_id,
                step_name=step_name,
                state_data=self.model_dump_json(),
                updated_at=datetime.now(),
            )
            rec.upsert(conn)

    def save_checkpoint(self, step_name: str) -> None:
        self.serialize_to_db(step_name)

    def load_from_db(self, step_name: str) -> Optional["NewsletterAgentState"]:
        with sqlite3.connect(self.db_path) as conn:
            rec = AgentState.get_by_session_and_step(conn, self.session_id, step_name)
        if rec is None:
            return None
        data = json.loads(rec.state_data)
        return type(self)(**data)

    def load_latest_from_db(self) -> Optional["NewsletterAgentState"]:
        with sqlite3.connect(self.db_path) as conn:
            rec = AgentState.get_latest_by_session(conn, self.session_id)
        if rec is None:
            return None
        data = json.loads(rec.state_data)
        return type(self)(**data)

    def list_session_steps(self) -> List[Dict[str, Any]]:
        with sqlite3.connect(self.db_path) as conn:
            records = AgentState.get_all_by_session(conn, self.session_id)
        return [
            {"step_name": r.step_name,
             "updated_at": r.updated_at.isoformat() if r.updated_at else None}
            for r in records
        ]

    @classmethod
    def list_recent_sessions(
        cls, db_path: str, limit: int = 10,
        updated_after: Optional[datetime] = None,
    ) -> List[str]:
        with sqlite3.connect(db_path) as conn:
            return AgentState.list_sessions(
                conn, updated_after=updated_after, n_records=limit
            )

    # ---- status ----

    def get_status(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "headlines": {"total": len(self.headline_data)},
            "sources": {
                "config_file": self.sources_file,
                "loaded_sources": len(self.sources),
            },
            "topics": {
                "cluster_topics": len(self.cluster_names),
                "topics": self.cluster_names,
            },
            "workflow": {
                "current_step": self.get_current_step(),
                "workflow_complete": self.all_complete(),
                "progress_percentage": self.get_progress_percentage(),
                "completed_steps": len(self.get_completed_steps()),
                "total_steps": len(self.steps),
            },
            "processing": {
                "topic_clusters": len(self.clusters),
                "newsletter_sections": len(self.newsletter_section_data),
                "final_newsletter_length": len(self.final_newsletter),
            },
        }
```

- [ ] **Step 4: Run tests to verify they pass**

Run:
```bash
cd /Users/drucev/projects/news_agent
pytest tests/test_state.py -v
```
Expected: 8 passed.

- [ ] **Step 5: Commit**

```bash
cd /Users/drucev/projects/news_agent
git add lib/state.py tests/test_state.py
git commit -m "feat(state): port Pydantic workflow state with 11-step pipeline"
```

---

## Task 4: Write `lib/steps/init.py` — `news:init` Python helper

**Files:**
- Create: `lib/steps/init.py`
- Create: `tests/test_step_init.py`

- [ ] **Step 1: Write the failing tests**

Create `/Users/drucev/projects/news_agent/tests/test_step_init.py`:
```python
import json
import sqlite3
from click.testing import CliRunner
from lib.steps.init import cli
from lib.db import init_db, AgentState


def test_init_creates_session_row(tmp_db, sample_sources_yaml):
    init_db(tmp_db)
    runner = CliRunner()
    result = runner.invoke(cli, [
        "--db", tmp_db,
        "--sources", sample_sources_yaml,
        "--session", "sess-X",
    ])
    assert result.exit_code == 0, result.output
    assert "sess-X" in result.output
    with sqlite3.connect(tmp_db) as conn:
        rec = AgentState.get_by_session_and_step(conn, "sess-X", "init")
    assert rec is not None
    data = json.loads(rec.state_data)
    assert data["session_id"] == "sess-X"
    assert "example_rss" in data["sources"]


def test_init_autogenerates_session_id(tmp_db, sample_sources_yaml):
    init_db(tmp_db)
    runner = CliRunner()
    result = runner.invoke(cli, ["--db", tmp_db, "--sources", sample_sources_yaml])
    assert result.exit_code == 0, result.output
    with sqlite3.connect(tmp_db) as conn:
        sessions = AgentState.list_sessions(conn, n_records=5)
    assert len(sessions) == 1
    assert sessions[0].startswith("2026")  # date-prefixed


def test_init_dry_run_summary_lists_enabled_sources(tmp_db, sample_sources_yaml):
    init_db(tmp_db)
    runner = CliRunner()
    result = runner.invoke(cli, [
        "--db", tmp_db, "--sources", sample_sources_yaml, "--session", "s2",
    ])
    assert "example_rss" in result.output
    # example_html is disabled — should still appear but marked
    assert "example_html" in result.output


def test_init_fails_cleanly_on_missing_sources_file(tmp_db):
    init_db(tmp_db)
    runner = CliRunner()
    result = runner.invoke(cli, [
        "--db", tmp_db, "--sources", "/nonexistent.yaml", "--session", "s3",
    ])
    assert result.exit_code != 0
    assert "sources" in result.output.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
cd /Users/drucev/projects/news_agent
pytest tests/test_step_init.py -v
```
Expected: errors with "No module named 'lib.steps.init'".

- [ ] **Step 3: Implement `lib/steps/init.py`**

Create `/Users/drucev/projects/news_agent/lib/steps/init.py`:
```python
"""news:init — create a new session and validate sources.

CLI entry: python -m lib.steps.init --db PATH --sources sources.yaml [--session SID]
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import click
import yaml

from lib.db import init_db
from lib.state import NewsletterAgentState


def _generate_session_id() -> str:
    return datetime.now().strftime("%Y-%m-%d-%H%M%S")


def _load_sources(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        raise click.ClickException(f"sources file not found: {path}")
    with p.open() as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise click.ClickException(f"sources file must be a YAML mapping: {path}")
    sources = data.get("sources", data)
    if not isinstance(sources, dict) or not sources:
        raise click.ClickException(f"no sources defined in {path}")
    return sources


@click.command()
@click.option("--db", "db_path", default="newsletter_agent.db",
              help="Path to SQLite database")
@click.option("--sources", "sources_path", default="sources.yaml",
              help="Path to sources.yaml")
@click.option("--session", "session_id", default=None,
              help="Session ID (autogenerated if omitted)")
def cli(db_path: str, sources_path: str, session_id: str | None) -> None:
    """Create a new newsletter session."""
    init_db(db_path)

    sources = _load_sources(sources_path)

    sid = session_id or _generate_session_id()
    state = NewsletterAgentState(
        session_id=sid,
        db_path=db_path,
        sources_file=sources_path,
        sources=sources,
    )
    state.start_step("init")
    state.complete_step("init", message=f"loaded {len(sources)} sources")
    state.save_checkpoint("init")

    click.echo(f"Session created: {sid}")
    click.echo(f"Database: {db_path}")
    click.echo(f"Sources file: {sources_path}")
    click.echo(f"Sources ({len(sources)}):")
    for name, cfg in sources.items():
        enabled = cfg.get("enabled", True) if isinstance(cfg, dict) else True
        marker = "ENABLED " if enabled else "disabled"
        stype = cfg.get("type", "?") if isinstance(cfg, dict) else "?"
        click.echo(f"  [{marker}] {name} ({stype})")
    click.echo(f"Workflow: {len(state.steps)} steps registered.")
    click.echo("Next: /news:gather")


if __name__ == "__main__":  # pragma: no cover
    sys.exit(cli())
```

- [ ] **Step 4: Run tests to verify they pass**

Run:
```bash
cd /Users/drucev/projects/news_agent
pytest tests/test_step_init.py -v
```
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
cd /Users/drucev/projects/news_agent
git add lib/steps/init.py tests/test_step_init.py
git commit -m "feat(steps): news:init creates session and validates sources"
```

---

## Task 5: Write `lib/steps/status.py` — `news:status` Python helper

**Files:**
- Create: `lib/steps/status.py`
- Create: `tests/test_step_status.py`

- [ ] **Step 1: Write the failing tests**

Create `/Users/drucev/projects/news_agent/tests/test_step_status.py`:
```python
from click.testing import CliRunner
from lib.steps.init import cli as init_cli
from lib.steps.status import cli as status_cli
from lib.db import init_db


def test_status_uses_most_recent_session_when_no_sid(tmp_db, sample_sources_yaml):
    init_db(tmp_db)
    runner = CliRunner()
    runner.invoke(init_cli, [
        "--db", tmp_db, "--sources", sample_sources_yaml, "--session", "sess-A",
    ])
    result = runner.invoke(status_cli, ["--db", tmp_db])
    assert result.exit_code == 0, result.output
    assert "sess-A" in result.output
    assert "init" in result.output
    assert "complete" in result.output.lower()


def test_status_specific_session(tmp_db, sample_sources_yaml):
    init_db(tmp_db)
    runner = CliRunner()
    runner.invoke(init_cli, [
        "--db", tmp_db, "--sources", sample_sources_yaml, "--session", "alpha",
    ])
    runner.invoke(init_cli, [
        "--db", tmp_db, "--sources", sample_sources_yaml, "--session", "beta",
    ])
    result = runner.invoke(status_cli, ["--db", tmp_db, "--session", "alpha"])
    assert result.exit_code == 0
    assert "alpha" in result.output


def test_status_no_sessions(tmp_db):
    init_db(tmp_db)
    runner = CliRunner()
    result = runner.invoke(status_cli, ["--db", tmp_db])
    assert result.exit_code == 0
    assert "no sessions" in result.output.lower()


def test_status_reports_progress_percentage(tmp_db, sample_sources_yaml):
    init_db(tmp_db)
    runner = CliRunner()
    runner.invoke(init_cli, [
        "--db", tmp_db, "--sources", sample_sources_yaml, "--session", "s",
    ])
    result = runner.invoke(status_cli, ["--db", tmp_db])
    assert "%" in result.output
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
cd /Users/drucev/projects/news_agent
pytest tests/test_step_status.py -v
```
Expected: errors with "No module named 'lib.steps.status'".

- [ ] **Step 3: Implement `lib/steps/status.py`**

Create `/Users/drucev/projects/news_agent/lib/steps/status.py`:
```python
"""news:status — show workflow progress for a session.

CLI entry: python -m lib.steps.status [--db PATH] [--session SID]
"""
from __future__ import annotations

import sys

import click

from lib.state import NewsletterAgentState


@click.command()
@click.option("--db", "db_path", default="newsletter_agent.db",
              help="Path to SQLite database")
@click.option("--session", "session_id", default=None,
              help="Session ID (defaults to most recent)")
def cli(db_path: str, session_id: str | None) -> None:
    """Report status of the current or specified session."""
    if session_id is None:
        recents = NewsletterAgentState.list_recent_sessions(db_path, limit=1)
        if not recents:
            click.echo("No sessions found. Run /news:init to create one.")
            return
        session_id = recents[0]

    state = NewsletterAgentState(session_id=session_id, db_path=db_path)
    loaded = state.load_latest_from_db()
    if loaded is None:
        click.echo(f"No state found for session {session_id}.")
        return

    status = loaded.get_status()
    click.echo(f"Session:  {session_id}")
    click.echo(
        f"Progress: {status['workflow']['progress_percentage']:.1f}% "
        f"({status['workflow']['completed_steps']}/{status['workflow']['total_steps']} steps)"
    )
    cur = status["workflow"]["current_step"]
    click.echo(f"Next:     {cur if cur else '(all complete)'}")
    click.echo(f"Headlines: {status['headlines']['total']}")
    click.echo(f"Clusters:  {status['topics']['cluster_topics']}")
    click.echo(f"Sections:  {status['processing']['newsletter_sections']}")
    click.echo("")
    click.echo(loaded.get_workflow_status_report(title="Workflow"))


if __name__ == "__main__":  # pragma: no cover
    sys.exit(cli())
```

- [ ] **Step 4: Run tests to verify they pass**

Run:
```bash
cd /Users/drucev/projects/news_agent
pytest tests/test_step_status.py -v
```
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
cd /Users/drucev/projects/news_agent
git add lib/steps/status.py tests/test_step_status.py
git commit -m "feat(steps): news:status reports workflow progress"
```

---

## Task 6: Write `lib/steps/sessions.py` — `news:sessions` Python helper

**Files:**
- Create: `lib/steps/sessions.py`
- Create: `tests/test_step_sessions.py`

- [ ] **Step 1: Write the failing tests**

Create `/Users/drucev/projects/news_agent/tests/test_step_sessions.py`:
```python
from click.testing import CliRunner
from lib.steps.init import cli as init_cli
from lib.steps.sessions import cli as sessions_cli
from lib.db import init_db


def test_sessions_empty_db(tmp_db):
    init_db(tmp_db)
    runner = CliRunner()
    result = runner.invoke(sessions_cli, ["--db", tmp_db])
    assert result.exit_code == 0
    assert "no sessions" in result.output.lower()


def test_sessions_lists_created_sessions(tmp_db, sample_sources_yaml):
    init_db(tmp_db)
    runner = CliRunner()
    runner.invoke(init_cli, [
        "--db", tmp_db, "--sources", sample_sources_yaml, "--session", "alpha",
    ])
    runner.invoke(init_cli, [
        "--db", tmp_db, "--sources", sample_sources_yaml, "--session", "beta",
    ])
    result = runner.invoke(sessions_cli, ["--db", tmp_db])
    assert result.exit_code == 0
    assert "alpha" in result.output
    assert "beta" in result.output


def test_sessions_limit_flag(tmp_db, sample_sources_yaml):
    init_db(tmp_db)
    runner = CliRunner()
    for name in ["a", "b", "c"]:
        runner.invoke(init_cli, [
            "--db", tmp_db, "--sources", sample_sources_yaml, "--session", name,
        ])
    result = runner.invoke(sessions_cli, ["--db", tmp_db, "--limit", "2"])
    # Parse the data rows (skip header and separator)
    data_lines = [
        ln for ln in result.output.splitlines()
        if ln and not ln.startswith("SESSION") and not ln.startswith("-")
    ]
    shown = [ln.split()[0] for ln in data_lines if ln.split()]
    assert "c" in shown
    assert "b" in shown
    assert "a" not in shown  # oldest dropped by --limit 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
cd /Users/drucev/projects/news_agent
pytest tests/test_step_sessions.py -v
```
Expected: errors with "No module named 'lib.steps.sessions'".

- [ ] **Step 3: Implement `lib/steps/sessions.py`**

Create `/Users/drucev/projects/news_agent/lib/steps/sessions.py`:
```python
"""news:sessions — list recent sessions in the database.

CLI entry: python -m lib.steps.sessions [--db PATH] [--limit N]
"""
from __future__ import annotations

import sqlite3
import sys

import click

from lib.db import AgentState
from lib.state import NewsletterAgentState


@click.command()
@click.option("--db", "db_path", default="newsletter_agent.db",
              help="Path to SQLite database")
@click.option("--limit", "limit", default=10, type=int,
              help="Max sessions to display")
def cli(db_path: str, limit: int) -> None:
    """List the most recent sessions."""
    recents = NewsletterAgentState.list_recent_sessions(db_path, limit=limit)
    if not recents:
        click.echo("No sessions found.")
        return

    click.echo(f"{'SESSION':<28} {'UPDATED':<26} {'STEP':<14} PROGRESS")
    click.echo("-" * 80)
    with sqlite3.connect(db_path) as conn:
        for sid in recents:
            latest = AgentState.get_latest_by_session(conn, sid)
            if latest is None:
                continue
            state = NewsletterAgentState(session_id=sid, db_path=db_path)
            loaded = state.load_latest_from_db()
            pct = f"{loaded.get_progress_percentage():.0f}%" if loaded else "?"
            cur = loaded.get_current_step() if loaded else "?"
            updated = latest.updated_at.isoformat() if latest.updated_at else "?"
            click.echo(f"{sid:<28} {updated:<26} {cur or 'done':<14} {pct}")


if __name__ == "__main__":  # pragma: no cover
    sys.exit(cli())
```

- [ ] **Step 4: Run tests to verify they pass**

Run:
```bash
cd /Users/drucev/projects/news_agent
pytest tests/test_step_sessions.py -v
```
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
cd /Users/drucev/projects/news_agent
git add lib/steps/sessions.py tests/test_step_sessions.py
git commit -m "feat(steps): news:sessions lists recent sessions"
```

---

## Task 7: Write `plugin.json` (publishable-shaped manifest)

**Files:**
- Create: `plugin.json`

- [ ] **Step 1: Write `plugin.json`**

Create `/Users/drucev/projects/news_agent/plugin.json`:
```json
{
  "name": "news-agent",
  "version": "0.1.0",
  "description": "Daily AI newsletter agent: gather, filter, summarize, rate, cluster, and draft an AI-focused newsletter from RSS/HTML/REST sources.",
  "author": {
    "name": "Druce Vertes",
    "email": "drucev@gmail.com"
  },
  "license": "MIT",
  "homepage": "https://github.com/druce/news_agent",
  "repository": {
    "type": "git",
    "url": "https://github.com/druce/news_agent.git"
  },
  "keywords": ["newsletter", "ai", "news", "rss", "summarization"],
  "claude_code": {
    "min_version": "0.1.0"
  },
  "skills": [
    "skills/init",
    "skills/status",
    "skills/sessions"
  ]
}
```

- [ ] **Step 2: Commit**

```bash
cd /Users/drucev/projects/news_agent
git add plugin.json
git commit -m "feat: plugin.json manifest (publishable shape, 3 Phase 0 skills)"
```

---

## Task 8: Write `skills/init/SKILL.md`

**Files:**
- Create: `skills/init/SKILL.md`

- [ ] **Step 1: Write `skills/init/SKILL.md`**

Create `/Users/drucev/projects/news_agent/skills/init/SKILL.md`:
```markdown
---
name: news:init
description: Create a new newsletter session — initialize SQLite DB if needed, load and validate sources.yaml, and register the 11-step workflow. First step of /news:run. Idempotent for a given --session SID.
---

# news:init

Creates a new newsletter session and writes its initial state to `newsletter_agent.db`.

## When to use

- At the start of `/news:run` (orchestrator calls this first).
- Standalone to create a session you'll resume later via `/news:run --resume`.

## How to invoke

```bash
python -m lib.steps.init [--db newsletter_agent.db] [--sources sources.yaml] [--session SID]
```

- `--db` (default `newsletter_agent.db`): SQLite path. Created if missing.
- `--sources` (default `sources.yaml`): YAML with source configs. Must exist.
- `--session`: Session ID. Autogenerated as `YYYY-MM-DD-HHMMSS` if omitted.

## What it does

1. `init_db()` — creates tables (`urls`, `articles`, `sites`, `newsletters`, `agent_state`) if missing.
2. Loads `sources.yaml` and validates it's a non-empty mapping under a top-level `sources:` key (or top-level mapping).
3. Constructs a `NewsletterAgentState` with the 11 workflow steps in NOT_STARTED.
4. Marks the `init` step COMPLETE, writes the state to `agent_state` keyed by `(session_id, "init")`.
5. Prints a dry-run summary: session id, source count, enabled/disabled per source, step count.

## Output contract

- Exit code 0 on success.
- New row in `agent_state` with `step_name = "init"`.
- The next workflow step (`gather`) will see all later steps as NOT_STARTED.

## Errors

- Missing/invalid `sources.yaml` → exit non-zero with "sources" in the message.
```

- [ ] **Step 2: Commit**

```bash
cd /Users/drucev/projects/news_agent
git add skills/init/SKILL.md
git commit -m "docs(skills): news:init SKILL.md contract"
```

---

## Task 9: Write `skills/status/SKILL.md`

**Files:**
- Create: `skills/status/SKILL.md`

- [ ] **Step 1: Write `skills/status/SKILL.md`**

Create `/Users/drucev/projects/news_agent/skills/status/SKILL.md`:
```markdown
---
name: news:status
description: Report workflow progress for the current or specified newsletter session — step list, status per step, error messages, headline/cluster/section counts. Defaults to the most recent session.
---

# news:status

Reads the latest checkpoint row for a session and prints a human-readable progress report.

## How to invoke

```bash
python -m lib.steps.status [--db newsletter_agent.db] [--session SID]
```

- `--session` defaults to the most-recently-updated session in `agent_state`.

## Output

- Session id, overall progress %, next step
- Counts: headlines, clusters, sections
- Per-step table: index, name, status, status_message or error_message

## When to use

- After `/news:init` to confirm setup.
- During a run to see what's running / errored.
- Before `/news:resume` to decide what to re-execute.

## No-op cases

- Empty DB or no sessions → prints "No sessions found." and exits 0.
- Session exists but no checkpoint rows → prints "No state found for session SID."
```

- [ ] **Step 2: Commit**

```bash
cd /Users/drucev/projects/news_agent
git add skills/status/SKILL.md
git commit -m "docs(skills): news:status SKILL.md contract"
```

---

## Task 10: Write `skills/sessions/SKILL.md`

**Files:**
- Create: `skills/sessions/SKILL.md`

- [ ] **Step 1: Write `skills/sessions/SKILL.md`**

Create `/Users/drucev/projects/news_agent/skills/sessions/SKILL.md`:
```markdown
---
name: news:sessions
description: List the N most recent newsletter sessions in the database with session id, last-updated timestamp, current step, and completion percentage.
---

# news:sessions

Browse recent sessions in `newsletter_agent.db`.

## How to invoke

```bash
python -m lib.steps.sessions [--db newsletter_agent.db] [--limit 10]
```

## Output

Four-column table:

| SESSION | UPDATED | STEP | PROGRESS |
|---|---|---|---|
| 2026-05-17-120000 | 2026-05-17T12:01:33 | gather | 9% |
| 2026-05-16-093015 | 2026-05-16T10:42:11 | done | 100% |

## When to use

- Find a session id to feed to `/news:resume`, `/news:status`, or `/news:show`.
- Audit recent runs at a glance.

## No-op cases

- Empty DB → "No sessions found." exit 0.
```

- [ ] **Step 2: Commit**

```bash
cd /Users/drucev/projects/news_agent
git add skills/sessions/SKILL.md
git commit -m "docs(skills): news:sessions SKILL.md contract"
```

---

## Task 11: End-to-end Phase 0 verification

**Files:** (no edits — verification only)

- [ ] **Step 1: Full test suite passes**

Run:
```bash
cd /Users/drucev/projects/news_agent
pytest tests/ -v --cov=lib --cov-report=term-missing
```
Expected: all tests pass; coverage on `lib/db.py` and `lib/state.py` ≥ 80%.

- [ ] **Step 2: Manual smoke test — create + inspect two sessions**

Run:
```bash
cd /Users/drucev/projects/news_agent
rm -f scratch.db
python -m lib.steps.init --db scratch.db --sources sources.yaml --session smoke-1
python -m lib.steps.init --db scratch.db --sources sources.yaml --session smoke-2
python -m lib.steps.sessions --db scratch.db
python -m lib.steps.status --db scratch.db
python -m lib.steps.status --db scratch.db --session smoke-1
rm -f scratch.db
```
Expected:
- `init` calls print "Session created: smoke-N" and a source summary.
- `sessions` lists both `smoke-1` and `smoke-2`, newest first.
- `status` (no --session) reports on `smoke-2` (most recent).
- `status --session smoke-1` reports on `smoke-1` with `init` complete and `gather` as next.

- [ ] **Step 3: Verify CLAUDE_REFACTOR §Verification Phase 0 sanity checks pass**

From [CLAUDE_REFACTOR.md §Verification](../../../CLAUDE_REFACTOR.md):
> `news:init` creates a session; `news:sessions` lists it; `news:status` reports 0% progress and the first step as next. Re-running `news:init` creates a second session, both visible in `news:sessions`.

Confirm by re-reading the Step 2 output. Note: in Phase 0, `init` is itself a workflow step that gets marked COMPLETE, so progress will be `1/11 = 9.1%` (not 0%) after init. This is intentional and a small deviation from the verification doc — update the doc later if we decide otherwise.

- [ ] **Step 4: Commit anything residual and tag**

```bash
cd /Users/drucev/projects/news_agent
git status   # should be clean
git tag phase-0-complete
```

---

## Notes for the implementer

- **TDD discipline:** Always run the failing test BEFORE writing the implementation. The "expected fail" output proves the test actually exercises the code path.
- **Click vs argparse:** Click is used throughout for consistent `--help`, `CliRunner` testability, and ergonomic flag handling.
- **Imports:** Use `from lib.db import ...` (absolute). Don't add relative imports — they break when running modules with `python -m lib.steps.init`.
- **Don't port what you don't need:** `print_status`, `print_workflow_status`, `display_newsletter`, pandas DataFrame helpers from legacy `newsletter_state.py` are intentionally omitted in Phase 0. Add them only when a later step needs them.
- **Legacy file is read-only:** Reference `~/projects/OpenAIAgentsSDK/newsletter_state.py` and `~/projects/OpenAIAgentsSDK/db.py` to understand semantics; never import from them and never modify them.

## What this phase explicitly does NOT include

- `lib/llm.py` / `call_prompt` (Phase 1)
- Any LLM calls or subagents (Phase 1+)
- `gather`/`download`/`send` (Phase 2)
- Show/resume/reset/checkpoint/diff/gc skills (Phase 2 onward)
- `umap_reducer.pkl` copy (Phase 4 — user has authorized copying from legacy when we get there)
- Bluesky (Phase 7)
