"""Shared output schemas for the draft/rewrite critic-optimizer loop.

Used by:
- critique_section.py  (CritiqueResult)
- critique_newsletter.py  (CritiqueResult)
- write_section.py  (SectionDraft)
- improve_section.py  (SectionDraft)
- improve_newsletter.py  (NewsletterDraft)
"""
from __future__ import annotations

from pydantic import BaseModel


class CritiqueResult(BaseModel):
    score: float       # 0-10
    feedback: str      # what to fix
    accept: bool       # short-circuit signal


class SectionDraft(BaseModel):
    section_markdown: str


class NewsletterDraft(BaseModel):
    newsletter_markdown: str
