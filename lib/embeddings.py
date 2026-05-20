"""OpenAI text-embedding-3-large helper for newsagent.

Used by dedup (cosine similarity), cluster (UMAP+HDBSCAN), and select (MMR).
Matches the legacy ~/projects/OpenAIAgentsSDK/ workflow so the existing
umap_reducer.pkl can be reused as-is.
"""
from __future__ import annotations

import os
from typing import List

from openai import OpenAI

from lib.engines.base import EngineError


_DEFAULT_MODEL = "text-embedding-3-large"
_BATCH_SIZE = 256


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
    for i in range(0, len(texts), _BATCH_SIZE):
        chunk = texts[i:i + _BATCH_SIZE]
        try:
            resp = client.embeddings.create(model=model, input=chunk)
        except Exception as exc:
            raise EngineError(f"OpenAI embeddings error: {exc}") from exc
        out.extend([d.embedding for d in resp.data])
    return out
