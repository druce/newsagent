"""OpenAI direct chat completions engine."""
from __future__ import annotations

import json
import os
from typing import Callable, Type

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


def openai_chat_engine(model_id: str) -> Callable[..., BaseModel]:
    def _call(system: str, user: str, schema: Type[BaseModel],
              reasoning_effort: int = 4) -> BaseModel:
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
                    "schema": schema.model_json_schema(),
                },
            },
            "reasoning_effort": _effort_level(reasoning_effort),
        }

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
