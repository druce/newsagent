import importlib
import pytest
import lib.prompts  # triggers registration of all prompts
from lib.llm import get_prompt, _registry


@pytest.fixture(autouse=True)
def _ensure_registered():
    if "name_topic" not in _registry:
        mod = importlib.import_module("lib.prompts.name_topic")
        importlib.reload(mod)
    yield


def test_name_topic_registered():
    cfg = get_prompt("name_topic")
    assert cfg.default_engine == "subagent"
    assert cfg.reasoning_effort == 3


def test_name_topic_input_accepts_string_fields():
    cfg = get_prompt("name_topic")
    parsed = cfg.input_schema.model_validate({
        "entities": "OpenAI, Anthropic, Google",
        "headlines": "OpenAI releases GPT-5\nAnthropic launches Claude 4",
    })
    assert parsed.entities == "OpenAI, Anthropic, Google"
    assert "GPT-5" in parsed.headlines


def test_name_topic_system_prompt_has_key_phrases():
    cfg = get_prompt("name_topic")
    assert "section title" in cfg.system_prompt.lower()
    assert "headlines" in cfg.system_prompt.lower()
