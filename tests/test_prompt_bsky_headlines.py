"""Tests for lib/prompts/bsky_headlines.py — BSKY_HEADLINES prompt."""
import importlib
import lib.prompts  # triggers registration of all prompts
from lib.llm import get_prompt, _registry


def _ensure_registered():
    if "bsky_headlines" not in _registry:
        mod = importlib.import_module("lib.prompts.bsky_headlines")
        importlib.reload(mod)


def test_bsky_headlines_registered():
    _ensure_registered()
    cfg = get_prompt("bsky_headlines")
    assert cfg.name == "bsky_headlines"
    assert cfg.default_engine == "subagent"
    assert cfg.reasoning_effort == 4


def test_bsky_headlines_output_schema():
    from lib.prompts.bsky_headlines import BskyHeadlinesOutput
    result = BskyHeadlinesOutput(headlines=["Sue-perintelligence", "Chips Ahoy"])
    assert len(result.headlines) == 2


def test_bsky_headlines_input_carries_each_headline():
    from lib.prompts.bsky_headlines import BskyHeadlinesInput
    inp = BskyHeadlinesInput(headlines=["OpenAI ships GPT-5", "Nvidia H200 lands"])
    assert "OpenAI ships GPT-5" in inp.headlines_json
    assert "Nvidia H200 lands" in inp.headlines_json


def test_bsky_headlines_system_prompt_is_punny_headline_rewrites():
    cfg = get_prompt("bsky_headlines")
    system_lower = cfg.system_prompt.lower()
    assert "pun" in system_lower or "punny" in system_lower
    assert "headline" in system_lower
    # rewrites headlines, NOT section titles
    assert "section" not in system_lower


def test_bsky_headlines_carries_editorial_voice():
    """The port keeps the legacy witty/irreverent register and example puns."""
    cfg = get_prompt("bsky_headlines")
    system = cfg.system_prompt
    assert "irreverent" in system.lower() or "witty" in system.lower()
    assert "Sue-perintelligence" in system or "Tim Cooked" in system
