"""Shared Pydantic schemas for the three per-axis rating prompts.

All three prompts (RATE_QUALITY, RATE_ON_TOPIC, RATE_IMPORTANCE) are yes/no
predicates over ONE article, answered by the OpenAI Decisions API with a
probability. They share the same input/output structure — only the question
(the system prompt) differs.
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class RatingItemInput(BaseModel):
    """One article: its title, a newline, then its bullet summary."""
    input_text: str


class PredicateOutput(BaseModel):
    """Probability that the predicate (the prompt's question) is true."""
    probability: float = Field(ge=0.0, le=1.0)
