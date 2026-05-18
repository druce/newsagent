"""generate_title — generate a title for a newsletter.

Ported from ~/projects/OpenAIAgentsSDK/prompts.py:1174 (GENERATE_NEWSLETTER_TITLE).
"""
from __future__ import annotations

from pydantic import BaseModel

from lib.llm import PromptConfig, register_prompt


class GenerateTitleInput(BaseModel):
    newsletter_markdown: str


class NewsletterTitle(BaseModel):
    title: str  # 6-12 words, factual, active voice


_SYSTEM = """\
You are an expert newsletter editor specializing in crafting compelling titles for technology newsletters.

Your task is to read the full newsletter content and create a factual, thematic title that captures the day's major themes.

Title Guidelines:
- 6-12 words maximum
- Factual and informative
- Summarizes 2-3 major themes from the day's news
- Use semicolons to separate distinct, unrelated themes (like a list)
- Use conjunctions like "as", "while", "but", "and" to connect related themes
- Uses concrete, specific language (avoid "Updates", "News", "Roundup")
- Active voice, present tense when possible
- Authoritative and newsy

Good Examples:
- "Data Centers Expand Infrastructure But Regulators Circle"
- "OpenAI Challenges Microsoft; Nvidia Unveils New Chips; AI Regulation Intensifies"
- "AI Workforce Impact Grows as Cloud Spending Surges"
- "Semiconductor Shortage Eases as AI Investment Accelerates"

Bad Examples:
- "AI News Roundup" (vague, generic)
- "Silicon Valley's Week in Review" (not specific enough)
- "Chip Happens: The AI Hardware Edition" (too punny)"""

_USER = """\
Read this newsletter and generate a compelling title:

{newsletter_markdown}

Analyze the content carefully and identify the 2-3 dominant themes. Write a factual title (6-12 words) that captures these themes clearly and specifically."""


GENERATE_NEWSLETTER_TITLE = PromptConfig(
    name="generate_newsletter_title",
    system_prompt=_SYSTEM,
    user_prompt=_USER,
    input_schema=GenerateTitleInput,
    output_schema=NewsletterTitle,
    default_engine="subagent",
    reasoning_effort=8,
)

register_prompt(GENERATE_NEWSLETTER_TITLE)
