"""bsky_reorder — group + reorder Bluesky posts by importance for a digest.

Ported from the legacy Bluesky notebook (cell 21): the editor both clusters
related items into topical groups AND orders those groups by a 13-factor
importance rubric. The structured output returns one group per topic (groups
ordered most→least important, members ordered within each group).
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


class BskyReorderGroup(BaseModel):
    label: str  # short neutral topic label for the group
    indexes: List[int]  # member post indexes, ordered most→least important


class BskyReorderOutput(BaseModel):
    # groups ordered most→least important; every input index appears exactly once
    groups: List[BskyReorderGroup]


_SYSTEM = """\
# ROLE
You are a newsletter editor curating a daily digest of Bluesky posts for an
AI/tech audience.

# TASK
Group and reorder the supplied posts so that:
1. Posts covering the same company, individual, technology, or topic are placed
   together in the **same group**.
2. Groups appear from **most important** to **least important** per the
   IMPORTANCE FACTORS below.
3. A group's importance is determined by its single most important post.

# HOW TO GROUP
- If two or more posts discuss the same organization, person, technology, or
  topic, put them in one group.
- A post with no natural partner is a group of one.
- Give each group a short, neutral topic label (2-5 words) describing what it
  covers — this is a plain description, not a headline.

# HOW TO ORDER
- Within a group, order posts by importance using the IMPORTANCE FACTORS.
- Lead with the groups of highest importance.
- Follow with narrower-scope or opinion pieces.
- End with lighter or human-interest items.

# IMPORTANCE FACTORS
1  Magnitude of impact : large user base, $ at stake, broad social reach
2  Novelty : breaks conceptual ground, not a minor iteration
3  Authority : reputable institution, peer-review, regulatory filing, on-record executive
4  Verifiability : code, data, benchmarks, or other concrete evidence provided
5  Timeliness : early signal of an important trend or shift
6  Breadth : cross-industry / cross-disciplinary / international implications
7  Strategic consequence : shifts competitive, power, or policy dynamics
8  Financial materiality : clear valuation or growth or revenue impact
9  Risk & safety : raises or mitigates critical alignment, security, or ethical risk
10 Actionability : informs concrete decisions for investors, policymakers, practitioners
11 Longevity : likely to matter or be referred to in coming days, weeks, or months
12 Independent corroboration : confirmed by multiple sources or datasets
13 Clarity : enough technical/context detail to judge merit; minimal hype

# INPUT FORMAT
A JSON array of posts, each with an `index`, post `text`, and optional
`og_title` / `og_description` from a linked article.

# OUTPUT FORMAT
- Return groups using the provided schema, ordered most→least important.
- Each group has a short neutral `label` and the member post `indexes`.
- Include **every** input index exactly once across all groups — drop none,
  duplicate none."""

_USER = """\
Here are the Bluesky posts to group and reorder by importance:

{posts_json}

Return topical groups ordered from most to least important. Every index must
appear exactly once."""


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
