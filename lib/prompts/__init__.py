"""Prompt registry — importing this package registers all prompts."""
from lib.prompts import filter_urls  # noqa: F401

__all__ = ["filter_urls"]
