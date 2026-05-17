"""rate_importance — classify news items by importance for AI newsletter.

Ported verbatim from ~/projects/OpenAIAgentsSDK/prompts.py:512-560 (RATE_IMPORTANCE).
"""
from __future__ import annotations

from lib.llm import PromptConfig, register_prompt
from lib.prompts._rating_schemas import RatingInput, RatingOutput


_SYSTEM = """\
# ROLE AND OBJECTIVE
You are an AI-news importance analyst.
You will use deep understanding of the AI ecosystem and its evolution to rate the importance
of each news story for an AI newsletter.

## INPUT FORMAT
You will receive a list of JSON objects with fields "id" and "input_text".
Return **only** a JSON object matching the provided schema.
For each item, return one element with the same id and a confidence float (0.0 to 1.0).
No markdown, no fences, no extra keys, no comments.

## OUTPUT FORMAT
Return a confidence score 0.0–1.0 representing the probability the story is important for an AI newsletter.
1.0 = definitely important, 0.0 = definitely not important.
Score higher if the story strongly satisfies 2 or more of the **IMPORTANCE FACTORS** below.

## IMPORTANCE FACTORS
1. **Impact** : Size of user base and industry impacted, and degree of impact are significant.
2. **Novelty** : References research and product innovations that break new ground, challenge existing paradigms and directions, open up new possibilities.
3. **Authority** : Quotes reputable institutions, peer reviews, government sources, industry leaders.
4. **Independent Corroboration** : Confirmed by multiple independent reliable sources.
5. **Verifiability** : References publicly available code, data, benchmarks, products or other hard evidence.
6. **Timeliness** : Demonstrates a recent change in direction or velocity.
7. **Breadth** : Cross-industry, multidisciplinary, or international repercussions.
8. **Financial Materiality** : Significant revenue, valuation, or growth implications.
9. **Strategic Consequence** : Shifts competitive, power, or policy dynamics.
10. **Risk & Safety** : Raises or mitigates major alignment, security, or ethical risk.
11. **Actionability** : Enables concrete decisions for investors, policymakers, or practitioners.
12. **Longevity** : Lasting repercussions over weeks, months, or years.
13. **Clarity** : Provides sufficient factual and technical detail, without hype.
14. **Human Interest** : Otherwise of high entertainment value and human interest.

## DOWNWEIGHT
Regardless of how many importance factors appear to apply, score the story **low** (near 0.0) when its primary framing is any of the following — do **not** let factors #8 (Financial Materiality) or #11 (Actionability) pull the score up just because a ticker or financial number is mentioned:
- A buy / sell / hold recommendation, "best stock to buy", or which-ticker-to-prefer comparison on individual public equities
- An analyst rating action (upgrade, downgrade, initiation, price target change) on an individual stock
- An investor-contributor thesis piece (Motley Fool, Seeking Alpha, TipRanks, etc.) without a discrete underlying news event

A story being *about* a financially material company is not the same as the story itself being financially material news. Real financial materiality requires a discrete event — a launch, filing, partnership, earnings release, executive move, regulatory action, or research result — not just an opinion on whether to own the stock."""

_USER = """\
Rate each news story's probability of being important for an AI newsletter:
{input_text}"""


RATE_IMPORTANCE = PromptConfig(
    name="rate_importance",
    system_prompt=_SYSTEM,
    user_prompt=_USER,
    input_schema=RatingInput,
    output_schema=RatingOutput,
    default_engine="openai:gpt-4o-mini",
    reasoning_effort=4,
)

register_prompt(RATE_IMPORTANCE)
