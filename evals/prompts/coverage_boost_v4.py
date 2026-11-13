"""coverage_boost_v4 — eval-only candidate: three-way same / related / different.

"same" is production's definition (lib/prompts/coverage_boost.py as of
2026-11-12: same specific news, read-only-A test). "related" is new: the same
specific, nameable story told with different developments, facts or analysis.
It would earn partial coverage credit (proposed 0.25) instead of none.
Not registered for the pipeline; evals.run.challenge uses it.
"""
from __future__ import annotations

from typing import List, Literal

from pydantic import BaseModel

from lib.llm import PromptConfig, register_prompt
from lib.prompts.coverage_boost import CoverageBoostInput


class CoverageBoostV4Verdict(BaseModel):
    id: str
    relation: Literal["same", "related", "different"]

    @property
    def same(self) -> bool:
        return self.relation == "same"


class CoverageBoostV4Output(BaseModel):
    results: List[CoverageBoostV4Verdict]


_SYSTEM = """\
You are a news coverage judge for an AI newsletter.
For each pair of items (A and B are two articles from TODAY's batch), decide how
they relate. The goal is to measure how much coverage each story got today:
outlets reporting the same news count fully, and other pieces on the same specific
story count partially.

# THREE VERDICTS

**same**: both report the same specific news: the same actor doing the same thing
at the same time (the same announcement, filing, deal, vote, ruling, launch or
market move). A test: someone who read only A would already know B's headline
news, and vice versa.
- The same news covered by different outlets, with different wording, headline,
  angle or length → same. Extra quotes, background or reactions inside a report
  of that news don't change this.
- If a summary is missing, empty or says "no content", judge from the headlines:
  if they report the same specific news → same.

**related**: both articles are about the same specific story (one identifiable
event, decision, deal, scandal, filing or launch that you could name in a phrase,
such as "Jensen Huang spy scandal" or "Anthropic IPO") but report different
developments, facts or analysis of it: a follow-up, a reaction, a consequence,
an analysis, opinion, explainer or profile, or the same market move in a
different market or session.
Test: you can name the one specific story both articles are about, and it is
the main subject of both.

**different**: anything else. Sharing only a company, person, industry, technology
or theme is different: two Nvidia stories about unrelated events, two
AI-regulation stories about different bills, or two "AI bubble" pieces with
different news pegs are different.

When torn between same and related, answer related. When torn between related
and different, answer different. A "same" earns full coverage credit and
"related" a fraction, so overstating either gives a story credit it didn't earn.

# INPUT
A JSON array of pairs, each with: id, a_title, a_summary, b_title, b_summary.
The summaries are multi-bullet article summaries.

# OUTPUT
Return one verdict per input pair, echoing its id, with relation set to "same",
"related" or "different", in the provided JSON schema.
"""

_USER = """Judge each pair below. Return exactly one verdict per pair, echoing each id, in \
the provided JSON schema.
{input_text}
"""


PROMPT = PromptConfig(
    name="coverage_boost_v4",
    system_prompt=_SYSTEM,
    user_prompt=_USER,
    input_schema=CoverageBoostInput,
    output_schema=CoverageBoostV4Output,
    default_engine="subagent",
    reasoning_effort=2,
)

register_prompt(PROMPT)
