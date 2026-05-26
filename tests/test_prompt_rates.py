"""Tests for RATE_QUALITY, RATE_ON_TOPIC, RATE_IMPORTANCE prompts."""
import importlib
import pytest
import lib.prompts  # triggers registration
from lib.llm import get_prompt, _registry


@pytest.fixture(autouse=True)
def _ensure_registered():
    """Ensure all rating prompts are registered before each test."""
    for name in ("rate_quality", "rate_on_topic", "rate_importance"):
        if name not in _registry:
            module_map = {
                "rate_quality": "lib.prompts.rate_quality",
                "rate_on_topic": "lib.prompts.rate_on_topic",
                "rate_importance": "lib.prompts.rate_importance",
            }
            mod = importlib.import_module(module_map[name])
            importlib.reload(mod)
    yield


# ---------------------------------------------------------------------------
# Shared schema tests
# ---------------------------------------------------------------------------

def test_rating_input_schema_accepts_valid_items():
    from lib.prompts._rating_schemas import RatingInput
    data = {"items": [{"id": "a1", "input_text": "Title\nSummary text"}]}
    parsed = RatingInput.model_validate(data)
    assert parsed.items[0].id == "a1"
    assert "a1" in parsed.input_text  # computed_field serializes to JSON


def test_rating_input_schema_rejects_empty_list():
    from lib.prompts._rating_schemas import RatingInput
    with pytest.raises(Exception):
        RatingInput.model_validate({"items": []})


def test_rating_output_schema_accepts_confidence_list():
    from lib.prompts._rating_schemas import RatingOutput, StoryConfidence
    out = RatingOutput(results_list=[
        StoryConfidence(id="a1", confidence=0.8),
        StoryConfidence(id="a2", confidence=0.1),
    ])
    assert len(out.results_list) == 2
    assert out.results_list[0].confidence == 0.8


# ---------------------------------------------------------------------------
# RATE_QUALITY
# ---------------------------------------------------------------------------

def test_rate_quality_registered():
    cfg = get_prompt("rate_quality")
    assert cfg is not None


def test_rate_quality_default_engine():
    cfg = get_prompt("rate_quality")
    assert cfg.default_engine == "openai:gpt-4o-mini"


def test_rate_quality_reasoning_effort_omitted():
    # gpt-4o-mini doesn't honor reasoning_effort; the prompt must opt out
    # explicitly so openai_chat skips the kwarg instead of catch-and-retry.
    cfg = get_prompt("rate_quality")
    assert cfg.reasoning_effort is None


def test_rate_quality_system_prompt_mentions_low_quality():
    cfg = get_prompt("rate_quality")
    assert "low quality" in cfg.system_prompt.lower()


def test_rate_quality_schema_types():
    cfg = get_prompt("rate_quality")
    from lib.prompts._rating_schemas import RatingInput, RatingOutput
    assert cfg.input_schema is RatingInput
    assert cfg.output_schema is RatingOutput


# ---------------------------------------------------------------------------
# RATE_ON_TOPIC
# ---------------------------------------------------------------------------

def test_rate_on_topic_registered():
    cfg = get_prompt("rate_on_topic")
    assert cfg is not None


def test_rate_on_topic_default_engine():
    cfg = get_prompt("rate_on_topic")
    assert cfg.default_engine == "openai:gpt-4o-mini"


def test_rate_on_topic_reasoning_effort_omitted():
    cfg = get_prompt("rate_on_topic")
    assert cfg.reasoning_effort is None


def test_rate_on_topic_system_prompt_mentions_ai_news():
    cfg = get_prompt("rate_on_topic")
    # The system prompt should reference AI news topics
    assert "AI" in cfg.system_prompt or "ai" in cfg.system_prompt.lower()


def test_rate_on_topic_schema_types():
    cfg = get_prompt("rate_on_topic")
    from lib.prompts._rating_schemas import RatingInput, RatingOutput
    assert cfg.input_schema is RatingInput
    assert cfg.output_schema is RatingOutput


# ---------------------------------------------------------------------------
# RATE_IMPORTANCE
# ---------------------------------------------------------------------------

def test_rate_importance_registered():
    cfg = get_prompt("rate_importance")
    assert cfg is not None


def test_rate_importance_default_engine():
    cfg = get_prompt("rate_importance")
    assert cfg.default_engine == "openai:gpt-4o-mini"


def test_rate_importance_reasoning_effort_omitted():
    cfg = get_prompt("rate_importance")
    assert cfg.reasoning_effort is None


def test_rate_importance_system_prompt_mentions_importance():
    cfg = get_prompt("rate_importance")
    assert "importance" in cfg.system_prompt.lower() or "IMPORTANCE" in cfg.system_prompt


def test_rate_importance_schema_types():
    cfg = get_prompt("rate_importance")
    from lib.prompts._rating_schemas import RatingInput, RatingOutput
    assert cfg.input_schema is RatingInput
    assert cfg.output_schema is RatingOutput


# ---------------------------------------------------------------------------
# User prompt formatting
# ---------------------------------------------------------------------------

def test_rate_quality_user_prompt_formats_input_text():
    cfg = get_prompt("rate_quality")
    user = cfg.user_prompt.format(input_text="test content")
    assert "test content" in user


def test_rate_on_topic_user_prompt_formats_input_text():
    cfg = get_prompt("rate_on_topic")
    user = cfg.user_prompt.format(input_text="test content")
    assert "test content" in user


def test_rate_importance_user_prompt_formats_input_text():
    cfg = get_prompt("rate_importance")
    user = cfg.user_prompt.format(input_text="test content")
    assert "test content" in user
