"""Prompt registry — importing this package registers all prompts."""
from lib.prompts import filter_urls, extract_summaries  # noqa: F401
from lib.prompts import rate_quality, rate_on_topic, rate_importance  # noqa: F401
from lib.prompts import battle  # noqa: F401
from lib.prompts import name_topic  # noqa: F401
from lib.prompts import assign_noise  # noqa: F401
from lib.prompts import merge_clusters  # noqa: F401
from lib.prompts import write_section  # noqa: F401
from lib.prompts import critique_section  # noqa: F401
from lib.prompts import improve_section  # noqa: F401

__all__ = [
    "filter_urls",
    "extract_summaries",
    "rate_quality",
    "rate_on_topic",
    "rate_importance",
    "battle",
    "name_topic",
    "assign_noise",
    "merge_clusters",
    "write_section",
    "critique_section",
    "improve_section",
]
