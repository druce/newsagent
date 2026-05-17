"""extract_summaries — bullet-point article summaries.

Ported from ~/projects/OpenAIAgentsSDK/prompts.py:284 (EXTRACT_SUMMARIES).
"""
from __future__ import annotations

import json
from typing import List

from pydantic import BaseModel, Field, computed_field

from lib.llm import PromptConfig, register_prompt


class ArticleItem(BaseModel):
    id: str
    title: str
    text: str


class ExtractSummariesInput(BaseModel):
    items: List[ArticleItem] = Field(min_length=1)

    @computed_field
    @property
    def input_text(self) -> str:
        return json.dumps([item.model_dump() for item in self.items])


class ArticleSummary(BaseModel):
    id: str
    summary: str


class ExtractSummariesOutput(BaseModel):
    summaries: List[ArticleSummary]


# Verbatim from legacy prompts.py:292-324
_SYSTEM = """\
You are an expert AI news analyst. Your task is to create concise, informative bullet-point summaries of AI and technology articles for a professional newsletter audience.

You will receive a list of JSON objects with fields "id" and "title"
Return **only** a JSON object that satisfies the provided schema.
For each article provided, you MUST return one element with the same id, and the summary.
Return elements in the same order they were provided.
No markdown, no markdown fences, no extra keys, no comments.

Write a summary with 3 bullet points (-) that capture ONLY the newsworthy content.

Include
- Key facts & technological developments
- Business implications and market impact
- Future outlook and expert predictions
- Practical applications and use cases
- Key quotes
- Essential background tied directly to the story

Exclude
- Navigation/UI text, ads, paywalls, cookie banners, JS, legal/footer copy, "About us", social widgets

Rules
- Accurately summarize original meaning
- Contents only, no additional commentary or opinion, no "the article discusses", "the author states"
- Maintain factual & neutral tone
- If no substantive news, return one bullet: "no content"
- Output raw bullets (no code fences, no headings, no extra text--only the bullet strings)"""

_USER = """\
Summarize the articles below:

{input_text}"""


EXTRACT_SUMMARIES = PromptConfig(
    name="extract_summaries",
    system_prompt=_SYSTEM,
    user_prompt=_USER,
    input_schema=ExtractSummariesInput,
    output_schema=ExtractSummariesOutput,
    default_engine="subagent",
    reasoning_effort=6,
)

register_prompt(EXTRACT_SUMMARIES)
