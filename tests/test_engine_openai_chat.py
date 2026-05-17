# tests/test_engine_openai_chat.py
import json
from unittest.mock import patch, MagicMock
import pytest
from pydantic import BaseModel
from lib.engines.openai_chat import openai_chat_engine
from lib.engines.base import EngineError


class _Out(BaseModel):
    answer: str


def _fake_completion(content: str):
    msg = MagicMock()
    msg.content = content
    choice = MagicMock()
    choice.message = msg
    resp = MagicMock()
    resp.choices = [choice]
    return resp


def test_openai_chat_engine_sends_correct_body(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    fake_client = MagicMock()
    fake_client.chat.completions.create.return_value = _fake_completion('{"answer": "yes"}')
    with patch("lib.engines.openai_chat.OpenAI", return_value=fake_client):
        engine = openai_chat_engine("gpt-4o-mini")
        result = engine(system="be helpful", user="hi", schema=_Out, reasoning_effort=6)
    assert isinstance(result, _Out) and result.answer == "yes"
    kwargs = fake_client.chat.completions.create.call_args.kwargs
    assert kwargs["model"] == "gpt-4o-mini"
    assert kwargs["messages"] == [
        {"role": "system", "content": "be helpful"},
        {"role": "user", "content": "hi"},
    ]
    assert kwargs["response_format"]["type"] == "json_schema"
    assert kwargs.get("reasoning_effort") == "medium"


def test_openai_chat_engine_maps_effort_levels(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    fake_client = MagicMock()
    fake_client.chat.completions.create.return_value = _fake_completion('{"answer": "x"}')
    with patch("lib.engines.openai_chat.OpenAI", return_value=fake_client):
        engine = openai_chat_engine("gpt-5-mini")
        for effort, level in [(0, "minimal"), (2, "low"), (5, "medium"), (9, "high")]:
            fake_client.chat.completions.create.reset_mock()
            engine(system="s", user="u", schema=_Out, reasoning_effort=effort)
            kwargs = fake_client.chat.completions.create.call_args.kwargs
            assert kwargs.get("reasoning_effort") == level


def test_openai_chat_engine_requires_api_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(EngineError, match="OPENAI_API_KEY"):
        openai_chat_engine("gpt-4o-mini")(system="s", user="u", schema=_Out)


def test_openai_chat_engine_raises_on_unparseable(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    fake_client = MagicMock()
    fake_client.chat.completions.create.return_value = _fake_completion("not json")
    with patch("lib.engines.openai_chat.OpenAI", return_value=fake_client):
        engine = openai_chat_engine("gpt-4o-mini")
        with pytest.raises(EngineError, match="parse"):
            engine(system="s", user="u", schema=_Out)
