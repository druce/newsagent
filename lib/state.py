"""Pydantic workflow state for newsagent.

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
    ("start",     "Initialize",        "Create session and validate sources"),
    ("gather",    "Gather URLs",       "Fetch headlines/URLs from configured sources"),
    ("filter",    "Filter URLs",       "Classify AI-relevance and drop dupes vs DB"),
    ("download",  "Download Articles", "Fetch full article HTML and extract text"),
    ("dedupe",    "Dedupe Articles",   "Cosine-similarity dedup on full article bodies"),
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
            lines.append(f"  Step {i}: {step.id} ({step.name}): {step.status.value}")
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
