"""OpenAI text-embedding-3-large helper for newsagent.

Used by dedup (cosine similarity), cluster (UMAP+HDBSCAN), and select (MMR).
Matches the legacy ~/projects/OpenAIAgentsSDK/ workflow so the existing
umap_reducer.pkl can be reused as-is.
"""
from __future__ import annotations

import os
import re
from typing import List

from openai import OpenAI

from lib.engines.base import EngineError


_DEFAULT_MODEL = "text-embedding-3-large"
_BATCH_SIZE = 256
# OpenAI caps embeddings requests at 300k tokens. Keep headroom; ~4 chars/token.
_MAX_CHARS_PER_REQUEST = 1_000_000
# text-embedding-3-large rejects any single input over 8192 tokens. Callers
# truncate by chars, but URL/percent-encoded-heavy text can tokenize at ~2
# chars/token and still blow the cap; on that 400 we halve the offending
# input and retry rather than fail the whole step.
_TOO_LONG_RE = re.compile(r"Invalid 'input\[(\d+)\]': maximum input length")


def embed_texts(
    texts: List[str],
    model: str = _DEFAULT_MODEL,
) -> List[List[float]]:
    """Return embeddings for each input text, preserving order."""
    if not texts:
        return []
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise EngineError("OPENAI_API_KEY not set in environment")

    client = OpenAI(api_key=api_key)
    out: List[List[float]] = []
    i = 0
    n = len(texts)
    while i < n:
        chunk: List[str] = []
        char_budget = 0
        j = i
        while j < n and len(chunk) < _BATCH_SIZE:
            t_len = len(texts[j])
            if chunk and char_budget + t_len > _MAX_CHARS_PER_REQUEST:
                break
            chunk.append(texts[j])
            char_budget += t_len
            j += 1
        out.extend(_embed_chunk(client, model, chunk))
        i = j
    return out


def _embed_chunk(client: OpenAI, model: str, chunk: List[str]) -> List[List[float]]:
    """Embed one request's worth of inputs, shrinking any input the API rejects as too long."""
    chunk = list(chunk)
    while True:
        try:
            resp = client.embeddings.create(model=model, input=chunk)
        except Exception as exc:
            m = _TOO_LONG_RE.search(str(exc))
            idx = int(m.group(1)) if m else -1
            if 0 <= idx < len(chunk) and len(chunk[idx]) > 1:
                chunk[idx] = chunk[idx][: len(chunk[idx]) // 2]
                continue
            raise EngineError(f"OpenAI embeddings error: {exc}") from exc
        return [d.embedding for d in resp.data]
