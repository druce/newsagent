"""Prompt registry — importing this package registers all prompts."""
from lib.prompts import filter_urls, extract_summaries  # noqa: F401
from lib.prompts import rate_quality, rate_on_topic, rate_importance  # noqa: F401

__all__ = [
    "filter_urls",
    "extract_summaries",
    "rate_quality",
    "rate_on_topic",
    "rate_importance",
]
