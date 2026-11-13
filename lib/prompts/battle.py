"""battle — rank a set of news items by editorial relevance.

Originally ported from ~/projects/OpenAIAgentsSDK/prompts.py:562-610 (BATTLE_PROMPT).
Rewritten 2026-11-13 (eval candidate battle_prompt_v2): holistic ranking against
a short prioritised rubric instead of the legacy 14-factor 0/1/2 sum. On the
battle eval (gemini-3.5-flash-lite vs Opus gold) tau 0.581 -> 0.610 on gold400
and pair accuracy 0.780 -> 0.803 at 10 items; ~17% cheaper per call; nearly
insensitive to input order. See evals/prompts/battle_prompt_v2.py for the
rationale.
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
You are the editor of a daily AI newsletter read by AI practitioners, investors and policymakers.
You will receive a JSON array of news items, each with an id, a title and a summary.
The order of the array is random and means nothing.

# TASK
Rank the items from most to least important to these readers.
Judge the underlying news, not how well the item is written. A short, vague or empty summary
(for example "no content") is not a reason to rank a story lower; judge it on its title.

Weigh these, roughly in priority order:
1. Impact and consequence: how many people, companies, industries or countries it affects, how much,
   and for how long; whether it shifts competition, power or policy.
2. Novelty: new capabilities, research results, products or decisions that break new ground or change
   direction, as opposed to incremental updates.
3. Substance: concrete facts such as releases, benchmarks, data, filings, rulings, deals or official
   statements from credible sources, as opposed to speculation, hype or opinion.
4. Money: large funding rounds, revenue, valuations, spending or deal values.
5. Risk and safety: major security, safety, alignment or ethical consequences.

Rank these lower:
- Stock picks, price targets and market commentary with no underlying news event.
- Opinion and punditry that report nothing new.
- Listicles, how-tos, promotions and minor product updates.

# OUTPUT
Return the ids, most important first, in the JSON schema provided.
Include every input id exactly once and no other ids."""

_USER = """\
Rank these news items, most important first.
{input_text}"""


BATTLE_PROMPT = PromptConfig(
    name="battle_prompt",
    system_prompt=_SYSTEM,
    user_prompt=_USER,
    input_schema=BattleInput,
    output_schema=BattleOutput,
    default_engine="google:gemini-3.5-flash-lite",
    reasoning_effort=2,
)

register_prompt(BATTLE_PROMPT)
