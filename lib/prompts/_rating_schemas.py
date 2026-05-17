"""Shared Pydantic schemas for the three per-axis rating prompts.

All three prompts (RATE_QUALITY, RATE_ON_TOPIC, RATE_IMPORTANCE) share the
same input/output structure — only the prompt text differs.
"""
from __future__ import annotations

import json
from typing import List

from pydantic import BaseModel, Field, computed_field


class RatingItem(BaseModel):
    id: str
    input_text: str


class RatingInput(BaseModel):
    items: List[RatingItem] = Field(min_length=1)

    @computed_field
    @property
    def input_text(self) -> str:
        return json.dumps([item.model_dump() for item in self.items])


class StoryConfidence(BaseModel):
    id: str
    confidence: float


class RatingOutput(BaseModel):
    results_list: List[StoryConfidence]
