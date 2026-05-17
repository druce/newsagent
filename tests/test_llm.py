import pytest
from pydantic import BaseModel
from lib.llm import (
    PromptConfig, call_prompt, register_prompt, get_prompt, _registry,
)


class _In(BaseModel):
    title: str


class _Out(BaseModel):
    is_ai: bool


def _make_cfg() -> PromptConfig:
    return PromptConfig(
        name="test_prompt",
        system_prompt="be brief",
        user_prompt="Classify: {title}",
        input_schema=_In,
        output_schema=_Out,
        default_engine="subagent",
    )


@pytest.fixture(autouse=True)
def _clean_registry():
    _registry.clear()
    yield
    _registry.clear()


def test_register_and_get_prompt():
    cfg = _make_cfg()
    register_prompt(cfg)
    assert get_prompt("test_prompt") is cfg


def test_register_prompt_rejects_duplicate():
    register_prompt(_make_cfg())
    with pytest.raises(ValueError, match="already registered"):
        register_prompt(_make_cfg())


def test_get_prompt_missing():
    with pytest.raises(KeyError, match="test_prompt"):
        get_prompt("test_prompt")


def test_call_prompt_formats_user_prompt_and_routes(monkeypatch):
    register_prompt(_make_cfg())
    called = {}

    def fake_engine(system, user, schema, reasoning_effort=None):
        called["system"] = system
        called["user"] = user
        called["schema"] = schema
        return schema(is_ai=True)

    def fake_get_engine(identifier):
        called["engine"] = identifier
        return fake_engine

    monkeypatch.setattr("lib.llm.get_engine", fake_get_engine)

    result = call_prompt("test_prompt", {"title": "GPT-6 released"})
    assert result.is_ai is True
    assert called["system"] == "be brief"
    assert called["user"] == "Classify: GPT-6 released"
    assert called["schema"] is _Out
    assert called["engine"] == "subagent"


def test_call_prompt_explicit_engine_overrides_default(monkeypatch):
    register_prompt(_make_cfg())
    captured = {}

    def fake_get_engine(identifier):
        captured["id"] = identifier
        return lambda system, user, schema, reasoning_effort=None: schema(is_ai=False)

    monkeypatch.setattr("lib.llm.get_engine", fake_get_engine)

    call_prompt("test_prompt", {"title": "x"}, engine="openrouter:google/gemini-2.5-flash")
    assert captured["id"] == "openrouter:google/gemini-2.5-flash"


def test_call_prompt_env_var_overrides_default(monkeypatch):
    register_prompt(_make_cfg())
    monkeypatch.setenv("NEWS_PROMPT_TEST_PROMPT_ENGINE", "openrouter:foo/bar")
    captured = {}

    def fake_get_engine(identifier):
        captured["id"] = identifier
        return lambda system, user, schema, reasoning_effort=None: schema(is_ai=False)

    monkeypatch.setattr("lib.llm.get_engine", fake_get_engine)

    call_prompt("test_prompt", {"title": "x"})
    assert captured["id"] == "openrouter:foo/bar"


def test_call_prompt_explicit_beats_env_var(monkeypatch):
    register_prompt(_make_cfg())
    monkeypatch.setenv("NEWS_PROMPT_TEST_PROMPT_ENGINE", "openrouter:via-env")
    captured = {}

    def fake_get_engine(identifier):
        captured["id"] = identifier
        return lambda system, user, schema, reasoning_effort=None: schema(is_ai=False)

    monkeypatch.setattr("lib.llm.get_engine", fake_get_engine)

    call_prompt("test_prompt", {"title": "x"}, engine="openrouter:via-arg")
    assert captured["id"] == "openrouter:via-arg"


def test_call_prompt_rejects_missing_engine(monkeypatch):
    cfg = PromptConfig(
        name="no_default",
        system_prompt="s", user_prompt="u: {title}",
        input_schema=_In, output_schema=_Out,
        default_engine=None,
    )
    register_prompt(cfg)
    monkeypatch.delenv("NEWS_PROMPT_NO_DEFAULT_ENGINE", raising=False)
    with pytest.raises(ValueError, match="No engine"):
        call_prompt("no_default", {"title": "x"})


def test_call_prompt_validates_input(monkeypatch):
    register_prompt(_make_cfg())
    monkeypatch.setattr("lib.llm.get_engine",
                        lambda i: (lambda s, u, sc, reasoning_effort=None: sc(is_ai=True)))
    with pytest.raises(ValueError):
        call_prompt("test_prompt", {"wrong_field": "x"})


def test_call_prompt_passes_reasoning_effort_to_engine(monkeypatch):
    register_prompt(PromptConfig(
        name="effort_test",
        system_prompt="s", user_prompt="u: {title}",
        input_schema=_In, output_schema=_Out,
        default_engine="subagent",
        reasoning_effort=8,
    ))
    captured = {}

    def fake_engine(system, user, schema, reasoning_effort=None):
        captured["effort"] = reasoning_effort
        return schema(is_ai=True)

    monkeypatch.setattr("lib.llm.get_engine", lambda i: fake_engine)
    call_prompt("effort_test", {"title": "x"})
    assert captured["effort"] == 8
