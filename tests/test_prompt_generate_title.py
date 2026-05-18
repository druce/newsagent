import importlib
import pytest
import lib.prompts  # triggers registration of all prompts
from lib.llm import get_prompt, _registry


@pytest.fixture(autouse=True)
def _ensure_registered():
    if "generate_newsletter_title" not in _registry:
        mod = importlib.import_module("lib.prompts.generate_title")
        importlib.reload(mod)
    yield


def test_generate_title_registered():
    cfg = get_prompt("generate_newsletter_title")
    assert cfg.name == "generate_newsletter_title"
    assert cfg.default_engine == "subagent"
    assert cfg.reasoning_effort == 8


def test_generate_title_output_schema():
    from lib.prompts.generate_title import NewsletterTitle
    title = NewsletterTitle(title="OpenAI Challenges Microsoft; Nvidia Unveils New Chips")
    assert "OpenAI" in title.title
