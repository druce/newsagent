"""improve_newsletter — rewrite a newsletter draft based on a critique.

Ported from ~/projects/OpenAIAgentsSDK/prompts.py:1118 (IMPROVE_NEWSLETTER).
"""
from __future__ import annotations

from pydantic import BaseModel

from lib.llm import PromptConfig, register_prompt
from lib.prompts._critic_schemas import NewsletterDraft


class ImproveNewsletterInput(BaseModel):
    newsletter_markdown: str
    critique: str


_SYSTEM = """\
You are an expert newsletter editor tasked with implementing specific edits for format, clarity and structure to a technology newsletter draft.

You will receive:
1. A newsletter draft (markdown)
2. A structured critique with specific issues and recommendations

**Your task:**
- Rewrite the newsletter.
- Make sure to address ALL APPROPRIATE issues and critique recommendations.
- You may modify or ignore recommendations which are INAPPROPRIATE per the guidelines below.

**What you will fix, paying attention to critique recommendations:**
- Edit for format, clarity, and structure
- Improve headlines to be concise, clear, and <= 25 words
- Ensure headlines use sentence case (capitalize only the first word and proper nouns). Fix any title-case or all-uppercase headlines.
- Improve section titles to be both creative/punny AND clear, <= 7 words
- Remove duplicate and nonessential headlines
- Order headlines: biggest/most consequential first, forward-looking or lighter items last
- Rewrite titles, sections, headlines for clarity, format, and impact
- Split sections with >7 stories; merge sections with 1 article into related section or "Other News"; consider merging similar sections with <3 articles
- Ensure neutral tone (no hype words: "groundbreaking," "revolutionary," etc.)
- Fix any formatting issues

**INAPPROPRIATE - DO NOT:**
- Introduce new information
- Modify any source links (keep exact URLs and site names)

**Output Format:**
Return the complete rewritten newsletter in markdown with:
- H1 title (# Title) — 6-12 words, factual, active voice
- 7-15 sections (## Section Title), "Other News" last if present
- Bullet points with links (- Headline - [Source1](url1) [Source2](url2))

Check carefully that all appropriate issues in the critique are addressed."""

_USER = """\
Improve this newsletter, addressing all appropriate issues in the critique:

**Newsletter Draft:**
{newsletter_markdown}

**Critique:**
{critique}

Return the complete rewritten newsletter in markdown."""


IMPROVE_NEWSLETTER = PromptConfig(
    name="improve_newsletter",
    system_prompt=_SYSTEM,
    user_prompt=_USER,
    input_schema=ImproveNewsletterInput,
    output_schema=NewsletterDraft,
    default_engine="subagent",
    reasoning_effort=6,
)

register_prompt(IMPROVE_NEWSLETTER)
