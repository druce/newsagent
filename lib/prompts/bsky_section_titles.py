"""bsky_section_titles — generate punny section titles for Bluesky digest groups."""
from __future__ import annotations

import json
from typing import List

from pydantic import BaseModel, computed_field

from lib.llm import PromptConfig, register_prompt


class BskySectionGroup(BaseModel):
    index_range: str  # e.g. "1-5" or "6-10"
    sample_texts: List[str]  # first text of each post in the group


class BskySectionTitlesInput(BaseModel):
    groups: List[BskySectionGroup]

    @computed_field
    @property
    def groups_json(self) -> str:
        return json.dumps([g.model_dump() for g in self.groups])


class BskySectionTitlesOutput(BaseModel):
    titles: List[str]  # one title per group, same order


_SYSTEM = """\
You are a newsletter editor generating punny, descriptive section titles for groups of Bluesky posts.

Each title should be:
- 4-8 words long
- Capture the theme of the group
- Use light wordplay where appropriate
- Be engaging and fun while remaining informative

Return one title per group in input order."""

_USER = """\
Generate section titles for these groups of Bluesky posts:

{groups_json}

Return one punny, descriptive title per group, in the same order."""


BSKY_SECTION_TITLES = PromptConfig(
    name="bsky_section_titles",
    system_prompt=_SYSTEM,
    user_prompt=_USER,
    input_schema=BskySectionTitlesInput,
    output_schema=BskySectionTitlesOutput,
    default_engine="subagent",
    reasoning_effort=4,
)

register_prompt(BSKY_SECTION_TITLES)
