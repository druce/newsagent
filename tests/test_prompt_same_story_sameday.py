import lib.prompts  # noqa: F401 — registers same_story_sameday
from lib.llm import get_prompt
from lib.prompts.same_story_sameday import (
    SameStorySamedayInput,
    SameStorySamedayOutput,
)


def test_prompt_registered_with_cheap_engine():
    cfg = get_prompt("same_story_sameday")
    assert cfg.name == "same_story_sameday"
    assert cfg.default_engine == "google:gemini-3.1-flash-lite"


def test_input_renders_full_summaries_into_user_prompt():
    cfg = get_prompt("same_story_sameday")
    validated = SameStorySamedayInput.model_validate({
        "pairs": [{
            "id": "3-7",
            "a_title": "OpenAI delays IPO",
            "a_summary": "- OpenAI pushed its IPO to next year\n- Valuation questioned",
            "b_title": "OpenAI listing slips",
            "b_summary": "- The OpenAI public offering is delayed\n- Investors cautious",
        }],
    })
    user = cfg.user_prompt.format(**validated.model_dump())
    assert "OpenAI pushed its IPO" in user
    assert "Investors cautious" in user


def test_output_schema_roundtrip():
    out = SameStorySamedayOutput.model_validate(
        {"results": [{"id": "3-7", "same": True}]}
    )
    assert out.results[0].id == "3-7"
    assert out.results[0].same is True
