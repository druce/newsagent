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
        "summaries": [{"id": "h1", "summary": "- bullet one\n- bullet two\n- bullet three"}]
    })
    assert len(out.summaries) == 1
