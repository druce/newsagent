from unittest.mock import patch, MagicMock
import pytest
from lib.embeddings import embed_texts
from lib.engines.base import EngineError


def _fake_resp(vectors):
    return MagicMock(data=[MagicMock(embedding=v) for v in vectors])


def test_embed_texts_returns_vectors(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    fake = MagicMock()
    fake.embeddings.create.return_value = _fake_resp([[0.1, 0.2], [0.3, 0.4]])
    with patch("lib.embeddings.OpenAI", return_value=fake):
        result = embed_texts(["hello", "world"])
    assert result == [[0.1, 0.2], [0.3, 0.4]]
    kwargs = fake.embeddings.create.call_args.kwargs
    assert kwargs["model"] == "text-embedding-3-large"


def test_embed_texts_empty():
    assert embed_texts([]) == []


def test_embed_texts_batched(monkeypatch):
    """When inputs > BATCH_SIZE, multiple API calls are made and results stitched in order."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    fake = MagicMock()
    # Two calls, each returning 1 vector
    fake.embeddings.create.side_effect = [
        _fake_resp([[0.1]]),
        _fake_resp([[0.2]]),
    ]
    with patch("lib.embeddings.OpenAI", return_value=fake):
        with patch("lib.embeddings._BATCH_SIZE", 1):
            result = embed_texts(["a", "b"])
    assert result == [[0.1], [0.2]]
    assert fake.embeddings.create.call_count == 2


def test_embed_texts_requires_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(EngineError, match="OPENAI_API_KEY"):
        embed_texts(["x"])


def _too_long_error(index: int):
    return Exception(
        f"Error code: 400 - {{'error': {{'message': \"Invalid 'input[{index}]': "
        f"maximum input length is 8192 tokens.\", 'type': 'invalid_request_error'}}}}"
    )


def test_embed_texts_truncates_over_limit_input(monkeypatch):
    """A 400 'maximum input length' on input[N] halves that text and retries the chunk."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    fake = MagicMock()
    fake.embeddings.create.side_effect = [
        _too_long_error(1),
        _too_long_error(1),
        _fake_resp([[0.1], [0.2], [0.3]]),
    ]
    long_text = "x" * 1000
    with patch("lib.embeddings.OpenAI", return_value=fake):
        result = embed_texts(["a", long_text, "c"])
    assert result == [[0.1], [0.2], [0.3]]
    assert fake.embeddings.create.call_count == 3
    sent = fake.embeddings.create.call_args.kwargs["input"]
    assert sent[0] == "a" and sent[2] == "c"
    assert len(sent[1]) == 250  # halved twice


def test_embed_texts_other_error_raises(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    fake = MagicMock()
    fake.embeddings.create.side_effect = Exception("Error code: 429 - rate limited")
    with patch("lib.embeddings.OpenAI", return_value=fake):
        with pytest.raises(EngineError, match="rate limited"):
            embed_texts(["a"])
