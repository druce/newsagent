import json
import os
import pytest
import respx
import httpx
from pydantic import BaseModel
from lib.engines.openrouter import openrouter_engine
from lib.engines.base import EngineError


class _Out(BaseModel):
    answer: str
    confidence: float


@pytest.fixture
def or_key(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key-xyz")


def _ok_body(payload: dict) -> dict:
    return {
        "id": "gen-test",
        "choices": [{
            "message": {"role": "assistant", "content": json.dumps(payload)},
            "finish_reason": "stop",
        }],
    }


@respx.mock
def test_openrouter_engine_sends_correct_request(or_key):
    route = respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=_ok_body({"answer": "yes", "confidence": 0.9}))
    )
    engine = openrouter_engine("google/gemini-2.5-flash")
    result = engine(system="be helpful", user="is the sky blue?", schema=_Out)

    assert isinstance(result, _Out)
    assert result.answer == "yes"
    assert result.confidence == 0.9
    assert route.called
    sent = json.loads(route.calls.last.request.content)
    assert sent["model"] == "google/gemini-2.5-flash"
    assert sent["messages"] == [
        {"role": "system", "content": "be helpful"},
        {"role": "user", "content": "is the sky blue?"},
    ]
    # Structured output request
    assert sent["response_format"]["type"] == "json_schema"
    assert "schema" in sent["response_format"]["json_schema"]
    # Auth header
    auth = route.calls.last.request.headers["authorization"]
    assert auth == "Bearer test-key-xyz"


@respx.mock
def test_openrouter_engine_raises_on_http_error(or_key):
    respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
        return_value=httpx.Response(500, text="server explode")
    )
    engine = openrouter_engine("google/gemini-2.5-flash")
    with pytest.raises(EngineError, match="500"):
        engine(system="s", user="u", schema=_Out)


@respx.mock
def test_openrouter_engine_raises_on_malformed_json(or_key):
    respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={
            "choices": [{"message": {"role": "assistant", "content": "not json"}}],
        })
    )
    engine = openrouter_engine("google/gemini-2.5-flash")
    with pytest.raises(EngineError, match="parse"):
        engine(system="s", user="u", schema=_Out)


def test_openrouter_engine_requires_api_key(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(EngineError, match="OPENROUTER_API_KEY"):
        openrouter_engine("google/gemini-2.5-flash")(system="s", user="u", schema=_Out)
