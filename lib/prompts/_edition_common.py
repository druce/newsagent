"""Shared text for the newsagent:edition prompts (write_edition_section, edit_edition)."""
from __future__ import annotations

from pathlib import Path

STYLE_DIR = Path(__file__).resolve().parents[2] / "docs" / "style"
STYLE_FILES = ("style-guide.md", "gold-headlines.md", "house-style.md")

# Editor-supplied, verbatim. Do not paraphrase.
RULES = """\
- Be funny first: the best joke may bend length, technique or format rules within reason. These rules never bend:
- Never invent facts, numbers, quotes or links. Change a URL only to strip tracking parameters.
- Italic asides are the editor's: fix typos and broken markdown only; never reword, move or write new ones.
- Aim jokes at the powerful or absurd party, never at the people harmed.
- When an instruction is ambiguous, apply the most likely reading and state it in one line."""

# Overrides of the docs, decided by the editor for this command.
OVERRIDES = """\
- EVERY item gets a kicker (this overrides house-style's "about one in five").
- Under every item, propose exactly {kicker_count} alternative kickers in `alt_kickers`. Across an item's alternatives, mix the five techniques in gold-headlines.md (title/pop-culture swap, pun on a name, idiom or jargon twist, deadpan, ventriloquism); do not give the same technique twice in a row. Alternatives must differ in joke, not just wording.
- Never drop or merge an item. If an item looks like a duplicate of another, or like something house-style §5 would cut, keep it and set `flag` to one short line (e.g. "possible duplicate of 123", "§5: vendor PR without a hook"). Otherwise leave `flag` null.
- You do not write links. Each item is identified by its `id`; links are attached later from the database."""


def load_style_docs(style_dir: Path = STYLE_DIR) -> str:
    """The three editorial docs, concatenated with file headers."""
    parts = []
    for name in STYLE_FILES:
        parts.append(f"===== {name} =====\n{(style_dir / name).read_text().strip()}\n")
    return "\n".join(parts)


ITEM_FIELDS = """\
Each item object has exactly these fields:
  - `id` (int): copied unchanged from the input.
  - `kicker` (str): the best kicker, sentence case, no trailing colon. A question kicker ends in `?`.
  - `summary` (str): the house-style §2 item sentence.
  - `source` (str): the publication name per the house-style §2 label table (not a domain, page title or section tag).
  - `alt_kickers` (list of str): exactly {kicker_count} alternatives, same format as `kicker`.
  - `flag` (str or null)."""
