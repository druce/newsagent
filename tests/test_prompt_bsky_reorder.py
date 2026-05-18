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
    from lib.prompts.bsky_reorder import BskyReorderOutput
    result = BskyReorderOutput(indexes=[3, 1, 2, 0])
    assert result.indexes == [3, 1, 2, 0]


def test_bsky_reorder_system_prompt_mentions_importance_and_order():
    cfg = get_prompt("bsky_reorder")
    system_lower = cfg.system_prompt.lower()
    assert "important" in system_lower or "importance" in system_lower
    assert "order" in system_lower
