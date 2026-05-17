"""Engine factory: resolve string identifiers to Engine callables."""
from __future__ import annotations

from typing import Callable
from pydantic import BaseModel

from lib.engines.base import Engine, EngineError
from lib.engines.openrouter import openrouter_engine
from lib.engines.subagent import subagent_engine
from lib.engines.openai_chat import openai_chat_engine
from lib.engines.google import google_engine


def get_engine(identifier: str) -> Callable[..., BaseModel]:
    """Resolve an engine identifier string to a callable.

    Identifiers:
      - "subagent"               -> Claude CLI subprocess
      - "openrouter:<model_id>"  -> OpenRouter HTTP, e.g. "openrouter:google/gemini-2.5-flash"
      - "openai:<model_id>"      -> OpenAI chat completions, e.g. "openai:gpt-4o-mini"
      - "google:<model_id>"      -> Google Gemini, e.g. "google:gemini-2.5-flash"
    """
    if identifier == "subagent":
        return subagent_engine()
    if identifier.startswith("openrouter:"):
        model_id = identifier[len("openrouter:"):]
        if not model_id:
            raise EngineError(
                "openrouter engine requires a model id, e.g. openrouter:google/gemini-2.5-flash"
            )
        return openrouter_engine(model_id)
    if identifier.startswith("openai:"):
        model_id = identifier[len("openai:"):]
        if not model_id:
            raise EngineError("openai engine requires a model id")
        return openai_chat_engine(model_id)
    if identifier.startswith("google:"):
        model_id = identifier[len("google:"):]
        if not model_id:
            raise EngineError("google engine requires a model id")
        return google_engine(model_id)
    raise EngineError(f"Unknown engine identifier: {identifier!r}")


__all__ = ["get_engine", "Engine", "EngineError"]
