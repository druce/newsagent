"""Claude Code CLI subprocess engine.

Shells out to `claude -p <prompt> --output-format json`. Uses the user's
Claude Code subscription; no API key required. Structured output is enforced
by the CLI's `--json-schema` flag and re-validated here with Pydantic;
`reasoning_effort` maps onto the CLI's `--effort` level.
"""
from __future__ import annotations

import json
import subprocess
from typing import Callable, Type

from pydantic import BaseModel, ValidationError

from lib.engines.base import EngineError


_TIMEOUT_SEC = 300


def _effort_level(effort: int) -> str:
    """Map 0-10 reasoning_effort onto a `claude --effort` level.

    Buckets match the other engines (<=3 low, <=6 medium, else high), with
    9-10 reaching up to xhigh.
    """
    if effort <= 3:
        return "low"
    if effort <= 6:
        return "medium"
    if effort <= 8:
        return "high"
    return "xhigh"


def _build_prompt(system: str, user: str) -> str:
    return f"{system}\n\nUser input:\n{user}"


def subagent_engine() -> Callable[..., BaseModel]:
    """Build an Engine callable that dispatches to `claude -p`."""

    def _call(system: str, user: str, schema: Type[BaseModel],
              reasoning_effort: int = 4) -> BaseModel:
        cmd = [
            "claude", "-p", _build_prompt(system, user),
            "--output-format", "json",
            "--json-schema", json.dumps(schema.model_json_schema()),
            "--effort", _effort_level(reasoning_effort),
        ]

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
            # --json-schema puts the parsed object in `structured_output`;
            # fall back to parsing the final text in `result`.
            parsed = wrapper.get("structured_output")
            if parsed is None:
                parsed = json.loads(wrapper.get("result", ""))
        except (json.JSONDecodeError, AttributeError) as exc:
            raise EngineError(f"Failed to parse claude CLI response: {exc}") from exc

        try:
            return schema.model_validate(parsed)
        except ValidationError as exc:
            raise EngineError(f"Subagent response failed schema validation: {exc}") from exc

    return _call
