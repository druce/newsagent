"""coverage_boost — decide whether two of TODAY's articles report the same event.

Used by `newsagent:coverage` to count how many outlets independently covered the
same story today. Unlike `dedupe_vs_archive` (cross-day, one-line short_summaries, framed
A=new / B=already-published), this judge is symmetric: A and B are two articles
from the same day's batch, and it is shown each article's FULL bullet `summary`.
The judge returns one boolean per pair.
"""
from __future__ import annotations

import json
from typing import List

from pydantic import BaseModel, Field, computed_field

from lib.llm import PromptConfig, register_prompt


class CoverageBoostPair(BaseModel):
    id: str
    a_title: str
    a_summary: str
    b_title: str
    b_summary: str


class CoverageBoostInput(BaseModel):
    pairs: List[CoverageBoostPair] = Field(min_length=1)

    @computed_field
    @property
    def input_text(self) -> str:
        return json.dumps([p.model_dump() for p in self.pairs])


class CoverageBoostVerdict(BaseModel):
    id: str
    same: bool


class CoverageBoostOutput(BaseModel):
    results: List[CoverageBoostVerdict]


_SYSTEM = """\
# ROLE AND OBJECTIVE
You are a **news coverage judge** for an AI newsletter.
For each pair of items (A and B are two articles from TODAY's batch), decide
whether A and B report the **same underlying news event**. The goal is to count
how many outlets independently covered the same story.


# WHAT COUNTS AS THE SAME STORY
Two articles are the same story when both report the same specific news: the same
actor doing the same thing at the same time (the same announcement, filing, deal,
vote, ruling, launch or market move). A test: someone who read only A would already
know B's headline news, and vice versa.
- The same news covered by different outlets, with different wording, headline,
  angle or length → **same**. Extra quotes, background, reactions inside a report
  of that news don't change this.
- If a summary is missing, empty or says "no content", judge from the headlines:
  if they report the same specific news → **same**.

# WHAT IS NOT THE SAME STORY
- A new development, announcement, disclosure or milestone, even in the same
  ongoing story → **different**.
- Pieces that share a storyline but not the same specific pieces of news: analysis,
  opinion, explainers, profiles and "what it means" pieces about a big ongoing
  story, compared with a news report or with each other → **different**, unless
  both report the same new facts.
- Market moves in different markets or trading sessions (US vs Europe vs Asia,
  Monday vs Tuesday) → **different**.
- Stories that merely share a topic, company or theme → **different**.

When unsure, answer different: a "same" verdict raises both articles' ranking, so a wrong "same" gives a story credit for coverage it didn't get.

# INPUT
A JSON array of pairs, each with: id, a_title, a_summary, b_title, b_summary.
The summaries are multi-bullet article summaries.

# OUTPUT
Return one verdict per input pair, echoing its id, in the provided JSON schema.
"""

_USER = """Judge each pair below. Return exactly one verdict per pair, echoing each id, in \
the provided JSON schema.
{input_text}
"""


COVERAGE_BOOST_PROMPT = PromptConfig(
    name="coverage_boost",
    system_prompt=_SYSTEM,
    user_prompt=_USER,
    input_schema=CoverageBoostInput,
    output_schema=CoverageBoostOutput,
    default_engine="google:gemini-3.1-flash-lite",
    reasoning_effort=2,
)

register_prompt(COVERAGE_BOOST_PROMPT)
