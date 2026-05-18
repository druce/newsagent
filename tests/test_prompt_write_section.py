import importlib
import pytest
import lib.prompts  # triggers registration of all prompts
from lib.llm import get_prompt, _registry


@pytest.fixture(autouse=True)
def _ensure_registered():
    if "write_section" not in _registry:
        mod = importlib.import_module("lib.prompts.write_section")
        importlib.reload(mod)
    yield


def test_write_section_registered():
    cfg = get_prompt("write_section")
    assert cfg.name == "write_section"
    assert cfg.default_engine == "subagent"
    assert cfg.reasoning_effort == 8


def test_write_section_engine_and_effort():
    cfg = get_prompt("write_section")
    assert cfg.default_engine == "subagent"
    assert cfg.reasoning_effort == 8


def test_write_section_system_prompt_key_phrases():
    cfg = get_prompt("write_section")
    sp = cfg.system_prompt.lower()
    assert "headline" in sp
    assert "section" in sp
    assert "sentence case" in sp
