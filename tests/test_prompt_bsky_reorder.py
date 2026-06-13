"""Tests for lib/prompts/bsky_reorder.py — BSKY_REORDER prompt."""
import importlib
import pytest
import lib.prompts  # triggers registration of all prompts
from lib.llm import get_prompt, _registry


@pytest.fixture(autouse=True)
def _ensure_registered():
    if "bsky_reorder" not in _registry:
        mod = importlib.import_module("lib.prompts.bsky_reorder")
        importlib.reload(mod)
    yield


def test_bsky_reorder_registered():
    cfg = get_prompt("bsky_reorder")
    assert cfg.name == "bsky_reorder"
    assert cfg.default_engine == "subagent"
    assert cfg.reasoning_effort == 4


def test_bsky_reorder_output_schema():
    from lib.prompts.bsky_reorder import BskyReorderOutput, BskyReorderGroup

    result = BskyReorderOutput(
        groups=[
            BskyReorderGroup(label="OpenAI", indexes=[3, 1]),
            BskyReorderGroup(label="Hardware", indexes=[2, 0]),
        ]
    )
    # groups are ordered most→least important; indexes flatten to the full order
    assert [i for g in result.groups for i in g.indexes] == [3, 1, 2, 0]
    assert result.groups[0].label == "OpenAI"


def test_bsky_reorder_system_prompt_mentions_importance_and_order():
    cfg = get_prompt("bsky_reorder")
    system_lower = cfg.system_prompt.lower()
    assert "important" in system_lower or "importance" in system_lower
    assert "order" in system_lower


def test_bsky_reorder_system_prompt_mentions_grouping_and_factors():
    """The full port carries the topical-grouping instruction + importance factors."""
    cfg = get_prompt("bsky_reorder")
    system_lower = cfg.system_prompt.lower()
    assert "group" in system_lower
    # a few of the legacy 13 importance factors should be present
    assert "magnitude" in system_lower
    assert "novelty" in system_lower
