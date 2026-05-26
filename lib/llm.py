"""Unified LLM entry point: call_prompt.

Every LLM call in the pipeline goes through here. Engine routing is:
  explicit arg -> NEWS_PROMPT_<NAME>_ENGINE env var -> PromptConfig.default_engine
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional, Sequence, Type, Union

from pydantic import BaseModel, ValidationError

from lib.engines import get_engine


@dataclass(frozen=True)
class PromptConfig:
    name: str
    system_prompt: str
    user_prompt: str
    input_schema: Type[BaseModel]
    output_schema: Type[BaseModel]
    default_engine: Optional[str]
    reasoning_effort: Optional[int] = 4  # 0-10 scale; None to omit the param entirely


_registry: Dict[str, PromptConfig] = {}


def register_prompt(cfg: PromptConfig) -> None:
    if cfg.name in _registry:
        raise ValueError(f"Prompt {cfg.name!r} already registered")
    _registry[cfg.name] = cfg


def get_prompt(name: str) -> PromptConfig:
    if name not in _registry:
        raise KeyError(f"Prompt not registered: {name!r}")
    return _registry[name]


def _resolve_engine(prompt_name: str, explicit: Optional[str],
                    cfg: PromptConfig) -> str:
    if explicit is not None:
        return explicit
    env_var = f"NEWS_PROMPT_{prompt_name.upper()}_ENGINE"
    if env_var in os.environ:
        return os.environ[env_var]
    if cfg.default_engine is not None:
        return cfg.default_engine
    raise ValueError(
        f"No engine configured for {prompt_name!r} "
        f"(set {env_var} or pass engine=...)"
    )


def call_prompt(
    prompt_name: str,
    inputs: Union[Dict[str, Any], BaseModel],
    *,
    engine: Optional[str] = None,
) -> BaseModel:
    """Run a registered prompt against an engine and return the parsed result."""
    cfg = get_prompt(prompt_name)

    if isinstance(inputs, BaseModel):
        validated = inputs
    else:
        try:
            validated = cfg.input_schema.model_validate(inputs)
        except ValidationError as exc:
            raise ValueError(f"Input validation failed for {prompt_name}: {exc}") from exc

    user_str = cfg.user_prompt.format(**validated.model_dump())

    engine_id = _resolve_engine(prompt_name, engine, cfg)
    eng = get_engine(engine_id)
    return eng(
        system=cfg.system_prompt,
        user=user_str,
        schema=cfg.output_schema,
        reasoning_effort=cfg.reasoning_effort,
    )


def call_prompt_batch(
    prompt_name: str,
    inputs: Sequence[Union[Dict[str, Any], BaseModel]],
    *,
    engine: Optional[str] = None,
    parallelism: int = 8,
) -> List[BaseModel]:
    """Run a prompt over many inputs concurrently. Returns results in input order.

    Concurrency: up to `parallelism` engine calls in flight at once. Both engines
    block on I/O (subprocess / HTTP), so threading is sufficient — no asyncio.
    """
    if not inputs:
        return []
    if parallelism < 1:
        raise ValueError("parallelism must be >= 1")

    def _one(item):
        return call_prompt(prompt_name, item, engine=engine)

    with ThreadPoolExecutor(max_workers=parallelism) as pool:
        return list(pool.map(_one, inputs))
