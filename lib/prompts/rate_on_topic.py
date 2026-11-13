"""rate_on_topic — is this article about AI? (Decisions API predicate)

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
True if this news item is about artificial intelligence: AI models, products, research, AI companies
(OpenAI, Anthropic, Nvidia, etc.) and their deals, funding and executives, AI policy, law and regulation,
AI safety and security, AI data centers and AI infrastructure, AI chips (with an AI angle), AI wearables
and glasses, and robotics. False when AI is incidental, for general tech, finance or other news. If the
summary is empty or says 'no content', judge by the title alone."""

_USER = "{input_text}"


RATE_ON_TOPIC = PromptConfig(
    name="rate_on_topic",
    system_prompt=_SYSTEM.replace("\n", " "),
    user_prompt=_USER,
    input_schema=RatingItemInput,
    output_schema=PredicateOutput,
    default_engine="openai-decisions:gpt-6-luna",
    reasoning_effort=None,  # the Decisions API has no effort knob
)

register_prompt(RATE_ON_TOPIC)
