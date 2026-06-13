"""write_section — draft a newsletter section from a list of stories.

Ported from ~/projects/OpenAIAgentsSDK/prompts.py:824 (WRITE_SECTION).
Output simplified: returns markdown section directly instead of structured JSON.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List

from pydantic import BaseModel, computed_field

from lib.llm import PromptConfig, register_prompt
from lib.prompts._critic_schemas import SectionDraft


class WriteSectionInput(BaseModel):
    section_title: str
    stories: List[Dict[str, Any]]  # {title, url, summary, source, rating}

    @computed_field
    @property
    def stories_json(self) -> str:
        return json.dumps(self.stories, ensure_ascii=False, indent=2)


_SYSTEM = """\
You are a newsletter editor transforming a collection of raw news stories into a compelling, coherent topic summary.

# TASK
Transform the list of news stories into a well-structured newsletter section with a strong title, crisp headlines, and punchy summaries.

# INPUT
- a list of json objects sorted by rating (highest first)
- Each item: { "rating": number, "summary": string, "site_name": string, "url": string }

# WORKFLOW
1. **Section title**
- Infer the dominant, unifying topic of the set of stories (by count x rating).
- Write a punchy/punny section title <= 7 words reflecting that theme.
- Section titles use title case (Capitalize Each Main Word) — e.g. "Chips, Drones, and the China Race". Note this differs from headlines, which use sentence case.

2. **Cluster near-duplicates**: Stories covering the exact same facts/event/subject should be combined into a single story with multiple sources noted
- Form clusters of identical stories that cover the same subject (not general topic)
- Merge into one story per cluster with multiple links.
- sources = all URLs in the cluster, preserving original input order.
- Do not rewrite URLs or site_names; keep exactly as given.

3. **Write headlines**: For each story, write a crisp headline-style sentence derived from the short summary or summaries
- Make each headline <= 25 words: crystal clear, punchy, informative, specific, factual, active voice.
- COUNT THE WORDS of every headline before output and rewrite any over 25 words. Headlines over the limit are the single most common review failure — when in doubt, cut a clause, not specificity.
- Use sentence case (capitalize only the first word and proper nouns). Do NOT capitalize every word or use all-uppercase headlines.
- No clickbait, hype words ("groundbreaking," "revolutionary"), or jargon; neutral tone throughout. Spell out or briefly gloss any term a general tech reader wouldn't know.
- include key numbers/dates/entities if present

4. **Section size**: Aim for 2-7 headlines per section.

5. **Order for narrative**: Arrange headlines to create a logical, compelling flow
- biggest/most consequential overview first,
- related follow-ups/contrasts,
- end with forward-looking or lighter items.

6. **Prune off-topic and low-quality headlines**
- Drop headlines which don't fit with the primary topic and section title.
- Drop stories with rating < 3.0 unless the story adds to the section's narrative.

# OUTPUT
Output a markdown section in this format:
## <Section Title>
- <crisp headline> — [<Source>](url), [<Source>](url2)
- <next headline> — [<Source>](url)
..."""

_USER = """\
SECTION TITLE (proposed): {section_title}

STORIES (JSON, sorted by rating):
{stories_json}

Write the section as markdown."""


WRITE_SECTION = PromptConfig(
    name="write_section",
    system_prompt=_SYSTEM,
    user_prompt=_USER,
    input_schema=WriteSectionInput,
    output_schema=SectionDraft,
    default_engine="subagent",
    reasoning_effort=8,
)

register_prompt(WRITE_SECTION)
