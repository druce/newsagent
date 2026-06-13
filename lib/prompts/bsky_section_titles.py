"""bsky_section_titles — generate punny section titles for Bluesky digest groups.

Ported from the legacy Bluesky notebook (cell 24): a witty, irreverent
culture-tech editorial voice that turns each topical group into a short,
pun-forward section title.
"""
from __future__ import annotations

import json
from typing import List

from pydantic import BaseModel, computed_field

from lib.llm import PromptConfig, register_prompt


class BskySectionGroup(BaseModel):
    label: str  # neutral topic label from the reorder step
    sample_texts: List[str]  # representative post texts from the group


class BskySectionTitlesInput(BaseModel):
    groups: List[BskySectionGroup]

    @computed_field
    @property
    def groups_json(self) -> str:
        return json.dumps([g.model_dump() for g in self.groups])


class BskySectionTitlesOutput(BaseModel):
    titles: List[str]  # one title per group, same order


_SYSTEM = """\
You are a seasoned culture-tech editor writing a witty, irreverent newsletter
about the AI industry (think: The Verge meets John Oliver).

You'll receive a list of topical groups, each with a plain-language label and a
few sample posts. For EACH group, generate one short section title that is:
\t• 1-7 words long
\t• Playful, witty, sharp, and meme-literate
\t• Can mix puns (clever or groan-worthy), alliteration, pop culture, business
\t  jargon, and double meaning
\t• Captures the essence or irony of that group's news

Examples of tone and style:
\t• "Sue-perintelligence" (Musk's $150B trial against OpenAI begins)
\t• "Tim Cooked" (Tim Cook stepping down)
\t• "AIPO-calypse now" (SpaceX, OpenAI, Anthropic preparing ~$3T in IPOs)
\t• "Recursive fundraising" (Recursive Superintelligence raises another round)
\t• "Bend it like Bengio"
\t• "Kumb-AI-a" (OpenAI's five-principle AGI framework)
\t• "CheatGPT" (students expelled for AI cheating)
\t• "Nay-mo to Waymo aquatic adventures" (Waymo recalls robotaxis for driving into floods)

Register:
\t• Compressed, sardonic, knowing. Sarcastic lowercase asides.
\t• Insider vocabulary without explanation (assume the reader knows MCP, CUDA,
\t  MoE, Reg S-P, etc.).
\t• Mix specialist terms freely with internet-speak ("sus," "kinda," "AF,"
\t  "stans") — the contrast is the joke.
\t• Never throat-clear, never both-sides, never hedge, no emojis.
\t• Amused, not contemptuous. The default stance is "this is absurd and I'm
\t  enjoying watching it" — not "this is bad and these people are bad."

Return exactly one title per group, in the same order as the input."""

_USER = """\
Generate one punny section title for each of these topical groups of Bluesky
posts:

{groups_json}

Return one title per group, in the same order."""


BSKY_SECTION_TITLES = PromptConfig(
    name="bsky_section_titles",
    system_prompt=_SYSTEM,
    user_prompt=_USER,
    input_schema=BskySectionTitlesInput,
    output_schema=BskySectionTitlesOutput,
    default_engine="subagent",
    reasoning_effort=4,
)

register_prompt(BSKY_SECTION_TITLES)
