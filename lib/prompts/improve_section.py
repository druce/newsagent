"""improve_section — revise a newsletter section based on a critique.

New prompt (no direct legacy equivalent).
Takes a section draft + critique feedback and returns a revised section.
"""
from __future__ import annotations

from pydantic import BaseModel

from lib.llm import PromptConfig, register_prompt
from lib.prompts._critic_schemas import SectionDraft


class ImproveSectionInput(BaseModel):
    section_markdown: str
    critique: str


_SYSTEM = """\
You are a newsletter editor revising a section draft based on a critique. Apply the critique's recommendations precisely. Do NOT add new information, do NOT change source URLs, do NOT introduce new stories. Return ONLY the revised markdown section.

House style (enforce even if the critique says otherwise):
- Section title (the "## " heading): title case (Capitalize Each Main Word), <= 7 words.
- Bullet headlines: sentence case (capitalize only the first word and proper nouns), <= 25 words each — count the words after revising."""

_USER = """\
Original section:
{section_markdown}

Critique:
{critique}

Return the revised section as markdown."""


IMPROVE_SECTION = PromptConfig(
    name="improve_section",
    system_prompt=_SYSTEM,
    user_prompt=_USER,
    input_schema=ImproveSectionInput,
    output_schema=SectionDraft,
    default_engine="subagent",
    reasoning_effort=6,
)

register_prompt(IMPROVE_SECTION)
