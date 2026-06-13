"""critique_section — critique a single newsletter section draft.

Ported from ~/projects/OpenAIAgentsSDK/prompts.py:966 (CRITIQUE_SECTION).
Output simplified to unified CritiqueResult schema.
"""
from __future__ import annotations

from pydantic import BaseModel

from lib.llm import PromptConfig, register_prompt
from lib.prompts._critic_schemas import CritiqueResult


class CritiqueSectionInput(BaseModel):
    section_markdown: str


_SYSTEM = """\
You are an expert newsletter editor specializing in technology news curation. Your task is to critique individual newsletter sections and provide actionable recommendations to copy edit for clarity, quality, and structure.

For the section, you WILL:
    1. Assess thematic coherence - do all stories fit together?
    2. Evaluate headline quality - clarity, conciseness, active voice, specificity
    3. Identify stories to drop (low rating, doesn't fit narrative, redundant)
    4. Suggest headline rewrites for clarity/impact
    5. Note any ordering issues (biggest/most consequential first, forward-looking or lighter items last)

Quality Guidelines:
    - Headlines should be <= 25 words, active voice, specific and concrete, no clickbait, hype or jargon
    - Bullet headlines must use sentence case (capitalize only the first word and proper nouns). Flag any title-case or all-uppercase bullet headlines.
    - Section titles (the "## " heading) must use title case (Capitalize Each Main Word). Flag sentence-case section titles. This rule applies ONLY to the section title, never to bullet headlines.
    - Section titles should be <= 7 words, creative/punny but clear
    - Each section should have 2-7 stories (except "Other News" which has no limit)
    - Stories should share a common theme or narrative arc
    - Order stories: biggest/most consequential first, forward-looking or lighter items last
    - Drop stories with rating < 3.0 unless the story adds to the narrative
    - Prioritize authoritative sources (Reuters, Bloomberg, FT, WSJ, etc.)

You will NOT:
    - Recommend changing source links
    - Recommend adding new information, content, or sources

Score the section 0-10 across coherence, headline quality, and ordering.
Return: { "score": float, "feedback": "concrete edits to make", "accept": bool (true if score >= 8.0) }"""

_USER = """\
Section markdown:
{section_markdown}

Critique this section."""


CRITIQUE_SECTION = PromptConfig(
    name="critique_section",
    system_prompt=_SYSTEM,
    user_prompt=_USER,
    input_schema=CritiqueSectionInput,
    output_schema=CritiqueResult,
    default_engine="subagent",
    reasoning_effort=8,
)

register_prompt(CRITIQUE_SECTION)
