"""Claude Code CLI subprocess engine.

Shells out to `claude -p <prompt> --output-format json`. Uses the user's
Claude Code subscription; no API key required. Structured output is enforced
by embedding the JSON schema in the prompt and parsing the result.
"""
from __future__ import annotations

import json
import subprocess
from typing import Callable, Type

from pydantic import BaseModel, ValidationError

from lib.engines.base import EngineError


_TIMEOUT_SEC = 300


def _build_prompt(system: str, user: str, schema: Type[BaseModel],
                  reasoning_effort: int = 4) -> str:
    schema_json = json.dumps(schema.model_json_schema(), indent=2)
    return (
        f"{system}\n\n"
        f"Reasoning effort: {reasoning_effort}/10 "
        f"(0=trivial, 4=moderate, 8=heavy)\n\n"
        f"User input:\n{user}\n\n"
        f"You MUST respond with valid JSON matching this exact schema. "
        f"No prose, no markdown fences — just the JSON object.\n\n"
        f"JSON Schema:\n{schema_json}"
    )


def subagent_engine() -> Callable[..., BaseModel]:
    """Build an Engine callable that dispatches to `claude -p`."""

    def _call(system: str, user: str, schema: Type[BaseModel],
              reasoning_effort: int = 4) -> BaseModel:
        prompt = _build_prompt(system, user, schema, reasoning_effort)
        cmd = ["claude", "-p", prompt, "--output-format", "json"]

        try:
            completed = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=_TIMEOUT_SEC,
            )
        except FileNotFoundError as exc:
            raise EngineError("`claude` CLI not found on PATH") from exc
        except subprocess.TimeoutExpired as exc:
            raise EngineError(f"claude CLI timed out after {_TIMEOUT_SEC}s") from exc

        if completed.returncode != 0:
            raise EngineError(
                f"claude CLI exit {completed.returncode}: {completed.stderr[:500]}"
            )

        try:
            wrapper = json.loads(completed.stdout)
            assistant_text = wrapper.get("result", "")
            parsed = json.loads(assistant_text)
        except (json.JSONDecodeError, AttributeError) as exc:
            raise EngineError(f"Failed to parse claude CLI response: {exc}") from exc

        try:
            return schema.model_validate(parsed)
        except ValidationError as exc:
            raise EngineError(f"Subagent response failed schema validation: {exc}") from exc

    return _call
