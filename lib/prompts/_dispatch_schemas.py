"""Result schemas for Agent-dispatch draft/rewrite outputs.

The Agent runs the full critic-optimizer loop inside its own context, so its
single result file must capture the entire transcript (final draft + per-
iteration scores and feedbacks).
"""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel


class DraftSectionResult(BaseModel):
    cat: str
    final_section_markdown: str
    iterations: int
    scores: List[float]
    feedbacks: List[str]
    accepted: bool


class RewriteResult(BaseModel):
    final_newsletter_markdown: str
    title: str
    iterations: int
    scores: List[float]
    feedbacks: List[str]
    accepted: bool


# ── newsagent:edition ────────────────────────────────────────────────────────

class EditionItem(BaseModel):
    id: int                      # published_articles.id — URL is looked up by it
    kicker: str
    summary: str
    source: str
    alt_kickers: List[str]
    flag: Optional[str] = None   # e.g. "possible duplicate of 123"; never a drop


class EditionSection(BaseModel):
    cat: str
    items: List[EditionItem]
    title: str = ""              # printed `## title`; written by the final edit only
    title_alts: List[str] = []


class EditionIssue(BaseModel):
    subtitle: str
    subtitle_alts: List[str]
    clusters: List[EditionSection]
    notes: List[str] = []


class EditionMergeAssignment(BaseModel):
    id: int                      # published_articles.id
    cat: str                     # merged working cluster name (not printed)


class EditionDuplicate(BaseModel):
    ids: List[int]               # 2+ ids that report the same specific news
    reason: str


class EditionMerge(BaseModel):
    """newsagent:edition merge step: one cluster per story, across sessions."""
    assignments: List[EditionMergeAssignment]
    duplicates: List[EditionDuplicate] = []
    notes: List[str] = []
