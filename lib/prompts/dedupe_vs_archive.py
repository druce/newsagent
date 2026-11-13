"""dedupe_vs_archive — decide whether two headlines report the same news event.

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


class DedupeVsArchivePair(BaseModel):
    id: str
    a_title: str
    a_short_summary: str
    b_title: str
    b_short_summary: str


class DedupeVsArchiveInput(BaseModel):
    pairs: List[DedupeVsArchivePair] = Field(min_length=1)

    @computed_field
    @property
    def input_text(self) -> str:
        return json.dumps([p.model_dump() for p in self.pairs])


class DedupeVsArchiveVerdict(BaseModel):
    id: str
    same: bool


class DedupeVsArchiveOutput(BaseModel):
    results: List[DedupeVsArchiveVerdict]


_SYSTEM = """\
# ROLE AND OBJECTIVE
You are a **news de-duplication judge** for an AI newsletter.
For each pair of items (A = a new candidate, B = a story already published in a
recent issue), decide whether A and B report the **same underlying news event**.

# WHAT COUNTS AS THE SAME STORY
A is the same story as B when A reports no news beyond what B already reported:
the same actor doing the same thing (the same announcement, filing, deal, vote,
ruling, launch or market move), even if A appears a day or more after B. A test:
someone who already read B would learn nothing new from A's headline news.
- The same news covered by a different outlet, with different wording, headline,
  angle or length → **same** (this is the case to catch: a syndicated or
  independently rewritten version of a story we already ran). Extra quotes,
  background or reactions inside a report of that news don't change this.
- A follow-up, recap, explainer or analysis piece that only restates B's news
  with no new facts → **same**.
- The same market move in the same trading session, reported with different
  figures (intraday vs close, premarket vs close, Nasdaq vs Nasdaq 100, different
  dollar totals) → **same**. Different figures for the same move are not new facts.
- If a summary is missing, empty or says "no content", judge from the headlines:
  if A's headline reports the same specific news as B's → **same**.

# WHAT IS NOT THE SAME STORY
- A new development, announcement, disclosure or milestone, even in the same
  ongoing story → **different**. If A contains B's news plus new facts, it is
  **different**.
- A different disclosure, product or detail from the same filing, announcement or
  event (another risk factor in the same S-1, another product at the same launch)
  → **different**.
- Market moves in different markets or trading sessions (US vs Europe vs Asia,
  Monday vs Tuesday) → **different**. Only a different session or market makes a
  market move different, not different figures.
- Stories that merely share a topic, company or theme → **different**.

When unsure, answer different: a "same" verdict drops A from the newsletter, so a
wrong "same" removes a story readers haven't seen.

# INPUT
A JSON array of pairs, each with: id, a_title, a_short_summary, b_title,
b_short_summary. The short summaries are one-line headline-style summaries.

# OUTPUT
Return one verdict per input pair, echoing its id, in the provided JSON schema.
"""

_USER = """Judge each pair below. Return exactly one verdict per pair, echoing each id, in \
the provided JSON schema.
{input_text}
"""


DEDUPE_VS_ARCHIVE_PROMPT = PromptConfig(
    name="dedupe_vs_archive",
    system_prompt=_SYSTEM,
    user_prompt=_USER,
    input_schema=DedupeVsArchiveInput,
    output_schema=DedupeVsArchiveOutput,
    default_engine="google:gemini-3.1-flash-lite",
    reasoning_effort=2,
)

register_prompt(DEDUPE_VS_ARCHIVE_PROMPT)
