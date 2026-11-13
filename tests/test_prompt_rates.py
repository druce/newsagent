"""Tests for RATE_QUALITY, RATE_ON_TOPIC, RATE_IMPORTANCE prompts (Decisions API predicates)."""
import pytest
from pydantic import ValidationError

import lib.prompts  # noqa: F401 — triggers registration
from lib.llm import get_prompt
from lib.prompts._rating_schemas import PredicateOutput, RatingItemInput

NAMES = ("rate_quality", "rate_on_topic", "rate_importance")


@pytest.mark.parametrize("name", NAMES)
def test_bound_to_luna_on_decisions_api(name):
    cfg = get_prompt(name)
    assert cfg.default_engine == "openai-decisions:gpt-6-luna"
    assert cfg.reasoning_effort is None  # the Decisions API takes no effort knob


@pytest.mark.parametrize("name", NAMES)
def test_one_article_in_probability_out(name):
    cfg = get_prompt(name)
    assert cfg.input_schema is RatingItemInput
    assert cfg.output_schema is PredicateOutput
    assert cfg.user_prompt.format(input_text="Title\n- summary") == "Title\n- summary"


@pytest.mark.parametrize("name", NAMES)
def test_question_is_a_predicate_with_the_no_content_rule(name):
    q = get_prompt(name).system_prompt
    assert q.startswith("True if ")
    assert "judge by the title alone" in q


def test_quality_counts_stock_picks_that_cite_real_financials():
    assert "even when it cites real revenue" in get_prompt("rate_quality").system_prompt


def test_on_topic_follows_filter_editorial_policy():
    q = get_prompt("rate_on_topic").system_prompt
    for term in ("data centers", "AI chips (with an AI angle)", "wearables", "robotics"):
        assert term in q


def test_predicate_output_bounds():
    assert PredicateOutput(probability=0.0).probability == 0.0
    with pytest.raises(ValidationError):
        PredicateOutput(probability=1.2)
