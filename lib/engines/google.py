"""Google Gemini engine via google-genai SDK."""
from __future__ import annotations

import json
import os
from typing import Callable, Type

from google import genai
from google.genai import types as genai_types
from pydantic import BaseModel, ValidationError

from lib.engines.base import EngineBlockedError, EngineError


# Thinking control. Google deprecated thinking_budget: Gemini 3.x only remaps
# it onto thinking_level as a fallback, and upcoming models reject it with
# 400 INVALID_ARGUMENT. reasoning_effort 0-10 maps onto the same buckets as
# the openai/openrouter engines. (The legacy token budgets were
# 0/512/1024/1536/2048/3072/4096/6144/8192/12288/16384 for effort 0-10.)
# Custom sampling params (temperature/top_p/top_k) are likewise deprecated
# and must never be sent.
def _thinking_level(effort: int) -> str:
    """Map 0-10 reasoning_effort to a Gemini thinking_level."""
    effort = max(0, min(10, effort))
    if effort == 0:
        return "minimal"
    if effort <= 3:
        return "low"
    if effort <= 6:
        return "medium"
    return "high"


# Models that should NOT receive thinking_config at all. Empty by default —
# every current Gemini model handles thinking_config fine when paired with a
# generous max_output_tokens cap. Earlier we listed gemini-3.1-flash-lite here
# after a 92K-char truncation, but that turned out to be a max_output_tokens
# issue (default ~8K), not a thinking-config incompatibility. Add an entry
# here only if a model genuinely misbehaves when thinking_config is present.
_NO_REASONING_MODELS: set[str] = set()

# Match legacy default_max_tokens for Gemini family. Without this explicit cap,
# the SDK falls back to a much smaller default (~8K) and structured-output
# responses can get truncated mid-string when the model is verbose.
_MAX_OUTPUT_TOKENS = 65536


def google_engine(model_id: str) -> Callable[..., BaseModel]:
    def _call(system: str, user: str, schema: Type[BaseModel],
              reasoning_effort: int = 4) -> BaseModel:
        api_key = os.environ.get("GOOGLE_API_KEY")
        if not api_key:
            raise EngineError("GOOGLE_API_KEY not set in environment")

        client = genai.Client(api_key=api_key)

        config_kwargs: dict = {
            "system_instruction": system,
            "max_output_tokens": _MAX_OUTPUT_TOKENS,
            "response_mime_type": "application/json",
            "response_schema": schema,
        }

        if model_id not in _NO_REASONING_MODELS:
            config_kwargs["thinking_config"] = genai_types.ThinkingConfig(
                thinking_level=_thinking_level(reasoning_effort)
            )

        config = genai_types.GenerateContentConfig(**config_kwargs)

        try:
            resp = client.models.generate_content(
                model=model_id,
                contents=user,
                config=config,
            )
        except Exception as exc:
            raise EngineError(f"Google API error: {exc}") from exc

        # Detect a prompt-level content-filter block explicitly. Gemini returns
        # zero candidates and an empty .text here, which would otherwise surface
        # as a baffling "Expecting value: line 1 column 1" JSON error. Callers
        # batching many independent items can catch EngineBlockedError and skip
        # just that item — retrying the identical prompt never succeeds.
        block_reason = getattr(resp.prompt_feedback, "block_reason", None)
        if block_reason:
            raise EngineBlockedError(
                f"Google prompt blocked by content filter "
                f"(block_reason={block_reason})"
            )

        # Detect truncation explicitly — gives a clearer error than the
        # cryptic JSON "unterminated string" the parser produces.
        if resp.candidates and resp.candidates[0].finish_reason == "MAX_TOKENS":
            raise EngineError(
                f"Gemini response truncated (finish_reason=MAX_TOKENS, "
                f"max_output_tokens={_MAX_OUTPUT_TOKENS}). "
                f"Model produced more tokens than the cap allows."
            )

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
