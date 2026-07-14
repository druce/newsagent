import importlib
import pytest
from lib.llm import get_prompt, _registry


@pytest.fixture(autouse=True)
def _ensure_registered():
    if "extract_summaries" not in _registry:
        import lib.prompts.extract_summaries
        importlib.reload(lib.prompts.extract_summaries)
    yield


def test_extract_summaries_registered():
    cfg = get_prompt("extract_summaries")
    assert cfg.default_engine == "subagent"
    assert cfg.reasoning_effort == 6  # legacy value


def test_extract_summaries_input_accepts_articles():
    cfg = get_prompt("extract_summaries")
    parsed = cfg.input_schema.model_validate({
        "items": [{"id": "h1", "title": "T", "text": "Article body here..."}]
    })
    assert parsed.items[0].text == "Article body here..."


def test_extract_summaries_output_round_trips():
    cfg = get_prompt("extract_summaries")
    out = cfg.output_schema.model_validate({
        "summaries": [{
            "id": "h1",
            "short_summary": "OpenAI launches GPT-6 with strong benchmarks",
            "summary": "- bullet one\n- bullet two\n- bullet three",
        }]
    })
    assert len(out.summaries) == 1
    assert out.summaries[0].short_summary.startswith("OpenAI")


def test_extract_summaries_bans_outlet_and_medium_mentions():
    """User feedback 2026-07-14: headlines must never mention the medium
    ('Blog post urges...') or credit the reporting outlet ('..., Washington
    Post reports') — the source name is rendered separately after the dash."""
    system = get_prompt("extract_summaries").system_prompt

    # Leading medium framing is called out with concrete banned examples.
    assert "Blog post urges" in system
    assert "Article analyzes" in system
    # Trailing outlet credit is called out too.
    assert "Washington Post reports" in system
    # The named-person/institution exception is documented.
    assert "Exception" in system
