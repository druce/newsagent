"""OpenRouter HTTP engine for call_prompt.

Uses OpenRouter's OpenAI-compatible /chat/completions endpoint with
response_format: json_schema for structured outputs.
"""
from __future__ import annotations

import json
import os
from typing import Callable, Type

import httpx
from pydantic import BaseModel, ValidationError

from lib.engines.base import EngineError


_BASE_URL = "https://openrouter.ai/api/v1/chat/completions"
_TIMEOUT = httpx.Timeout(120.0, connect=10.0)


def openrouter_engine(model_id: str) -> Callable[..., BaseModel]:
    """Build an Engine callable bound to a specific OpenRouter model id."""

    def _call(system: str, user: str, schema: Type[BaseModel]) -> BaseModel:
        api_key = os.environ.get("OPENROUTER_API_KEY")
        if not api_key:
            raise EngineError("OPENROUTER_API_KEY not set in environment")

        body = {
            "model": model_id,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "structured_output",
                    "strict": True,
                    "schema": schema.model_json_schema(),
                },
            },
            "provider": {"sort": "latency", "require_parameters": True},
        }
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        try:
            with httpx.Client(timeout=_TIMEOUT) as client:
                resp = client.post(_BASE_URL, headers=headers, json=body)
        except httpx.HTTPError as exc:
            raise EngineError(f"OpenRouter HTTP error: {exc}") from exc

        if resp.status_code != 200:
            raise EngineError(
                f"OpenRouter returned {resp.status_code}: {resp.text[:500]}"
            )

        try:
            payload = resp.json()
            content = payload["choices"][0]["message"]["content"]
            parsed = json.loads(content)
        except (KeyError, IndexError, json.JSONDecodeError) as exc:
            raise EngineError(f"Failed to parse OpenRouter response: {exc}") from exc

        try:
            return schema.model_validate(parsed)
        except ValidationError as exc:
            raise EngineError(f"Response failed schema validation: {exc}") from exc

    return _call
