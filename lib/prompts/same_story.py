"""same_story — decide whether two headlines report the same news event.

Used by `newsagent:crossdedupe` to confirm cross-day duplicates that an
embedding shortlist surfaced. Each pair carries an `id` (the today-candidate's
headline index) plus the title + one-line short_summary of the candidate (A)
and of the already-published prior article (B). The judge returns one boolean
per pair.

Structured like battle.py: a cheap default engine for the classic/in-process
path; the pipeline dispatch path renders system_prompt/user_prompt into batch
files consumed by Haiku subagents.
"""
from __future__ import annotations

import json
from typing import List

from pydantic import BaseModel, Field, computed_field

from lib.llm import PromptConfig, register_prompt


class SameStoryPair(BaseModel):
    id: str
    a_title: str
    a_short_summary: str
    b_title: str
    b_short_summary: str


class SameStoryInput(BaseModel):
    pairs: List[SameStoryPair] = Field(min_length=1)

    @computed_field
    @property
    def input_text(self) -> str:
        return json.dumps([p.model_dump() for p in self.pairs])


class SameStoryVerdict(BaseModel):
    id: str
    same: bool


class SameStoryOutput(BaseModel):
    results: List[SameStoryVerdict]


_SYSTEM = """\
# ROLE AND OBJECTIVE
You are a **news de-duplication judge** for an AI newsletter.
For each pair of items (A = a new candidate, B = a story already published in a
recent issue), decide whether A and B report the **same underlying news event**.

# WHAT COUNTS AS THE SAME STORY
- Same event covered by different outlets, with different wording, headline, or
  length → **same** (this is the case to catch: a syndicated or independently
  rewritten version of a story we already ran).
- A follow-up that only restates the earlier event with no material new
  development → **same**.

# WHAT IS NOT THE SAME STORY
- A genuinely new development, announcement, or milestone — even about the same
  company, people, or product → **different**.
- Two stories that merely share a topic, company, or theme but describe distinct
  events → **different**.

When uncertain, prefer **different** (do not suppress a story unless you are
confident it duplicates the published one).

# INPUT
A JSON array of pairs, each with: id, a_title, a_short_summary, b_title,
b_short_summary.

# OUTPUT
Return one verdict per input pair, echoing its id, in the provided JSON schema.
Think step-by-step **silently**; reveal only the JSON output."""

_USER = """\
Judge each pair below. Return exactly one verdict per pair, echoing each id, in \
the provided JSON schema.
{input_text}"""


SAME_STORY_PROMPT = PromptConfig(
    name="same_story",
    system_prompt=_SYSTEM,
    user_prompt=_USER,
    input_schema=SameStoryInput,
    output_schema=SameStoryOutput,
    default_engine="google:gemini-3.1-flash-lite",
    reasoning_effort=2,
)

register_prompt(SAME_STORY_PROMPT)
