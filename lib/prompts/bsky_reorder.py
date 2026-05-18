"""bsky_reorder — reorder Bluesky posts by importance for a digest.

Ported from the legacy Bluesky notebook (cell 21).
"""
from __future__ import annotations

import json
from typing import List, Optional

from pydantic import BaseModel, computed_field

from lib.llm import PromptConfig, register_prompt


class BskyPost(BaseModel):
    index: int
    text: str
    og_title: Optional[str] = None
    og_description: Optional[str] = None


class BskyReorderInput(BaseModel):
    posts: List[BskyPost]

    @computed_field
    @property
    def posts_json(self) -> str:
        return json.dumps([p.model_dump() for p in self.posts])


class BskyReorderOutput(BaseModel):
    indexes: List[int]  # in order most→least important


_SYSTEM = """\
You are an editor curating a daily digest of Bluesky posts for an AI/tech newsletter audience.

Given a list of posts with text and optional linked-article metadata, return the post indexes
in order of importance. Consider: significance of the news or insight, authoritative sources,
novelty, and clarity of communication.

Return ALL indexes — do not drop any. Order them from most important to least important."""

_USER = """\
Here are the Bluesky posts to reorder by importance:

{posts_json}

Return all indexes in order from most important to least important."""


BSKY_REORDER = PromptConfig(
    name="bsky_reorder",
    system_prompt=_SYSTEM,
    user_prompt=_USER,
    input_schema=BskyReorderInput,
    output_schema=BskyReorderOutput,
    default_engine="subagent",
    reasoning_effort=4,
)

register_prompt(BSKY_REORDER)
