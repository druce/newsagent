"""Extract article links from a landing-page HTML document.

Ported from legacy ~/projects/OpenAIAgentsSDK/scrape.py:1144 (parse_source_file)
with one change: takes HTML content directly instead of reading from disk.
"""
from __future__ import annotations

import json
import re
import threading
from typing import Any, Optional
from urllib.parse import urljoin, urlparse

import trafilatura
from bs4 import BeautifulSoup

from lib.fetch.types import Article
from lib.utilities import clean_url


_DEFAULT_MINLENGTH = 28

_NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
    re.DOTALL,
)

# trafilatura parses every document through ONE module-global lxml parser
# (trafilatura/utils.py: HTML_PARSER). lxml parsers are not safe to share
# between threads — concurrent parses corrupt the parser's interned-name
# dictionary and abort the interpreter inside lxml's _fixHtmlDictNames
# ("pointer being freed was not allocated", SIGABRT). That killed the
# download step's 8-way Phase 1 mid-run.
#
# Serializing the parse costs ~1.5s per ~110 articles — negligible next to
# the network I/O those threads exist to overlap — and keeps trafilatura's
# extraction behaviour byte-for-byte identical. Any code that parses HTML
# through trafilatura must hold this lock.
HTML_PARSE_LOCK = threading.Lock()


def article_body_from_next_data(html: str) -> Optional[str]:
    """Return ``props.pageProps.data.articleBody`` from a Next.js page, if present.

    Some sites (notably HackerNoon) render article prose only inside the
    ``__NEXT_DATA__`` JSON payload rather than as server-side HTML, so
    trafilatura sees only author-bio boilerplate. This recovers the real body.
    Returns None when the blob is missing, unparseable, or has no articleBody.
    """
    m = _NEXT_DATA_RE.search(html or "")
    if not m:
        return None
    try:
        data = json.loads(m.group(1))
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    body = (
        ((data.get("props") or {}).get("pageProps") or {}).get("data") or {}
    ).get("articleBody")
    if isinstance(body, str) and body.strip():
        return body
    return None


def article_body_from_post_content(html: str) -> Optional[str]:
    """Extract text from a body split across several ``div.post-content`` blocks.

    Ars Technica breaks one article into multiple ``post-content`` divs with ad
    slots between them; trafilatura picks the first block as the whole article
    and drops the rest (~7 of 27 paragraphs). Joining the blocks into a single
    ``<article>`` before extraction recovers the full body. Returns None when
    the page has fewer than two such blocks.
    """
    soup = BeautifulSoup(html or "", "html.parser")
    blocks = soup.select("div.post-content")
    if len(blocks) < 2:
        return None
    title = str(soup.title) if soup.title else ""
    joined = (
        f"<html><head>{title}</head><body><article>"
        + "".join(str(b) for b in blocks)
        + "</article></body></html>"
    )
    with HTML_PARSE_LOCK:
        return trafilatura.extract(joined, include_comments=False, include_tables=False)


def extract_article_text(html: str) -> Optional[str]:
    """Extract article body text from a page's HTML.

    Tries trafilatura first, then two fallbacks: the ``__NEXT_DATA__``
    ``articleBody`` (for JS-rendered sites like HackerNoon) and the joined
    ``div.post-content`` blocks (for split bodies like Ars Technica). Returns
    whichever source yields the most text, so ordinary pages keep their
    trafilatura extract.
    """
    with HTML_PARSE_LOCK:
        traf = trafilatura.extract(html, include_comments=False, include_tables=False)
    candidates = [
        traf,
        article_body_from_next_data(html),
        article_body_from_post_content(html),
    ]
    return max((c for c in candidates if c), key=len, default=traf)


_HEADING_TAGS = re.compile(r"^h[1-6]$")
# Markup that flags a headline: <h1>-<h6>, or class/data-* values naming a
# headline/heading (WSJ data-testid="flexcard-headline", FT "o-teaser__heading",
# Bloomberg data-component="headline"). "subheadline"/"subheading" are deks.
_HEADLINE_ATTR = re.compile(r"(?<!sub)(?<!sub-)head(?:line|ing)", re.I)
# Labels above a headline: WSJ "EXCLUSIVE" flag, Bloomberg "The Big Take" eyebrow.
_LABEL_ATTR = re.compile(r"(?<![a-z])flag(?:container)?(?![a-z])|content-tag|eyebrow|kicker", re.I)
_DEK_ATTR = re.compile(r"summary|dek|standfirst|description|subhead", re.I)
_BLOCK_TAGS = frozenset({
    "address", "article", "aside", "blockquote", "br", "dd", "div", "dl", "dt",
    "figcaption", "figure", "footer", "h1", "h2", "h3", "h4", "h5", "h6",
    "header", "li", "main", "nav", "ol", "p", "section", "table", "td", "th",
    "tr", "ul",
})
_DEK_MIN_CHARS = 40
# A headline-marked text this long may be the title even under minlength, as
# long as some anchor for the same URL meets minlength (WSJ "Big Blue Blues").
_HEADLINE_MIN_CHARS = 10


def _text(el) -> str:
    """Element text with inline markup kept intact ("<em>Go</em> players") and
    a space at block boundaries (Bloomberg's eyebrow/headline/dek divs)."""
    parts: list[str] = []

    def walk(node) -> None:
        for child in node.children:
            name = getattr(child, "name", None)
            if name is None:
                if type(child).__name__ in ("NavigableString",):
                    parts.append(str(child))
            elif name in _SKIP_TAGS or _LABEL_ATTR.search(_attr_blob(child)):
                continue
            elif name in _BLOCK_TAGS:
                parts.append(" ")
                walk(child)
                parts.append(" ")
            else:
                walk(child)

    walk(el)
    return re.sub(r"\s+", " ", "".join(parts)).strip()


def _attr_blob(el) -> str:
    vals = []
    for k, v in el.attrs.items():
        if k == "class" or k.startswith("data-"):
            vals.append(" ".join(v) if isinstance(v, list) else str(v))
    return " ".join(vals)


_SKIP_TAGS = frozenset({"script", "style", "noscript", "template"})


def _is_headline_el(el) -> bool:
    if el.name in _SKIP_TAGS:
        return False
    return bool(_HEADING_TAGS.match(el.name or "")) or bool(_HEADLINE_ATTR.search(_attr_blob(el)))


def _anchor_parts(link) -> tuple[str, bool, str]:
    """(title text, headline-marked?, dek text inside the same anchor)."""
    inner = next((d for d in link.find_all(True) if _is_headline_el(d)), None)
    if inner is not None:
        dek_el = next((d for d in link.find_all(True)
                       if d is not inner and _DEK_ATTR.search(_attr_blob(d))), None)
        head = _text(inner)
        if dek_el is not None:
            return head, True, _text(dek_el)
        # No dek markup (The Register, WaPo): whatever else the anchor says.
        return head, True, _text(link).replace(head, " ", 1).strip()
    marked = _is_headline_el(link)
    parent = link.parent
    for _ in range(2):          # the anchor's own heading/teaser-heading wrapper
        if marked or parent is None:
            break
        marked = _is_headline_el(parent) and not _DEK_ATTR.search(_attr_blob(parent))
        parent = parent.parent
    return _text(link), marked, ""


def extract_article_links(
    html: str,
    source_cfg: dict[str, Any],
    source_name: str,
) -> list[Article]:
    """Parse landing-page HTML and return article links matching the source's include/exclude rules.

    Several anchors often share one URL (image, headline, dek). The title is
    the headline-marked text when the page marks one (heading tag, or a
    headline/heading class or data attribute); otherwise the longest text, as
    before. When a headline was identified, the dek (a summary element inside
    the headline anchor, or the longest other text for the URL) becomes
    ``rss_summary`` so the filter can see it.
    """
    minlength = source_cfg.get("minlength") or _DEFAULT_MINLENGTH
    exclude: list[str] = source_cfg.get("exclude") or []
    include: list[str] = source_cfg.get("include") or []
    base_page_url = source_cfg.get("url", "")

    soup = BeautifulSoup(html, "html.parser")

    base_tag = soup.find("base")
    base_href = base_tag.get("href") if base_tag else None
    base_url: str = str(base_href) if base_href else base_page_url

    # url -> list of (text, headline_marked, inner_dek), in document order
    groups: dict[str, list[tuple[str, bool, str]]] = {}
    for link in soup.find_all("a"):
        href_val = link.get("href")
        href: str = str(href_val) if href_val else ""
        if not href:
            continue
        text, marked, inner_dek = _anchor_parts(link)
        if not text or re.match(r"^\d+$", text):  # bare numbers (comment counts on Ars, etc.)
            continue
        absolute = clean_url(urljoin(base_url, href))
        if not absolute.startswith("http"):  # drop javascript:, mailto:, etc.
            continue
        if len(urlparse(absolute).path) <= 1:  # drop bare-domain links
            continue
        if len(text) < (_HEADLINE_MIN_CHARS if marked else minlength):
            continue
        if exclude and any(re.match(p, absolute) for p in exclude):
            continue
        if include and not any(re.match(p, absolute) for p in include):
            continue
        groups.setdefault(absolute, []).append((text, marked, inner_dek))

    out: list[Article] = []
    for url, cands in groups.items():
        if not any(len(t) >= minlength for t, _, _ in cands):
            continue  # short-only URLs stay excluded (nav fragments like "So Expensive")
        heads = [c for c in cands if c[1]]
        if heads:
            title = heads[0][0]
            dek_opts = [c[2] for c in heads if c[2]] + [t for t, m, _ in cands if not m]
            dek_opts = [d for d in dek_opts if d != title and title not in d and len(d) >= _DEK_MIN_CHARS]
            dek = max(dek_opts, key=len) if dek_opts else None
        else:
            # No headline markup: keep the LONGEST text. One anchor often wraps
            # a hero image caption ("Credit: VentureBeat made with Midjourney"),
            # another the real headline, which is almost always longer.
            title, dek = max((c[0] for c in cands), key=len), None
        out.append(Article(source=source_name, title=title, url=url, rss_summary=dek))
    return out
