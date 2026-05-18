"""Prompt registry — importing this package registers all prompts."""
from lib.prompts import filter_urls, extract_summaries  # noqa: F401
from lib.prompts import rate_quality, rate_on_topic, rate_importance  # noqa: F401
from lib.prompts import battle  # noqa: F401
from lib.prompts import name_topic  # noqa: F401
from lib.prompts import assign_noise  # noqa: F401

__all__ = [
    "filter_urls",
    "extract_summaries",
    "rate_quality",
    "rate_on_topic",
    "rate_importance",
    "battle",
    "name_topic",
    "assign_noise",
]
