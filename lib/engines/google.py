"""Google Gemini engine via google-genai SDK."""
from __future__ import annotations

import json
import os
from typing import Callable, Type

from google import genai
from google.genai import types as genai_types
from pydantic import BaseModel, ValidationError

from lib.engines.base import EngineError


def _thinking_budget(effort: int) -> int:
    """Map 0-10 reasoning_effort to Gemini thinking_budget (0 = off)."""
    if effort <= 0:
        return 0
    if effort <= 3:
        return 2048
    if effort <= 6:
        return 8192
    return 24576


def google_engine(model_id: str) -> Callable[..., BaseModel]:
    def _call(system: str, user: str, schema: Type[BaseModel],
              reasoning_effort: int = 4) -> BaseModel:
        api_key = os.environ.get("GOOGLE_API_KEY")
        if not api_key:
            raise EngineError("GOOGLE_API_KEY not set in environment")

        client = genai.Client(api_key=api_key)

        config = genai_types.GenerateContentConfig(
            system_instruction=system,
            response_mime_type="application/json",
            response_schema=schema,
            thinking_config=genai_types.ThinkingConfig(
                thinking_budget=_thinking_budget(reasoning_effort)
            ),
        )

        try:
            resp = client.models.generate_content(
                model=model_id,
                contents=user,
                config=config,
            )
        except Exception as exc:
            raise EngineError(f"Google API error: {exc}") from exc

        try:
            text = resp.text or ""
            parsed = json.loads(text)
        except (json.JSONDecodeError, AttributeError) as exc:
            raise EngineError(f"Failed to parse Google response: {exc}") from exc

        try:
            return schema.model_validate(parsed)
        except ValidationError as exc:
            raise EngineError(f"Response failed schema validation: {exc}") from exc

    return _call
