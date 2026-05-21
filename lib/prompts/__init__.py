"""Prompt registry — importing this package registers all prompts."""
from lib.prompts import filter_urls, extract_summaries  # noqa: F401
from lib.prompts import rate_quality, rate_on_topic, rate_importance  # noqa: F401
from lib.prompts import battle  # noqa: F401
from lib.prompts import name_topic  # noqa: F401
from lib.prompts import name_topic_batch  # noqa: F401
from lib.prompts import assign_noise  # noqa: F401
from lib.prompts import merge_clusters  # noqa: F401
from lib.prompts import write_section  # noqa: F401
from lib.prompts import critique_section  # noqa: F401
from lib.prompts import improve_section  # noqa: F401
from lib.prompts import critique_newsletter  # noqa: F401
from lib.prompts import improve_newsletter  # noqa: F401
from lib.prompts import generate_title  # noqa: F401
from lib.prompts import bsky_reorder  # noqa: F401
from lib.prompts import bsky_section_titles  # noqa: F401
from lib.prompts import sitename  # noqa: F401

__all__ = [
    "filter_urls",
    "extract_summaries",
    "rate_quality",
    "rate_on_topic",
    "rate_importance",
    "battle",
    "name_topic",
    "name_topic_batch",
    "assign_noise",
    "merge_clusters",
    "write_section",
    "critique_section",
    "improve_section",
    "critique_newsletter",
    "improve_newsletter",
    "generate_title",
    "bsky_reorder",
    "bsky_section_titles",
    "sitename",
]
