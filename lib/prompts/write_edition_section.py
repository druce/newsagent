"""write_edition_section — one cluster of the newsagent:edition draft, in house style.

Adapts write_section: instead of a titled markdown section, returns one house-style
item per curated story (kicker + one-sentence summary + source label + alternative
kickers). Docs: docs/style/{style-guide,gold-headlines,house-style}.md.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List

from pydantic import BaseModel, computed_field

from lib.llm import PromptConfig, register_prompt
from lib.prompts._dispatch_schemas import EditionSection
from lib.prompts._edition_common import ITEM_FIELDS, OVERRIDES, RULES


class WriteEditionSectionInput(BaseModel):
    cat: str
    stories: List[Dict[str, Any]]  # {id, url, title, site_name, short_summary, summary, rating}
    kicker_count: int
    docs: str

    @computed_field
    @property
    def stories_json(self) -> str:
        return json.dumps(self.stories, ensure_ascii=False, indent=1)

    @computed_field
    @property
    def overrides(self) -> str:
        return OVERRIDES.format(kicker_count=self.kicker_count)

    @computed_field
    @property
    def item_fields(self) -> str:
        return ITEM_FIELDS.format(kicker_count=self.kicker_count)


_SYSTEM = """\
You are the editor of Skynet and Chill, a daily AI newsletter. You write the items for one cluster of today's issue: for each story, a kicker (the joke), a one-sentence factual summary, and a source label, in the house style below."""

_USER = """\
RULES
{rules}

RULES FOR THIS DRAFT (these override the docs where they conflict)
{overrides}

THE DOCS (style-guide.md = voice and stance; gold-headlines.md = kicker calibration and the repeat list; house-style.md = item format, mechanics, selection)
{docs}

TASK
Cluster: {cat}
The editor has already chosen these stories. For each one:
1. Summary. Write the house-style §2 sentence: one sentence, sentence case, present tense, active voice, actor first, 15–30 words (cap 35), at least one concrete specific, no terminal period, semicolon to join two facts. Start from `short_summary`; use the `summary` bullets only to check facts and pick the best specific. If `short_summary` and `summary` are empty or "no content" (failed download), build the item from `title` alone, add nothing beyond it, and set `flag` to "no content: summary from headline only". Every fact, number and name must appear in the input. Apply §3 mechanics (AI, US, $40 billion, %, American spelling, straight quotes, no em dashes).
2. Kicker. Read the gold kickers first and match their register: about 3 words, one recognizable reference, hooked on a concrete noun from the story, with a point of view aimed at the powerful or absurd party. It must land on its own and get funnier after the summary. Do not repeat the pun word in the summary (rewrite the summary's wording if needed, without changing facts). Avoid every frame on the gold-headlines repeat list.
3. Alternatives and flag, per the draft rules above.
4. Source label. Normalize `site_name` per the §2 table.

Order the items for narrative within the cluster: the most consequential first, lighter items last.

STORIES (JSON)
{stories_json}

OUTPUT
Return ONLY a JSON object: {{"cat": "<the cluster name above>", "items": [...]}} with one item per input story.
{item_fields}"""


WRITE_EDITION_SECTION = PromptConfig(
    name="write_edition_section",
    system_prompt=_SYSTEM,
    user_prompt=_USER,
    input_schema=WriteEditionSectionInput,
    output_schema=EditionSection,
    default_engine="subagent",
    reasoning_effort=7,
)

register_prompt(WRITE_EDITION_SECTION)


def render_user_prompt(inp: WriteEditionSectionInput) -> str:
    return _USER.format(rules=RULES, **inp.model_dump())
