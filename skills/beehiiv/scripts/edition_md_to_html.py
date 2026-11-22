"""Convert an edition markdown file (out/edition-<stamp>.md, from newsagent:edition, after
the editor's hand edits) into the digest-style HTML that build_image_clipboard.py and
build_doc.js consume.

    .venv/bin/python skills/beehiiv/scripts/edition_md_to_html.py out/latest-edition.md OUT.html

Mapping:
  `# Title`                  -> dropped (the post title is set via the API)
  `## Section`               -> <h1>Section</h1>   (buildDoc -> heading level 1)
  section boundaries         -> <hr />             (one content break BEFORE every heading
                                                    except the first; the md's own divider
                                                    lines are ignored so breaks never double)
  `![](../download/...)`     -> <p><img src='file:///abs/download/...'></p>
  `[text](url) - _Source_`   -> <p><a href='url'>text</a> - <em>Source</em></p>
  `Follow the latest ...`    -> footer paragraph linking the Bluesky profile
  `- Label: text` bullets     -> <ul><li>…</li></ul>  (editor notes from newsagent:edition:
                                alt kickers/titles/subtitles, flags, duplicates, missing
                                images; consecutive bullets form one list)
  `<!-- ... -->`             -> dropped (old-style editor notes)
  first plain line before
  the first `## ` heading    -> NOT in the body: printed as "subtitle" for setSubtitle()
                                (beehiiv's web_subtitle + email_preview_text fields)
  any other line             -> <p><em>line</em></p>

Exits non-zero on an item line it can't parse, so a malformed edit is caught before
anything reaches beehiiv. Prints {items, headings, hrs, imgs, links, lists, bullets,
subtitle} — the counts are the verification targets for the editor; subtitle is null when
the md has none.
"""
from __future__ import annotations

import html
import json
import os
import re
import sys

BSKY_FOOTER = ("<p>Follow the latest AI headlines via "
               "<a href='https://bsky.app/profile/druce.ai'>druce.ai on Bluesky</a></p>")
_ITEM = re.compile(r"^\[(.*)\]\((https?://[^)\s]+)\)\s+-\s+[_*](.+?)[_*]$")
_RULE = re.compile(r"^[-—–_*]{3,}$")
_IMG = re.compile(r"^!\[[^\]]*\]\((?:\.\./)?([^)]+)\)$")


def convert(md: str, root: str) -> tuple[str, dict]:
    out = []
    n = {"items": 0, "headings": 0, "hrs": 0, "imgs": 0, "links": 0,
         "lists": 0, "bullets": 0, "subtitle": None}
    bullets: list[str] = []

    def flush() -> None:
        if bullets:
            out.append("<ul>" + "".join(f"<li>{html.escape(b)}</li>" for b in bullets) + "</ul>")
            n["lists"] += 1; n["bullets"] += len(bullets)
            bullets.clear()

    for raw in md.split("\n"):
        line = raw.strip()
        if line.startswith("- ") and not _RULE.match(line):
            bullets.append(line[2:].strip())
            continue
        flush()
        # divider lines in any spelling (---, ----, ———, ***) — breaks come only from headings
        if not line or line.startswith("# ") or line.startswith("<!--") or _RULE.match(line):
            continue
        if line.startswith("## "):
            if n["headings"]:
                out.append("<hr />"); n["hrs"] += 1
            out.append(f"<h1>{html.escape(line[3:].strip())}</h1>"); n["headings"] += 1
            continue
        if m := _IMG.match(line):
            path = m[1] if m[1].startswith("/") else os.path.join(root, m[1])
            out.append(f"<p><img alt='image' src='file://{path}'></p>"); n["imgs"] += 1
            continue
        # editors sometimes wrap a whole item in _..._ while editing; unwrap it
        if line.startswith("_[") and line.endswith("_"):
            line = line[1:-1]
        if m := _ITEM.match(line):
            out.append(f"<p><a href='{html.escape(m[2], quote=True)}'>{html.escape(m[1])}</a>"
                       f" - <em>{html.escape(m[3])}</em></p>")
            n["items"] += 1; n["links"] += 1
            continue
        if line.startswith("Follow the latest"):
            out.append(BSKY_FOOTER); n["links"] += 1
            continue
        if line.startswith("["):
            raise ValueError(f"unparsed item line: {line[:100]}")
        if not n["headings"] and n["subtitle"] is None and not out:
            n["subtitle"] = line.strip("_*").strip()  # goes to the subtitle field, not the body
            continue
        # editor asides are written *like this* or _like this_; the <em> already italicizes
        if len(line) > 2 and line[0] == line[-1] and line[0] in "*_":
            line = line[1:-1].strip()
        out.append(f"<p><em>{html.escape(line)}</em></p>")
    flush()
    return "<html><body>\n" + "\n".join(out) + "\n</body></html>\n", n


def main() -> None:
    if len(sys.argv) != 3:
        sys.exit("usage: edition_md_to_html.py EDITION.md OUT.html")
    src, dst = sys.argv[1], sys.argv[2]
    root = os.path.abspath(".")  # image paths in the md are relative to the project root
    try:
        doc, counts = convert(open(os.path.realpath(src), encoding="utf-8").read(), root)
    except ValueError as e:
        sys.exit(str(e))
    with open(dst, "w", encoding="utf-8") as f:
        f.write(doc)
    print(json.dumps(counts))


if __name__ == "__main__":
    main()
