"""rate_decisions — the three rate_* axes as OpenAI Decisions API predicates
(gpt-6-luna), for evals.run.rate_decisions (one call per article, all three
questions). Production since 2026-11-13: each predicate's `probability` replaced
the gpt-4o-mini prompts' verbalized 0–1 `confidence`.

Mirrors the production predicates in lib/prompts/rate_*.py (the source of
truth); tests/test_evals_rate_decisions.py fails if they drift. The gold300 Opus
labels were made with the first wording (2026-11-13, before the rate_quality
stock-event sentence and the rate_on_topic company examples).

Empty / "no content" summaries: every predicate judges such items on the title
alone. Whether a missing summary should itself cost rating points is a policy
decision for rate.py, not something the model is asked to infer.
"""
from __future__ import annotations

NO_CONTENT = "If the summary is empty or says 'no content', judge by the title alone."

QUESTIONS: dict[str, str] = {
    'quality_low': (
        "True if this news item is low quality for an AI newsletter. Low quality means any of: its main purpose is to recommend buying, selling or holding a stock, to compare which stock to own, to discuss price targets, or to report an analyst rating action, even when it cites real revenue, growth or other financial data to support the pick; an investing thesis or pundit opinion piece with no discrete underlying news event (such as a launch, filing, deal, earnings release, executive or regulatory action, or research result); clickbait, hype or spam without concrete facts; pure speculation; or sponsored content, promotion or advertising. False for announcements, actions, facts, research and analysis, and for quotes from senior executives or officials that shed light on their future actions. A report of a significant corporate event, such as an executive change, deal or regulatory action, is news even if it also moved a stock; commentary on why a stock moved, or whether to buy it, is not. If the summary is empty or says 'no content', judge by the title alone."),
    'importance': (
        "True if this news item is important to readers of a daily AI newsletter (AI practitioners, investors and policymakers). Important means substantial impact and consequence (many people, companies, industries or countries affected, or a shift in competition, power or policy), real novelty (new capabilities, research results, products or decisions, not incremental updates), concrete substance from credible sources, large sums of money, or major security, safety or ethical consequences. False for stock picks, punditry that reports nothing new, listicles, how-tos, promotions and minor product updates. Judge the news, not the writing. If the summary is empty or says 'no content', judge by the title alone."),
    'on_topic': (
        "True if this news item is about artificial intelligence: AI models, products, research, AI companies (OpenAI, Anthropic, Nvidia, etc.) and their deals, funding and executives, AI policy, law and regulation, AI safety and security, AI data centers and AI infrastructure, AI chips (with an AI angle), AI wearables and glasses, and robotics. False when AI is incidental, for general tech, finance or other news. If the summary is empty or says 'no content', judge by the title alone."),
}
AXES = tuple(QUESTIONS)
