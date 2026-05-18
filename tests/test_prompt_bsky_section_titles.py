"""Tests for lib/prompts/bsky_section_titles.py — BSKY_SECTION_TITLES prompt."""
import importlib
import pytest
import lib.prompts  # triggers registration of all prompts
from lib.llm import get_prompt, _registry


@pytest.fixture(autouse=True)
def _ensure_registered():
    if "bsky_section_titles" not in _registry:
        mod = importlib.import_module("lib.prompts.bsky_section_titles")
        importlib.reload(mod)
    yield


def test_bsky_section_titles_registered():
    cfg = get_prompt("bsky_section_titles")
    assert cfg.name == "bsky_section_titles"
    assert cfg.default_engine == "subagent"
    assert cfg.reasoning_effort == 4


def test_bsky_section_titles_output_schema():
    from lib.prompts.bsky_section_titles import BskySectionTitlesOutput
    result = BskySectionTitlesOutput(titles=["The AI Revolution Begins", "Models Gone Wild"])
    assert len(result.titles) == 2
    assert "AI" in result.titles[0]


def test_bsky_section_titles_system_prompt_mentions_punny_and_titles():
    cfg = get_prompt("bsky_section_titles")
    system_lower = cfg.system_prompt.lower()
    assert "title" in system_lower
    assert "pun" in system_lower or "wordplay" in system_lower or "punny" in system_lower
