import importlib
import pytest
import lib.prompts  # triggers registration of all prompts
from lib.llm import get_prompt, _registry


@pytest.fixture(autouse=True)
def _ensure_registered():
    if "improve_newsletter" not in _registry:
        mod = importlib.import_module("lib.prompts.improve_newsletter")
        importlib.reload(mod)
    yield


def test_improve_newsletter_registered():
    cfg = get_prompt("improve_newsletter")
    assert cfg.name == "improve_newsletter"
    assert cfg.default_engine == "subagent"
    assert cfg.reasoning_effort == 6


def test_improve_newsletter_schemas():
    from lib.prompts._critic_schemas import NewsletterDraft
    draft = NewsletterDraft(newsletter_markdown="# AI Daily\n\n## AI Models\n- GPT-5 launches — [OpenAI](https://openai.com)")
    assert "# AI Daily" in draft.newsletter_markdown


def test_improve_newsletter_system_prompt_forbids_new_info():
    cfg = get_prompt("improve_newsletter")
    sp = cfg.system_prompt.lower()
    assert "do not" in sp or "not introduce" in sp or "inappropriate" in sp
    assert "link" in sp or "url" in sp
