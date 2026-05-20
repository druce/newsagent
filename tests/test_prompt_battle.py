"""Tests for BATTLE_PROMPT."""
import importlib
import pytest
import lib.prompts  # triggers registration
from lib.llm import get_prompt, _registry


@pytest.fixture(autouse=True)
def _ensure_registered():
    """Ensure battle prompt is registered before each test."""
    if "battle_prompt" not in _registry:
        mod = importlib.import_module("lib.prompts.battle")
        importlib.reload(mod)
    yield


def test_battle_prompt_registered():
    cfg = get_prompt("battle_prompt")
    assert cfg is not None


def test_battle_prompt_default_engine():
    cfg = get_prompt("battle_prompt")
    assert cfg.default_engine == "google:gemini-3.1-flash-lite"


def test_battle_prompt_reasoning_effort():
    cfg = get_prompt("battle_prompt")
    assert cfg.reasoning_effort == 2


def test_battle_prompt_system_mentions_ranking():
    cfg = get_prompt("battle_prompt")
    # Should mention ranking/ordering/relevance
    lower = cfg.system_prompt.lower()
    assert "rank" in lower or "sort" in lower or "order" in lower


def test_battle_input_schema_accepts_two_items():
    from lib.prompts.battle import BattleInput
    data = {
        "items": [
            {"id": "a1", "title": "Title A", "summary": "Summary A"},
            {"id": "a2", "title": "Title B", "summary": "Summary B"},
        ]
    }
    parsed = BattleInput.model_validate(data)
    assert len(parsed.items) == 2
    # computed_field serializes to JSON string
    assert "a1" in parsed.input_text
    assert "a2" in parsed.input_text


def test_battle_input_schema_rejects_single_item():
    from lib.prompts.battle import BattleInput
    with pytest.raises(Exception):
        BattleInput.model_validate({
            "items": [{"id": "a1", "title": "T", "summary": "S"}]
        })


def test_battle_output_schema_accepts_ranking_list():
    from lib.prompts.battle import BattleOutput
    out = BattleOutput(ranking=["id3", "id1", "id2"])
    assert out.ranking == ["id3", "id1", "id2"]


def test_battle_prompt_user_formats_input_text():
    cfg = get_prompt("battle_prompt")
    user = cfg.user_prompt.format(input_text="[{id: x}]")
    assert "[{id: x}]" in user
