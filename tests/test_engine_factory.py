import pytest
from lib.engines import get_engine, EngineError


def test_get_engine_subagent():
    assert callable(get_engine("subagent"))


def test_get_engine_openrouter_requires_model():
    with pytest.raises(EngineError, match="model id"):
        get_engine("openrouter:")


def test_get_engine_openrouter_with_model():
    assert callable(get_engine("openrouter:google/gemini-2.5-flash"))


def test_get_engine_unknown():
    with pytest.raises(EngineError, match="Unknown"):
        get_engine("anthropic:claude-opus-4-7")


def test_get_engine_openai_with_model():
    assert callable(get_engine("openai:gpt-4o-mini"))


def test_get_engine_openai_requires_model():
    with pytest.raises(EngineError, match="model id"):
        get_engine("openai:")


def test_get_engine_google_with_model():
    assert callable(get_engine("google:gemini-2.5-flash"))


def test_get_engine_anthropic_still_unknown():
    """Anthropic direct is explicitly NOT supported — must error."""
    with pytest.raises(EngineError, match="Unknown"):
        get_engine("anthropic:claude-opus-4-7")
