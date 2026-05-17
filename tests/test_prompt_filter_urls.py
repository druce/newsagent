import importlib
import pytest
from lib.llm import get_prompt, _registry
import lib.prompts  # triggers registration


@pytest.fixture(autouse=True)
def _ensure_registered():
    if "filter_urls" not in _registry:
        # Other tests may have cleared the registry; re-trigger registration.
        import lib.prompts.filter_urls  # noqa: F401
        importlib.reload(lib.prompts.filter_urls)
    yield


def test_filter_urls_registered():
    cfg = get_prompt("filter_urls")
    assert cfg is not None
    assert cfg.default_engine == "subagent"


def test_filter_urls_input_schema_accepts_list_of_headlines():
    cfg = get_prompt("filter_urls")
    valid = {"items": [{"id": "h1", "title": "GPT-6 released"}]}
    parsed = cfg.input_schema.model_validate(valid)
    assert parsed.items[0].title == "GPT-6 released"


def test_filter_urls_output_schema_round_trips():
    cfg = get_prompt("filter_urls")
    out = cfg.output_schema.model_validate({"classifications": [
        {"id": "h1", "is_ai": True},
        {"id": "h2", "is_ai": False},
    ]})
    assert len(out.classifications) == 2
    assert out.classifications[0].is_ai is True


def test_filter_urls_user_prompt_formats_items():
    cfg = get_prompt("filter_urls")
    user = cfg.user_prompt.format(input_text='[{"id":"h1","title":"x"}]')
    assert "h1" in user


def test_filter_urls_system_prompt_mentions_json():
    cfg = get_prompt("filter_urls")
    assert "JSON" in cfg.system_prompt or "json" in cfg.system_prompt.lower()


def test_filter_urls_has_reasoning_effort():
    cfg = get_prompt("filter_urls")
    assert cfg.reasoning_effort == 4
