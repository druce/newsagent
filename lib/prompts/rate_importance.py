"""rate_importance — is this article important to AI-newsletter readers? (Decisions API predicate)

Replaced the gpt-4o-mini verbalized-confidence prompt (legacy port of
~/projects/OpenAIAgentsSDK/prompts.py) on 2026-11-13. The score is the
probability gpt-6-luna assigns to the predicate below via the OpenAI Decisions
API, one article per call. On the rate_decisions gold300 eval (300 archived
articles, Opus labels) luna's AUC was 0.92-0.95 on all three axes vs 0.63-0.72
for the old prompts. evals/prompts/rate_decisions.py mirrors this text
(tests/test_evals_rate_decisions.py fails if they drift).
"""
from __future__ import annotations

from lib.llm import PromptConfig, register_prompt
from lib.prompts._rating_schemas import PredicateOutput, RatingItemInput

_SYSTEM = """\
True if this news item is important to readers of a daily AI newsletter (AI practitioners, investors
and policymakers). Important means substantial impact and consequence (many people, companies,
industries or countries affected, or a shift in competition, power or policy), real novelty (new
capabilities, research results, products or decisions, not incremental updates), concrete substance
from credible sources, large sums of money, or major security, safety or ethical consequences. False
for stock picks, punditry that reports nothing new, listicles, how-tos, promotions and minor product
updates. Judge the news, not the writing. If the summary is empty or says 'no content', judge by the
title alone."""

_USER = "{input_text}"


RATE_IMPORTANCE = PromptConfig(
    name="rate_importance",
    system_prompt=_SYSTEM.replace("\n", " "),
    user_prompt=_USER,
    input_schema=RatingItemInput,
    output_schema=PredicateOutput,
    default_engine="openai-decisions:gpt-6-luna",
    reasoning_effort=None,  # the Decisions API has no effort knob
)

register_prompt(RATE_IMPORTANCE)
