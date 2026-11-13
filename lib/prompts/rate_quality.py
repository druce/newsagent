"""rate_quality — is this article low quality for an AI newsletter? (Decisions API predicate)

Replaced the gpt-4o-mini verbalized-confidence prompt (legacy port of
~/projects/OpenAIAgentsSDK/prompts.py) on 2026-11-13. The score is the
probability gpt-6-luna assigns to the predicate below via the OpenAI Decisions
API, one article per call. On the rate_decisions gold300 eval (300 archived
articles, Opus labels) luna's AUC was 0.92-0.95 on all three axes vs 0.63-0.72
for the old prompts. evals/prompts/rate_decisions.py mirrors this text
(tests/test_evals_rate_decisions.py fails if they drift).

Revised 2026-11-13: a report of a significant corporate event that moved a
stock is news, while stock commentary is not (fixes "Nvidia stock falls 7%
after report board is preparing to oust Jensen Huang", scored 0.73 -> 0.00).
gold300 AUC 0.909 vs 0.927 for the first wording (-0.018, 95% CI -0.044 ..
+0.008; the Opus labels used the first wording).
"""
from __future__ import annotations

from lib.llm import PromptConfig, register_prompt
from lib.prompts._rating_schemas import PredicateOutput, RatingItemInput

_SYSTEM = """\
True if this news item is low quality for an AI newsletter. Low quality means any of: its main
purpose is to recommend buying, selling or holding a stock, to compare which stock to own, to
discuss price targets, or to report an analyst rating action, even when it cites real revenue,
growth or other financial data to support the pick; an investing thesis or pundit opinion piece
with no discrete underlying news event (such as a launch, filing, deal, earnings release, executive
or regulatory action, or research result); clickbait, hype or spam without concrete facts; pure
speculation; or sponsored content, promotion or advertising. False for announcements, actions,
facts, research and analysis, and for quotes from senior executives or officials that shed light on
their future actions. A report of a significant corporate event, such as an executive change, deal
or regulatory action, is news even if it also moved a stock; commentary on why a stock moved, or
whether to buy it, is not. If the summary is empty or says 'no content', judge by the title alone."""

_USER = "{input_text}"


RATE_QUALITY = PromptConfig(
    name="rate_quality",
    system_prompt=_SYSTEM.replace("\n", " "),
    user_prompt=_USER,
    input_schema=RatingItemInput,
    output_schema=PredicateOutput,
    default_engine="openai-decisions:gpt-6-luna",
    reasoning_effort=None,  # the Decisions API has no effort knob
)

register_prompt(RATE_QUALITY)
