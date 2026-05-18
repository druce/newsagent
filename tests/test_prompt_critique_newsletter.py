import importlib
import pytest
import lib.prompts  # triggers registration of all prompts
from lib.llm import get_prompt, _registry


@pytest.fixture(autouse=True)
def _ensure_registered():
    if "critique_newsletter" not in _registry:
        mod = importlib.import_module("lib.prompts.critique_newsletter")
        importlib.reload(mod)
    yield


def test_critique_newsletter_registered():
    cfg = get_prompt("critique_newsletter")
    assert cfg.name == "critique_newsletter"
    assert cfg.default_engine == "subagent"
    assert cfg.reasoning_effort == 8


def test_critique_newsletter_output_schema():
    from lib.prompts._critic_schemas import CritiqueResult
    result = CritiqueResult(score=8.5, feedback="Title too vague", accept=True)
    assert result.score == 8.5
    assert result.accept is True


def test_critique_newsletter_system_prompt_rubric():
    cfg = get_prompt("critique_newsletter")
    sp = cfg.system_prompt.lower()
    # Must mention rubric dimensions from legacy: title, structure, section, headline
    assert "title" in sp
    assert "structure" in sp
    assert "headline" in sp
    assert "section" in sp
