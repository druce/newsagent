"""Prompt registry — importing this package registers all prompts."""
from lib.prompts import filter_urls, extract_summaries  # noqa: F401

__all__ = ["filter_urls", "extract_summaries"]
