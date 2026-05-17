"""Shared types for the fetch package."""
from __future__ import annotations

from typing import Optional
from pydantic import BaseModel, Field


class Article(BaseModel):
    """A headline returned by a source fetcher."""
    source: str
    title: str
    url: str
    published: Optional[str] = None
    rss_summary: Optional[str] = None


class FetchResult(BaseModel):
    """Outcome of fetching one source."""
    source: str
    articles: list[Article] = Field(default_factory=list)
    ok: bool
    error: Optional[str] = None
