"""Engine factory: resolve string identifiers to Engine callables."""
from __future__ import annotations

from typing import Callable
from pydantic import BaseModel

from lib.engines.base import Engine, EngineError
from lib.engines.openrouter import openrouter_engine
from lib.engines.subagent import subagent_engine


def get_engine(identifier: str) -> Callable[..., BaseModel]:
    """Resolve an engine identifier string to a callable.

    Identifiers:
      - "subagent"               -> Claude CLI subprocess
      - "openrouter:<model_id>"  -> OpenRouter HTTP, e.g. "openrouter:google/gemini-2.5-flash"
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
    raise EngineError(f"Unknown engine identifier: {identifier!r}")


__all__ = ["get_engine", "Engine", "EngineError"]
