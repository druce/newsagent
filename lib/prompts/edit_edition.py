"""edit_edition — the final edit of the newsagent:edition draft (whole issue).

Adapts the rewrite step's whole-newsletter pass: takes every cluster from
write_edition_section, orders the issue, copy-edits each item against the house
docs and the pre-send checklist, de-duplicates kicker frames, and writes the subtitle.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List

from pydantic import BaseModel, computed_field

from lib.llm import PromptConfig, register_prompt
from lib.prompts._dispatch_schemas import EditionIssue
from lib.prompts._edition_common import ITEM_FIELDS, OVERRIDES, RULES


class EditEditionInput(BaseModel):
    sections: List[Dict[str, Any]]  # [{cat, items: [EditionItem + "facts": short_summary]}]
    kicker_count: int
    recent_kickers: List[str]
    docs: str

    @computed_field
    @property
    def sections_json(self) -> str:
        return json.dumps(self.sections, ensure_ascii=False, indent=1)

    @computed_field
    @property
    def recent_kickers_text(self) -> str:
        return "\n".join(f"- {k}" for k in self.recent_kickers) or "(none on file)"

    @computed_field
    @property
    def overrides(self) -> str:
        return OVERRIDES.format(kicker_count=self.kicker_count)

    @computed_field
    @property
    def item_fields(self) -> str:
        return ITEM_FIELDS.format(kicker_count=self.kicker_count)


_SYSTEM = """\
You are the managing editor of Skynet and Chill, doing the final edit of today's issue before it goes to the editor-in-chief. The items are drafted; your job is to make the issue read as one published issue: right order, clean copy, the funniest kickers and section titles, a subtitle that sells it."""

_USER = """\
RULES
{rules}

RULES FOR THIS DRAFT (these override the docs where they conflict)
{overrides}

THE DOCS (style-guide.md = voice and stance; gold-headlines.md = kicker calibration and the repeat list; house-style.md = issue anatomy, item format, mechanics, selection, pre-send checklist)
{docs}

KICKERS USED IN THE LAST 30 DAYS (do not reuse these, or their frames)
{recent_kickers_text}

THE FINAL EDIT — do these in order, as separate passes over the whole issue:

1. Shape the issue (house-style §1).
   - Pick the lead: the day's dominant story by editorial weight, not by input order. Put that cluster first, with 3–10 items.
   - Order the rest: markets/compute, policy, security, labor in the middle; culture, oddities and light items at the tail.
   - You may move an item to a better-fitting cluster, split a cluster, or rename a cluster (`cat` is a working label, not printed). Many single-item clusters are fine. Never drop or merge items.

2. Copy edit every item.
   - Check `summary` against §2 and §3: one sentence, 15–30 words (cap 35), actor first, present tense, at least one concrete specific, no terminal period, semicolon not a second sentence. Use AI, US/UK/UN, `$40 billion`, `%`, model and company names as the vendor writes them, American spelling, straight quotes, and no em dashes.
   - Fix grammar, typos and names (§6 items 2, 5 and 6).
   - Check each `source` against the §2 label table.
   - Each item carries `facts` (the original one-line summary) as ground truth. Your summary may not state anything that is not in `facts` or the drafted summary.

3. Punch up the kickers.
   - Read every kicker as a reader would: does it land on its own and get funnier after the summary?
   - Replace weak ones: labels instead of jokes, generic AI wordplay, kickers that need a fact the summary omits, or kickers that target the people harmed. You may promote one of the item's `alt_kickers` and refill the alternatives.
   - No two kickers in the issue may share a pun word or frame. Nothing may repeat the gold-headlines repeat list or the recent-kickers list above.
   - Keep the pun word out of the summary.
   - Make sure every item still has exactly the required number of distinct `alt_kickers` that mix the techniques.

4. Title every cluster (house-style §1; calibrate on the gold section titles; voice per style-guide.md).
   - Each cluster gets a printed `title`: 1–7 words, a pun, title/pop-culture swap, idiom twist or deadpan that captures the cluster as a whole, not just its first item.
   - The title must not reuse the pun word, reference or frame of any kicker in its cluster (especially the first one). No two titles in the issue may share a frame. Nothing may repeat the gold-headlines repeat list or the recent-kickers list.
   - Provide exactly {kicker_count} more in `title_alts`, mixing the techniques, differing in joke and not just wording.

5. Subtitle (house-style §1; calibrate on the gold subtitles).
   - Write 2–8 words riffing on the lead theme. A film, song or idiom twist works best.
   - Provide 5 more in `subtitle_alts`.
   - Read it aloud: no typos, and no prompt or instruction text (§6 item 1).

6. Pre-send checklist (§6).
   - Re-check names, source labels and grammar on the final text.
   - Keep or refine the `flag`s: a possible duplicate, or a §5 cut candidate. The editor-in-chief decides on those; you only flag.

7. Notes. Put each one-line statement of how you read an ambiguous instruction, plus anything the editor-in-chief must know, in `notes`. Keep it short; an empty list is fine.

THE DRAFT (JSON: clusters of items)
{sections_json}

OUTPUT
Return ONLY a JSON object:
{{"subtitle": str, "subtitle_alts": [5 strings], "clusters": [{{"cat": str, "title": str, "title_alts": [{kicker_count} strings], "items": [...]}}, ...], "notes": [str, ...]}}
Clusters are in issue order, and items are in order within each cluster. Every input item `id` appears exactly once. Do not include `facts` in the output.
{item_fields}"""


EDIT_EDITION = PromptConfig(
    name="edit_edition",
    system_prompt=_SYSTEM,
    user_prompt=_USER,
    input_schema=EditEditionInput,
    output_schema=EditionIssue,
    default_engine="subagent",
    reasoning_effort=8,
)

register_prompt(EDIT_EDITION)


def render_user_prompt(inp: EditEditionInput) -> str:
    return _USER.format(rules=RULES, **inp.model_dump())
