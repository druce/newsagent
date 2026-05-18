import importlib
import pytest
import lib.prompts  # triggers registration of all prompts
from lib.llm import get_prompt, _registry


@pytest.fixture(autouse=True)
def _ensure_registered():
    if "critique_section" not in _registry:
        mod = importlib.import_module("lib.prompts.critique_section")
        importlib.reload(mod)
    yield


def test_critique_section_registered():
    cfg = get_prompt("critique_section")
    assert cfg.name == "critique_section"
    assert cfg.default_engine == "subagent"
    assert cfg.reasoning_effort == 8


def test_critique_section_output_schema_validates():
    from lib.prompts._critic_schemas import CritiqueResult
    result = CritiqueResult(score=7.5, feedback="Improve headline clarity", accept=False)
    assert result.score == 7.5
    assert result.accept is False


def test_critique_section_system_prompt_quality_phrases():
    cfg = get_prompt("critique_section")
    sp = cfg.system_prompt.lower()
    assert "sentence case" in sp
    assert "headline" in sp
    assert "25 words" in sp
