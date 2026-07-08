"""same_story_sameday — decide whether two of TODAY's articles report the same event.

Used by `newsagent:coverage` to count how many outlets independently covered the
same story today. Unlike `same_story` (cross-day, one-line short_summaries, framed
A=new / B=already-published), this judge is symmetric: A and B are two articles
from the same day's batch, and it is shown each article's FULL bullet `summary`.
The judge returns one boolean per pair.
"""
from __future__ import annotations

import json
from typing import List

from pydantic import BaseModel, Field, computed_field

from lib.llm import PromptConfig, register_prompt


class SameStorySamedayPair(BaseModel):
    id: str
    a_title: str
    a_summary: str
    b_title: str
    b_summary: str


class SameStorySamedayInput(BaseModel):
    pairs: List[SameStorySamedayPair] = Field(min_length=1)

    @computed_field
    @property
    def input_text(self) -> str:
        return json.dumps([p.model_dump() for p in self.pairs])


class SameStorySamedayVerdict(BaseModel):
    id: str
    same: bool


class SameStorySamedayOutput(BaseModel):
    results: List[SameStorySamedayVerdict]


_SYSTEM = """\
# ROLE AND OBJECTIVE
You are a **news coverage judge** for an AI newsletter.
For each pair of items (A and B are two articles from TODAY's batch), decide
whether A and B report the **same underlying news event**. The goal is to count
how many outlets independently covered the same story.

# WHAT COUNTS AS THE SAME STORY
- The same event covered by different outlets, with different wording, headline,
  angle, or length → **same** (this is exactly the case to catch: independent
  editorial coverage of one event).
- A piece that only restates the same event with no material new development
  → **same**.

# WHAT IS NOT THE SAME STORY
- A genuinely new development, announcement, or milestone — even about the same
  company, people, or product → **different**.
- Two stories that merely share a topic, company, or theme but describe distinct
  events → **different**.

When uncertain, prefer **different** (do not merge two stories unless you are
confident they are the same event).

# INPUT
A JSON array of pairs, each with: id, a_title, a_summary, b_title, b_summary.
The summaries are multi-bullet article summaries.

# OUTPUT
Return one verdict per input pair, echoing its id, in the provided JSON schema.
Think step-by-step **silently**; reveal only the JSON output."""

_USER = """\
Judge each pair below. Return exactly one verdict per pair, echoing each id, in \
the provided JSON schema.
{input_text}"""


SAME_STORY_SAMEDAY_PROMPT = PromptConfig(
    name="same_story_sameday",
    system_prompt=_SYSTEM,
    user_prompt=_USER,
    input_schema=SameStorySamedayInput,
    output_schema=SameStorySamedayOutput,
    default_engine="google:gemini-3.1-flash-lite",
    reasoning_effort=2,
)

register_prompt(SAME_STORY_SAMEDAY_PROMPT)
