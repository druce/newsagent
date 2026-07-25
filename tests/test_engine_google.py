# tests/test_engine_google.py
from unittest.mock import patch, MagicMock
import pytest
from pydantic import BaseModel
from lib.engines.google import google_engine
from lib.engines.base import EngineBlockedError, EngineError


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
