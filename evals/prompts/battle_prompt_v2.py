"""battle_prompt_v2 — eval-only candidate rewrite of lib/prompts/battle.py.

Changes vs. v1 (the legacy port), from the 2026-11-13 audit:
- Holistic ranking against a short prioritised rubric instead of a 14-factor
  0/1/2 sum with factor-by-factor and by-id tie-breaks, which a low-effort
  flash-lite model cannot actually execute silently.
- Overlapping factors merged (impact/breadth/strategic/longevity;
  authority/verifiability); the triple stock-pick penalty stated once.
- Dropped factors that measure the article or the scraper rather than the
  story: Clarity (penalises paywalled "no content" summaries), Independent
  Corroboration (unobservable per item; the coverage step already boosts it),
  Timeliness (every item in a battle is from the same day).
- Explicit: input order means nothing; judge the event, not the write-up;
  every id exactly once.
Promoted to production (lib/prompts/battle.py) on 2026-11-13; kept here so the
eval runs tagged @v2 stay reproducible. evals.run.battle --prompt uses it.
"""
from __future__ import annotations

from lib.llm import PromptConfig, register_prompt
from lib.prompts.battle import BATTLE_PROMPT

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

BATTLE_PROMPT_V2 = PromptConfig(
    name="battle_prompt_v2",
    system_prompt=_SYSTEM,
    user_prompt=_USER,
    input_schema=BATTLE_PROMPT.input_schema,
    output_schema=BATTLE_PROMPT.output_schema,
    default_engine=BATTLE_PROMPT.default_engine,
    reasoning_effort=BATTLE_PROMPT.reasoning_effort,
)

register_prompt(BATTLE_PROMPT_V2)
