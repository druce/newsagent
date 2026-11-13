"""Eval-only coverage_boost_v4: three-way same / related / different."""
import pytest
from pydantic import ValidationError

import evals.prompts  # noqa: F401  (registers eval-only prompts)
from evals.run import challenge as R
from lib.llm import get_prompt
from lib.prompts import coverage_boost as P


def test_v4_shares_input_schema_and_renders_pairs():
    v4 = get_prompt("coverage_boost_v4")
    assert v4.input_schema is P.CoverageBoostInput
    inp = v4.input_schema.model_validate({"pairs": [
        {"id": "0", "a_title": "A t", "a_summary": "- a", "b_title": "B t", "b_summary": "- b"}]})
    assert '"a_title": "A t"' in v4.user_prompt.format(**inp.model_dump())


def test_v4_prompt_keeps_current_same_definition_and_defines_related():
    s = get_prompt("coverage_boost_v4").system_prompt
    assert "someone who read only A would already" in s       # production's "same" test
    assert "related" in s and "name in a phrase" in s


def test_v4_output_relation_enum_and_same_property():
    out = get_prompt("coverage_boost_v4").output_schema
    parsed = out.model_validate({"results": [{"id": "0", "relation": "related"},
                                             {"id": "1", "relation": "same"}]})
    assert [r.same for r in parsed.results] == [False, True]
    with pytest.raises(ValidationError):
        out.model_validate({"results": [{"id": "0", "relation": "maybe"}]})


def test_parse_labels_v4_gives_same_and_relation():
    out = {"results": [{"id": "0", "relation": "related"}, {"id": "1", "relation": "same"}]}
    assert R.parse_labels("coverage_boost_v4", out) == {
        "0": {"same": False, "relation": "related"}, "1": {"same": True, "relation": "same"}}
    assert R.ITEM_KEY["coverage_boost_v4"] == "pairs"
