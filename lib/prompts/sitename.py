"""sitename — turn a bare domain into a human-friendly publisher name.

Ported from ~/projects/OpenAIAgentsSDK/prompts.py:106 (SITENAME) — the
legacy used a list-input / list-output schema so a single call can resolve
many domains at once.

Default engine: openrouter Sonnet 4.6. Trivial task but the user picked
Sonnet for the quality on edge cases (e.g. acronyms, less-known startups).
Routing via OpenRouter avoids `claude -p` (not covered under the Max plan)
and uses a paid OpenRouter API key.
"""
from __future__ import annotations

import json
from typing import List

from pydantic import BaseModel, Field, computed_field

from lib.llm import PromptConfig, register_prompt


class DomainItem(BaseModel):
    id: str
    domain: str


class SitenameInput(BaseModel):
    items: List[DomainItem] = Field(min_length=1)

    @computed_field
    @property
    def input_text(self) -> str:
        return json.dumps([item.model_dump() for item in self.items])


class SitenameRecord(BaseModel):
    id: str
    domain: str
    site_name: str


class SitenameOutput(BaseModel):
    results: List[SitenameRecord]


_SYSTEM = """\
# ROLE AND OBJECTIVE
You are a specialized content analyst tasked with identifying the site name of a given website domain.
For example, if the domain is 'washingtonpost.com', the site name would be 'Washington Post'.

Consider these factors:

If it's a well-known platform, return its official name or most commonly used or marketed name.
For less known sites, use context clues from the domain name.
Remove common prefixes like 'www.' or suffixes like '.com'.
Convert appropriate dashes or underscores to spaces.
Use proper capitalization for brand names.
If the site has rebranded, use the most current brand name.

## INPUT AND OUTPUT FORMAT
You will receive a list of JSON objects with fields "id" and "domain".
Return **only** a JSON object that satisfies the provided schema.
For each domain provided, you MUST return one element with the same id, the domain, and the site_name; do not skip any items.
Return elements in the same order they were provided.
No markdown, no markdown fences, no extra keys, no comments."""

_USER = """\
Please analyze the following domains according to these criteria:
{input_text}"""


SITENAME = PromptConfig(
    name="sitename",
    system_prompt=_SYSTEM,
    user_prompt=_USER,
    input_schema=SitenameInput,
    output_schema=SitenameOutput,
    default_engine="openrouter:anthropic/claude-sonnet-4.6",
    reasoning_effort=4,
)

register_prompt(SITENAME)
