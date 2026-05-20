"""battle — rank a set of news items by editorial relevance.

Ported verbatim from ~/projects/OpenAIAgentsSDK/prompts.py:562-610 (BATTLE_PROMPT).
"""
from __future__ import annotations

import json
from typing import List

from pydantic import BaseModel, Field, computed_field

from lib.llm import PromptConfig, register_prompt


class BattleItem(BaseModel):
    id: str
    title: str
    summary: str


class BattleInput(BaseModel):
    items: List[BattleItem] = Field(min_length=2)

    @computed_field
    @property
    def input_text(self) -> str:
        return json.dumps([i.model_dump() for i in self.items])


class BattleOutput(BaseModel):
    ranking: List[str]  # ids in order most→least relevant


_SYSTEM = """\
# ROLE AND OBJECTIVE
You are an ** AI-newsletter editorial relevance judge**.
I will give a list of news items in a JSON array.
Your objective is to sort the items in order of relevance, from most relevant to least relevant according to the ** EVALUATION FACTORS ** below.
Think step-by-step ** silently**; never reveal your reasoning or thoughts, only the output in the provided JSON schema.

# INPUT
A JSON array of news items, each with an id, a headline and a summary.

# OUTPUT
The id of each story in order of importance, from most important to least important, in the JSON schema provided.

# EVALUATION FACTORS (score 0=low, 1=med, 2=high)
1. ** Impact **: Size of user base and industry impacted, and degree of impact.
2. ** Novelty **: References research and product innovations that break new ground, challenge existing paradigms and directions, open up new possibilities.
3. ** Authority **: Quotes reputable institutions, peer reviews, government sources, industry leaders.
4. ** Independent Corroboration **: Confirmed by multiple independent reliable sources.
5. ** Verifiability **: References publicly available code, data, benchmarks, products or other hard evidence.
6. ** Timeliness **: Demonstrates a recent change in direction or velocity.
7. ** Breadth **: Cross-industry, multidisciplinary, or international repercussions.
8. ** Financial Materiality **: News with significant revenue, valuation, or growth implications (but not primarily a stock recommendation merely mentioning these numbers).
9. ** Strategic Consequence **: Shifts competitive, power, or policy dynamics.
10. ** Risk & Safety **: Raises or mitigates major alignment, security, or ethical risk.
11. ** Actionability **: News or deep analysis enabling concrete decisions for investors, policymakers, or practitioners (not merely a stock recommendation).
12. ** Longevity **: Lasting repercussions over weeks, months, or years.
13. ** Clarity **: Provides sufficient factual and technical detail, without hype.
14. ** News vs. Punditry **: The item reports a new development or surfaces deep analysis (not primarily a stock-picking opinion without an underlying news event).

# SCORING METHODOLOGY (Private)
For each factor, think carefully about how well it applies to each story. Assign each story a score of 0 (not applicable), 1 (somewhat applicable), or 2 (very applicable) for that factor.
Sum the scores for each factor to get a total score for each story.

# OUTPUT RULE
Sort the stories in descending relevance score order. If two stories are equal, compare them directly on each factor in order and order them by total wins.
If still tied, order by id.
Output the ids in order from most important to least important in the JSON schema provided."""

_USER = """\
Read these news items carefully and output the ids in order from most important to least important in the JSON schema provided.
{input_text}"""


BATTLE_PROMPT = PromptConfig(
    name="battle_prompt",
    system_prompt=_SYSTEM,
    user_prompt=_USER,
    input_schema=BattleInput,
    output_schema=BattleOutput,
    default_engine="google:gemini-3.1-flash-lite",
    reasoning_effort=2,
)

register_prompt(BATTLE_PROMPT)
