"""Result schemas for Agent-dispatch draft/rewrite outputs.

The Agent runs the full critic-optimizer loop inside its own context, so its
single result file must capture the entire transcript (final draft + per-
iteration scores and feedbacks).
"""
from __future__ import annotations

from typing import List

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
