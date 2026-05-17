"""filter_urls — classify headlines as AI-related.

Ported from ~/projects/OpenAIAgentsSDK/prompts.py:43 (FILTER_URLS).
"""
from __future__ import annotations

import json
from typing import List

from pydantic import BaseModel, Field, computed_field

from lib.llm import PromptConfig, register_prompt


class HeadlineItem(BaseModel):
    id: str
    title: str


class FilterUrlsInput(BaseModel):
    items: List[HeadlineItem] = Field(min_length=1)

    @computed_field
    @property
    def input_text(self) -> str:
        return json.dumps([item.model_dump() for item in self.items])


class HeadlineClassification(BaseModel):
    id: str
    is_ai: bool


class FilterUrlsOutput(BaseModel):
    classifications: List[HeadlineClassification]


_SYSTEM = """\
You are a content-classification assistant that labels news headlines as AI-related or not.
You will receive a list of JSON objects with fields "id" and "title".
Return **only** a JSON object that satisfies the provided schema.
For each headline provided, you MUST return one element with the same id, and a boolean value; do not skip any items.
Return elements in the same order they were provided.
No markdown, no markdown fences, no extra keys, no comments."""

_USER = """\
Classify every headline below.

AI-related if the title mentions (explicitly or implicitly):
- Core AI technologies: machine learning, neural / deep / transformer networks
- AI Applications: computer vision, NLP, robotics, autonomous driving, generative media
- AI hardware, GPU chip supply, AI data centers and infrastructure
- Companies or labs known for AI: OpenAI, DeepMind, Anthropic, xAI, NVIDIA, etc.
- AI models & products: ChatGPT, Gemini, Claude, Sora, Midjourney, DeepSeek, etc.
- New AI products and AI integration into existing products/services
- AI policy / ethics / safety / regulation / analysis
- Research results related to AI
- AI industry figures (Sam Altman, Demis Hassabis, etc.)
- AI market and business developments, funding rounds, partnerships centered on AI
- Any other news with a significant AI component

Non-AI examples: crypto, ordinary software, non-AI gadgets and medical devices, and anything else.
Input:
{input_text}"""


FILTER_URLS = PromptConfig(
    name="filter_urls",
    system_prompt=_SYSTEM,
    user_prompt=_USER,
    input_schema=FilterUrlsInput,
    output_schema=FilterUrlsOutput,
    default_engine="subagent",
)

register_prompt(FILTER_URLS)
