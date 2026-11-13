# tests/test_engine_openai_decisions.py
from unittest.mock import MagicMock, patch

import httpx
import pytest
from pydantic import BaseModel

from lib.engines import get_engine
from lib.engines.base import EngineError
from lib.engines.openai_decisions import openai_decisions_engine
from lib.prompts._rating_schemas import PredicateOutput


def _resp(status: int, body: dict | None = None):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = body or {}
    r.text = str(body)
    return r


def _ok(p: float):
    return _resp(200, {"answers": [{"type": "predicate", "name": None, "probability": p}],
                       "usage": {"input_tokens": 300, "output_tokens": 0}})


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr("lib.engines.openai_decisions.time.sleep", lambda s: None)


def test_sends_predicate_request_and_returns_probability():
    with patch("lib.engines.openai_decisions.httpx.post", return_value=_ok(0.37)) as post:
        out = openai_decisions_engine("gpt-6-luna")(system="True if X.", user="Title\n- s",
                                                    schema=PredicateOutput, reasoning_effort=None)
    assert isinstance(out, PredicateOutput) and out.probability == 0.37
    body = post.call_args.kwargs["json"]
    assert body == {"model": "gpt-6-luna", "input": "Title\n- s",
                    "questions": [{"type": "predicate", "instructions": "True if X."}]}
    assert post.call_args.args[0] == "https://api.openai.com/v1/decisions"
    assert post.call_args.kwargs["headers"]["Authorization"] == "Bearer sk-test"


def test_retries_transient_then_succeeds():
    seq = [httpx.ConnectError("boom"), _resp(503, {"error": "busy"}), _ok(0.9)]
    with patch("lib.engines.openai_decisions.httpx.post", side_effect=seq) as post:
        out = openai_decisions_engine("gpt-6-luna")(system="q", user="u", schema=PredicateOutput)
    assert out.probability == 0.9 and post.call_count == 3


def test_falls_back_to_chat_completions_on_client_error():
    fake_chat = MagicMock(return_value=PredicateOutput(probability=0.2))
    with patch("lib.engines.openai_decisions.httpx.post", return_value=_resp(400, {"error": "bad"})), \
         patch("lib.engines.openai_decisions.openai_chat_engine", return_value=fake_chat) as factory:
        out = openai_decisions_engine("gpt-6-luna")(system="True if X.", user="art", schema=PredicateOutput)
    assert out.probability == 0.2
    factory.assert_called_once_with("gpt-6-luna")
    kw = fake_chat.call_args.kwargs
    assert "True if X." in kw["system"] and kw["user"] == "art" and kw["schema"] is PredicateOutput


def test_falls_back_after_transient_retries_exhausted():
    fake_chat = MagicMock(return_value=PredicateOutput(probability=0.6))
    with patch("lib.engines.openai_decisions.httpx.post", return_value=_resp(500, {})), \
         patch("lib.engines.openai_decisions.openai_chat_engine", return_value=fake_chat):
        out = openai_decisions_engine("gpt-6-luna")(system="q", user="u", schema=PredicateOutput)
    assert out.probability == 0.6


def test_rejects_schema_without_probability():
    class Other(BaseModel):
        answer: str
    with pytest.raises(EngineError, match="probability"):
        openai_decisions_engine("gpt-6-luna")(system="q", user="u", schema=Other)


def test_requires_api_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY")
    with pytest.raises(EngineError, match="OPENAI_API_KEY"):
        openai_decisions_engine("gpt-6-luna")(system="q", user="u", schema=PredicateOutput)


def test_factory_resolves_identifier():
    assert callable(get_engine("openai-decisions:gpt-6-luna"))
    with pytest.raises(EngineError):
        get_engine("openai-decisions:")
