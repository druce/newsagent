import importlib
import pytest
import lib.prompts  # triggers registration of all prompts
from lib.llm import get_prompt, _registry


@pytest.fixture(autouse=True)
def _ensure_registered():
    if "improve_section" not in _registry:
        mod = importlib.import_module("lib.prompts.improve_section")
        importlib.reload(mod)
    yield


def test_improve_section_registered():
    cfg = get_prompt("improve_section")
    assert cfg.name == "improve_section"
    assert cfg.default_engine == "subagent"
    assert cfg.reasoning_effort == 6


def test_improve_section_schemas():
    from lib.prompts._critic_schemas import SectionDraft
    draft = SectionDraft(section_markdown="## AI News\n- GPT-5 launches — [OpenAI](https://openai.com)")
    assert "GPT-5" in draft.section_markdown


def test_improve_section_system_prompt_forbids_new_info():
    cfg = get_prompt("improve_section")
    sp = cfg.system_prompt.lower()
    assert "do not add" in sp or "not add" in sp or "do not introduce" in sp or "not introduce" in sp
    assert "url" in sp or "source url" in sp or "links" in sp
