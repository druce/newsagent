"""bsky_headlines — punny rewrites of Bluesky digest headlines.

Ported from the legacy Bluesky notebook (cell 24): a witty, irreverent
culture-tech editorial voice that turns each headline into a short, pun-forward
rewrite. One rewrite per input headline, in the same order. These rewrites are a
SEPARATE artifact — they are not injected into the digest HTML, which uses the
plain post text (matching the legacy skynet.html).
"""
from __future__ import annotations

import json
from typing import List

from pydantic import BaseModel, computed_field

from lib.llm import PromptConfig, register_prompt


class BskyHeadlinesInput(BaseModel):
    headlines: List[str]  # the digest headlines (post texts), in final order

    @computed_field
    @property
    def headlines_json(self) -> str:
        return json.dumps(
            [{"index": i, "headline": h} for i, h in enumerate(self.headlines)]
        )


class BskyHeadlinesOutput(BaseModel):
    headlines: List[str]  # one punny rewrite per input headline, same order


_SYSTEM = """\
You are a seasoned culture-tech editor writing a witty, irreverent newsletter
about the AI industry (think: The Verge meets John Oliver).

You'll receive a list of article headlines. For EACH headline, generate one
short, funny, pun-heavy, or referential rewrite that is:
\t• 1-7 words long
\t• Playful, witty, sharp, and meme-literate
\t• Can mix puns (clever or groan-worthy), alliteration, pop culture, business
\t  jargon, and double meaning
\t• Captures the essence or irony of that headline's news

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

Return exactly one rewrite per headline, in the same order as the input."""

_USER = """\
Generate one punny rewrite for each of these headlines, in the same order:

{headlines_json}

Return one rewrite per headline, in the same order."""


BSKY_HEADLINES = PromptConfig(
    name="bsky_headlines",
    system_prompt=_SYSTEM,
    user_prompt=_USER,
    input_schema=BskyHeadlinesInput,
    output_schema=BskyHeadlinesOutput,
    default_engine="subagent",
    reasoning_effort=4,
)

register_prompt(BSKY_HEADLINES)
