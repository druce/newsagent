import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "edition_md_to_html",
    Path(__file__).resolve().parents[1] / "skills/beehiiv/scripts/edition_md_to_html.py")
conv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(conv)

MD = """# AI Reading for Thursday November 19
Going public
- Alt subtitles: a | b
- Note: kept both clusters

## Claude nine
- Alt titles: x | y

![](../download/edition-images/a.jpg)
[Ant-icipation: Anthropic prices at $83](https://x.com/1) - _The New York Times_
- Alt kickers: p | q | r
- Flag: possible duplicate of 2 & <3>

---

## Fed up
- No image: no og:image
[Plain summary](https://x.com/2) - _Bloomberg_
<!-- old-style comment -->
*An aside*

Follow the latest AI headlines via druce.ai on Bluesky
"""


def test_bullets_become_lists_and_counts():
    html, n = conv.convert(MD, "/root")
    assert n["subtitle"] == "Going public"
    assert n["headings"] == 2 and n["hrs"] == 1 and n["imgs"] == 1
    assert n["items"] == 2 and n["links"] == 3
    # notes(2) + title alts(1) + item bullets(2) + no-image(1)
    assert n["lists"] == 4 and n["bullets"] == 6
    assert ("<ul><li>Alt subtitles: a | b</li><li>Note: kept both clusters</li></ul>"
            in html)
    assert "<li>Flag: possible duplicate of 2 &amp; &lt;3&gt;</li>" in html
    assert "old-style comment" not in html
    assert "<p><em>An aside</em></p>" in html
    # a list closes before the next block
    assert html.index("</ul>\n<hr />") > 0


def test_rule_lines_are_not_bullets():
    _, n = conv.convert("x\n\n## A\n[t](https://u) - _S_\n---\n- real bullet\n", "/r")
    assert n["hrs"] == 0 and n["bullets"] == 1
