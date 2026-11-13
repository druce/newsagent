"""filter_urls_v2 — eval-only candidate: title + optional feed excerpt, with the
editorial policy for borderline topics set by the user on 2026-11-10
(revised 2026-11-11: data centers, chips, stock pieces, surveillance, fake media).
Not registered for the pipeline; evals.run.challenge imports it."""
from __future__ import annotations

import json
from typing import List, Optional

from pydantic import BaseModel, Field, computed_field

from lib.llm import PromptConfig, register_prompt
from lib.prompts.filter_urls import FilterUrlsOutput
from lib.prompts.filter_urls import _SYSTEM as _PROD_SYSTEM, _USER as _PROD_USER


class ExcerptItem(BaseModel):
    id: str
    title: str
    excerpt: Optional[str] = None


class FilterUrlsV2Input(BaseModel):
    items: List[ExcerptItem] = Field(min_length=1)

    @computed_field
    @property
    def input_text(self) -> str:
        return json.dumps([it.model_dump(exclude_none=True) for it in self.items], ensure_ascii=False)


# Same text as production (adopted 2026-11-11); imported so the two never drift.
_SYSTEM, _USER = _PROD_SYSTEM, _PROD_USER


FILTER_URLS_V2 = PromptConfig(
    name="filter_urls_v2",
    system_prompt=_SYSTEM,
    user_prompt=_USER,
    input_schema=FilterUrlsV2Input,
    output_schema=FilterUrlsOutput,
    default_engine="subagent",
    reasoning_effort=4,
)

register_prompt(FILTER_URLS_V2)
