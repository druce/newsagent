"""extract_summaries — bullet-point article summary + one-line headline.

Merges two legacy prompts:
  - EXTRACT_SUMMARIES (~/projects/OpenAIAgentsSDK/prompts.py:284)
  - ITEM_DISTILLER     (~/projects/OpenAIAgentsSDK/prompts.py:326)

Bundling both into a single call avoids a second LLM pass per article — the
model is already reading the full text for the bullet summary, so producing
a headline-style one-liner alongside is essentially free.
"""
from __future__ import annotations

import json
from typing import List

from pydantic import BaseModel, Field, computed_field

from lib.llm import PromptConfig, register_prompt


class ArticleItem(BaseModel):
    id: str
    title: str
    text: str


class ExtractSummariesInput(BaseModel):
    items: List[ArticleItem] = Field(min_length=1)

    @computed_field
    @property
    def input_text(self) -> str:
        return json.dumps([item.model_dump() for item in self.items])


class ArticleSummary(BaseModel):
    id: str
    short_summary: str  # ONE headline-style sentence, <=25 words, sentence case
    summary: str        # 3-bullet article summary


class ExtractSummariesOutput(BaseModel):
    summaries: List[ArticleSummary]


_SYSTEM = """\
You are an expert AI news analyst and headline writer. For each article you receive, produce TWO outputs:
  1. `short_summary` — ONE crisp, headline-style sentence (<=25 words, sentence case) that captures the key facts.
  2. `summary` — a 3-bullet article summary capturing the newsworthy content.

You will receive a list of JSON objects with fields "id", "title", and "text".
Return **only** a JSON object that satisfies the provided schema.
For each article provided, you MUST return exactly one element with the same id, the short_summary, and the summary.
Return elements in the same order they were provided.
No markdown, no markdown fences, no extra keys, no comments.

# short_summary requirements
- Length: <=25 words.
- Form: ONE crisp, headline-style sentence.
- Front-load the actor and the event; cut hedging clauses.
- Sentence case ONLY: capitalize the first word and proper nouns. No Title Case, no ALL-CAPS.
- Tone: objective, factual, specific. No hype, adjectives, speculation, or clickbait words ("groundbreaking", "revolutionary", "stunning", etc.).
- Active voice.
- NEVER mention the publication, outlet, or medium. The source name is displayed
  separately right next to the headline, so naming it — or stating the obvious fact
  that this is an article/blog post/X post — is redundant noise. This ban covers
  both positions:
    - Leading medium framing: "Blog post urges...", "Article analyzes...",
      "Op-ed argues...", "X post claims...", "Report says...", "Commentary argues...",
      "Video explains...", "Newsletter covers...".
    - Trailing outlet credit: "..., Washington Post reports", "..., according to
      Reuters", "..., per industry analysis", "..., FT analysis says",
      "..., TechCrunch writes".
  Instead, state the event or thesis directly, attributed to the actor INSIDE the
  story: not "Blog post urges open-source projects to adopt AI tools" but
  "Open-source projects should adopt subsidized frontier-lab AI tools for maintenance
  work before funding runs out".
  Exception: a named person or institution that is part of the story (researcher,
  executive, official, prominent analyst) may be cited by name or role when that
  identity is important context — "MIT economist warns...", "Andrej Karpathy
  argues...", "Goldman Sachs projects...". Never use the anonymous medium
  ("a blog post", "an article", "a study" with no named author) as the actor.
- No emojis or exclamation points.
- Content priority (in strict order, drop from the bottom if word limit forces omission):
    1. Concrete facts, figures, or statistics.
    2. Primary source attribution (people, institutions, reports cited within the article — not the news outlet).
    3. Timeframe or year, if stated.
    4. Comparisons or trends (e.g., "up from 17%").

# summary requirements
Write a summary with 3 bullet points (-) that capture ONLY the newsworthy content.

Include
- Key facts & technological developments
- Business implications and market impact
- Future outlook and expert predictions
- Practical applications and use cases
- Key quotes
- Essential background tied directly to the story

Exclude
- Navigation/UI text, ads, paywalls, cookie banners, JS, legal/footer copy, "About us", social widgets

Rules
- Accurately summarize original meaning
- Contents only, no additional commentary or opinion, no "the article discusses", "the author states"
- Maintain factual & neutral tone
- If no substantive news, return short_summary "no content" and summary "- no content"
- Output raw bullets (no code fences, no headings, no extra text — only the bullet strings)"""

_USER = """\
For each article below, produce a short_summary (one headline-style sentence) and a 3-bullet summary, per the schema:

{input_text}"""


EXTRACT_SUMMARIES = PromptConfig(
    name="extract_summaries",
    system_prompt=_SYSTEM,
    user_prompt=_USER,
    input_schema=ExtractSummariesInput,
    output_schema=ExtractSummariesOutput,
    default_engine="subagent",
    reasoning_effort=6,
)

register_prompt(EXTRACT_SUMMARIES)
