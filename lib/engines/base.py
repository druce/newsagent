"""Shared types for engine implementations."""
from __future__ import annotations

from typing import Protocol, Type
from pydantic import BaseModel


class EngineError(Exception):
    """Raised when an engine fails to produce a valid response."""


class EngineBlockedError(EngineError):
    """Raised when the provider refused the prompt outright (safety/content filter).

    Distinct from a transient failure: the provider returned no candidates at
    all, so retrying the identical prompt cannot succeed. Callers that process
    many independent items (e.g. Bradley-Terry battles) should skip the item
    rather than abort the whole step.
    """


class Engine(Protocol):
    """A callable that takes system+user prompts and returns a parsed Pydantic model."""

    def __call__(
        self,
        system: str,
        user: str,
        schema: Type[BaseModel],
    ) -> BaseModel: ...
