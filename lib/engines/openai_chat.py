"""OpenAI direct chat completions engine."""
from __future__ import annotations

import json
import os
from typing import Callable, Optional, Type

from openai import OpenAI
from pydantic import BaseModel, ValidationError

from lib.engines.base import EngineError


def _effort_level(effort: int) -> str:
    if effort <= 0:
        return "minimal"
    if effort <= 3:
        return "low"
    if effort <= 6:
        return "medium"
    return "high"


def _strictify_schema(schema):
    """Recursively add `additionalProperties: false` to every object in the
    schema and ensure every property appears in `required`.

    OpenAI's structured-output strict mode rejects schemas without these.
    """
    if isinstance(schema, dict):
        if schema.get("type") == "object":
            schema["additionalProperties"] = False
            if "properties" in schema:
                schema["required"] = list(schema["properties"].keys())
        for v in schema.values():
            _strictify_schema(v)
    elif isinstance(schema, list):
        for v in schema:
            _strictify_schema(v)
    return schema


def openai_chat_engine(model_id: str) -> Callable[..., BaseModel]:
    def _call(system: str, user: str, schema: Type[BaseModel],
              reasoning_effort: Optional[int] = 4) -> BaseModel:
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise EngineError("OPENAI_API_KEY not set in environment")

        client = OpenAI(api_key=api_key)

        kwargs = {
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
                    "schema": _strictify_schema(schema.model_json_schema()),
                },
            },
        }
        # Only attach reasoning_effort for models that honor it. Prompts can
        # opt out by setting reasoning_effort=None on their PromptConfig (e.g.
        # gpt-4o-mini rejects the param outright, which otherwise burns a
        # round-trip via the fallback retry below).
        if reasoning_effort is not None:
            kwargs["reasoning_effort"] = _effort_level(reasoning_effort)

        try:
            resp = client.chat.completions.create(**kwargs)
        except Exception as exc:
            # SDK raises on unsupported params; retry once without reasoning_effort
            if "reasoning_effort" in str(exc):
                kwargs.pop("reasoning_effort", None)
                try:
                    resp = client.chat.completions.create(**kwargs)
                except Exception as inner:
                    raise EngineError(f"OpenAI API error: {inner}") from inner
            else:
                raise EngineError(f"OpenAI API error: {exc}") from exc

        try:
            content = resp.choices[0].message.content or ""
            parsed = json.loads(content)
        except (IndexError, AttributeError, json.JSONDecodeError) as exc:
            raise EngineError(f"Failed to parse OpenAI response: {exc}") from exc

        try:
            return schema.model_validate(parsed)
        except ValidationError as exc:
            raise EngineError(f"Response failed schema validation: {exc}") from exc

    return _call
