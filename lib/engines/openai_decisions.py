"""OpenAI Decisions API engine: one yes/no predicate per call, returns its probability.

POST https://api.openai.com/v1/decisions with
    {"model": <model>, "input": <user>, "questions": [{"type": "predicate", "instructions": <system>}]}
returns {"answers": [{"type": "predicate", "probability": p}], "usage": {...}}.

The API is undocumented (request shape found by trial, 2026-11-10; see the
reference_openai_decisions_api memory note). It produces no output tokens and
answers in ~0.3 s. Prompts bound to this engine are predicates: the system
prompt is the question ("True if ..."), the user prompt is the text to judge,
and the output schema must have a `probability` field.

Transient failures (transport drops, 429, 5xx) are retried with backoff. If the
Decisions API still fails, or rejects the request outright, the same model is
asked through Chat Completions for a verbalized probability, so the rate step
keeps working if the undocumented endpoint changes or disappears.
"""
from __future__ import annotations

import os
import sys
import time
from typing import Callable, Optional, Type

import httpx
from pydantic import BaseModel, ValidationError

from lib.engines.base import EngineError, EngineTransientError
from lib.engines.openai_chat import openai_chat_engine

DECISIONS_URL = "https://api.openai.com/v1/decisions"
_ATTEMPTS = 3
_TIMEOUT_SECONDS = 60

_FALLBACK_SYSTEM = """\
Judge the text in the user message against the statement below. Return the probability (0.0 to 1.0)
that the statement is true for that text.

Statement:
{question}"""


def _decide(model_id: str, api_key: str, question: str, text: str) -> float:
    body = {"model": model_id, "input": text,
            "questions": [{"type": "predicate", "instructions": question}]}
    try:
        r = httpx.post(DECISIONS_URL, json=body, timeout=_TIMEOUT_SECONDS,
                       headers={"Authorization": f"Bearer {api_key}"})
    except httpx.TransportError as exc:
        raise EngineTransientError(f"Decisions API transport error: {exc}") from exc
    if r.status_code == 429 or r.status_code >= 500:
        raise EngineTransientError(f"Decisions API {r.status_code}: {r.text[:200]}")
    if r.status_code != 200:
        raise EngineError(f"Decisions API {r.status_code}: {r.text[:200]}")
    try:
        return float(r.json()["answers"][0]["probability"])
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise EngineError(f"Unexpected Decisions API response: {r.text[:200]}") from exc


def openai_decisions_engine(model_id: str) -> Callable[..., BaseModel]:
    def _call(system: str, user: str, schema: Type[BaseModel],
              reasoning_effort: Optional[int] = None) -> BaseModel:
        if "probability" not in schema.model_fields:
            raise EngineError(
                f"openai-decisions engine needs an output schema with a `probability` field; "
                f"got {schema.__name__}")
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise EngineError("OPENAI_API_KEY not set in environment")

        err: Optional[EngineError] = None
        for attempt in range(_ATTEMPTS):
            try:
                p = _decide(model_id, api_key, system, user)
                return schema.model_validate({"probability": min(max(p, 0.0), 1.0)})
            except EngineTransientError as exc:
                err = exc
                if attempt < _ATTEMPTS - 1:
                    time.sleep(2 ** attempt)
            except (EngineError, ValidationError) as exc:
                err = exc if isinstance(exc, EngineError) else EngineError(str(exc))
                break

        print(f"  [decisions] {err}; falling back to chat completions on {model_id}", file=sys.stderr)
        return openai_chat_engine(model_id)(
            system=_FALLBACK_SYSTEM.format(question=system), user=user, schema=schema,
            reasoning_effort=reasoning_effort)

    return _call
