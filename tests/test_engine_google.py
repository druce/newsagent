# tests/test_engine_google.py
from unittest.mock import patch, MagicMock
import httpx
import pytest
from google.genai import errors as genai_errors
from pydantic import BaseModel
from lib.engines.google import google_engine
from lib.engines.base import EngineBlockedError, EngineError, EngineTransientError


class _Out(BaseModel):
    answer: str


def _fake_resp(text: str):
    r = MagicMock()
    r.text = text
    r.prompt_feedback = None
    return r


def test_google_engine_sends_request(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "g-test")
    fake_client = MagicMock()
    fake_client.models.generate_content.return_value = _fake_resp('{"answer": "yes"}')
    with patch("lib.engines.google.genai.Client", return_value=fake_client):
        engine = google_engine("gemini-2.5-flash")
        result = engine(system="be helpful", user="hi", schema=_Out, reasoning_effort=6)
    assert result.answer == "yes"
    kwargs = fake_client.models.generate_content.call_args.kwargs
    assert kwargs["model"] == "gemini-2.5-flash"


def test_google_engine_requires_api_key(monkeypatch):
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    with pytest.raises(EngineError, match="GOOGLE_API_KEY"):
        google_engine("gemini-2.5-flash")(system="s", user="u", schema=_Out)


def test_google_engine_raises_on_unparseable(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "g-test")
    fake_client = MagicMock()
    fake_client.models.generate_content.return_value = _fake_resp("not json")
    with patch("lib.engines.google.genai.Client", return_value=fake_client):
        engine = google_engine("gemini-2.5-flash")
        with pytest.raises(EngineError, match="parse"):
            engine(system="s", user="u", schema=_Out)


def test_google_engine_raises_blocked_when_prompt_filtered(monkeypatch):
    """A safety/content-filtered prompt comes back with no candidates and an
    empty .text; surface it as EngineBlockedError so callers can skip the item
    instead of dying on a cryptic JSON parse error."""
    monkeypatch.setenv("GOOGLE_API_KEY", "g-test")
    resp = _fake_resp("")
    resp.candidates = []
    resp.prompt_feedback = MagicMock(block_reason="OTHER")
    fake_client = MagicMock()
    fake_client.models.generate_content.return_value = resp
    with patch("lib.engines.google.genai.Client", return_value=fake_client):
        engine = google_engine("gemini-2.5-flash")
        with pytest.raises(EngineBlockedError, match="blocked"):
            engine(system="s", user="u", schema=_Out)


def test_engine_blocked_error_is_an_engine_error():
    assert issubclass(EngineBlockedError, EngineError)


# ---------------------------------------------------------------------------
# Transient (retryable) failures. Gemini intermittently drops the connection
# before answering — seen 2026-09-15 as httpx RemoteProtocolError ("Server
# disconnected without sending a response") and httpx ConnectError ("SSL:
# UNEXPECTED_EOF_WHILE_READING"). These, and SDK-level 5xx, must surface as a
# distinct EngineTransientError so batch callers can retry by type rather than
# by grepping the message.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "exc",
    [
        httpx.RemoteProtocolError("Server disconnected without sending a response."),
        httpx.ConnectError("[SSL: UNEXPECTED_EOF_WHILE_READING] EOF occurred"),
        httpx.ReadTimeout("timed out"),
    ],
)
def test_google_engine_raises_transient_on_httpx_transport_error(monkeypatch, exc):
    monkeypatch.setenv("GOOGLE_API_KEY", "g-test")
    fake_client = MagicMock()
    fake_client.models.generate_content.side_effect = exc
    with patch("lib.engines.google.genai.Client", return_value=fake_client):
        engine = google_engine("gemini-2.5-flash")
        with pytest.raises(EngineTransientError):
            engine(system="s", user="u", schema=_Out)


def test_google_engine_raises_transient_on_sdk_5xx(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "g-test")
    fake_client = MagicMock()
    fake_client.models.generate_content.side_effect = genai_errors.ServerError(
        503, {"error": {"message": "UNAVAILABLE", "status": "UNAVAILABLE"}}
    )
    with patch("lib.engines.google.genai.Client", return_value=fake_client):
        engine = google_engine("gemini-2.5-flash")
        with pytest.raises(EngineTransientError, match="503"):
            engine(system="s", user="u", schema=_Out)


def test_google_engine_non_transport_error_is_plain_engine_error(monkeypatch):
    """A 4xx or an arbitrary SDK exception is not retryable."""
    monkeypatch.setenv("GOOGLE_API_KEY", "g-test")
    fake_client = MagicMock()
    fake_client.models.generate_content.side_effect = genai_errors.ClientError(
        400, {"error": {"message": "INVALID_ARGUMENT", "status": "INVALID_ARGUMENT"}}
    )
    with patch("lib.engines.google.genai.Client", return_value=fake_client):
        engine = google_engine("gemini-2.5-flash")
        with pytest.raises(EngineError) as ei:
            engine(system="s", user="u", schema=_Out)
    assert not isinstance(ei.value, EngineTransientError)


def test_engine_transient_error_is_an_engine_error():
    assert issubclass(EngineTransientError, EngineError)


# ---------------------------------------------------------------------------
# Thinking control: Google deprecated thinking_budget (upcoming models return
# 400 INVALID_ARGUMENT) in favour of thinking_level. reasoning_effort 0-10 maps
# onto the same buckets as the openai/openrouter engines.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("effort, level", [
    (0, "minimal"), (1, "low"), (2, "low"), (3, "low"),
    (4, "medium"), (5, "medium"), (6, "medium"),
    (7, "high"), (8, "high"), (10, "high"),
])
def test_thinking_level_mapping(effort, level):
    from lib.engines.google import _thinking_level
    assert _thinking_level(effort) == level


def test_google_engine_sends_thinking_level_not_budget(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "g-test")
    fake_client = MagicMock()
    fake_client.models.generate_content.return_value = _fake_resp('{"answer": "yes"}')
    with patch("lib.engines.google.genai.Client", return_value=fake_client):
        google_engine("gemini-3.5-flash-lite")(system="s", user="u", schema=_Out, reasoning_effort=2)
    config = fake_client.models.generate_content.call_args.kwargs["config"]
    tc = config.thinking_config
    assert tc is not None
    assert tc.thinking_budget is None
    assert str(getattr(tc.thinking_level, "value", tc.thinking_level)).lower() == "low"
    # no custom sampling parameters (deprecated; future models reject them)
    assert config.temperature is None and config.top_p is None and config.top_k is None
