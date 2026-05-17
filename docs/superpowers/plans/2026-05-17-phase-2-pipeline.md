# Phase 2 — Non-LLM Pipeline (`gather`, `download`, `send`) + Recovery Skills

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up the three non-LLM pipeline steps (`gather`, `download`, `send`) so the system can produce a dummy newsletter end-to-end (`init → gather → download → [skip LLM steps] → send`), plus the three state-inspection/recovery skills (`show`, `resume`, `reset`) that recover from broken runs.

**Architecture:**
- `gather` fetches headlines from configured sources. Three source types: `rss`, `html`, `rest`. HTML uses **adaptive scraping**: try `httpx + trafilatura` first, fall back to Playwright on failure, persist the working method in `sites.scrape_method` so subsequent runs skip straight to the right method. Outputs `headline_data` rows + writes new URLs into the `urls` table.
- `download` fetches the full article HTML for each kept URL using Playwright, extracts main text via `trafilatura`, writes to `download/<id>.txt`. Cosine-similarity dedup is **deferred to Phase 3** (needs embeddings, which need an LLM/HTTP API key).
- `send` renders `final_newsletter` as styled HTML, writes to `out/YYYY-MM-DD.html` with a `latest.html` symlink. **No Gmail in Phase 2** — preview only. Add `--notify` flag when the user explicitly wants email.
- `show`, `resume`, `reset` are thin wrappers over existing `NewsletterAgentState` methods.

**Hard constraints (per memory):**
- No Anthropic SDK / API. (No LLM calls in Phase 2 anyway.)
- Adaptive scraping with per-site SQLite memoization (`sites.scrape_method`).

**Tech Stack:** httpx, trafilatura, feedparser, beautifulsoup4, playwright (chromium), Pydantic, Click, pytest + respx.

**Reference (read, don't import):**
- `~/projects/OpenAIAgentsSDK/fetch.py` — `Fetcher.fetch_rss`, `fetch_html`, `fetch_api`, `fetch_all`
- `~/projects/OpenAIAgentsSDK/scrape.py` — `scrape_url`, `scrape_urls_concurrent`, `parse_source_file`, content extraction
- `~/projects/OpenAIAgentsSDK/utilities.py` — `format_newsletter_email`, `export_newsletter_html`
- `~/projects/OpenAIAgentsSDK/sources.yaml` and `./sources.yaml`

## File structure

| Path | Purpose |
|---|---|
| `lib/db.py` (modify) | Add `Site` dataclass with `scrape_method` field; add `scrape_method TEXT` column to `sites` schema |
| `lib/fetch/__init__.py` | Package marker |
| `lib/fetch/rss.py` | `fetch_rss(rss_url) -> list[Article]` — feedparser-based |
| `lib/fetch/html.py` | `fetch_html(source_cfg, scrape_method) -> list[Article]` — trafilatura+httpx → Playwright fallback |
| `lib/fetch/rest.py` | `fetch_rest(source_cfg) -> list[Article]` — generic JSON API (NewsAPI, etc.) |
| `lib/fetch/playwright_runner.py` | Thin Playwright wrapper: `fetch_url_with_playwright(url) -> str` (HTML body) |
| `lib/fetch/extract.py` | `extract_article_links(html, source_cfg) -> list[Article]` — BeautifulSoup link extraction with include/exclude regex |
| `lib/steps/gather.py` | CLI: orchestrates RSS/HTML/REST fetchers across all enabled sources, updates `sites.scrape_method` on fallback, writes `headline_data` |
| `lib/steps/download.py` | CLI: per-URL Playwright fetch + trafilatura main-text extraction; writes to `download/` |
| `lib/steps/send.py` | CLI: HTML render of `final_newsletter`, write to `out/YYYY-MM-DD.html` + symlink |
| `lib/steps/show.py` | CLI: dump full state record for a session |
| `lib/steps/resume.py` | CLI: clear errors and re-enter pipeline at first incomplete step |
| `lib/steps/reset.py` | CLI: reset step(s) to NOT_STARTED |
| `skills/{gather,download,send,show,resume,reset}/SKILL.md` | Agent-facing contracts |
| `tests/test_db_site.py` | Site dataclass CRUD + scrape_method upserts |
| `tests/test_fetch_rss.py` | RSS parser (canned XML fixture) |
| `tests/test_fetch_html.py` | HTML fetch with adaptive fallback (httpx mocks via respx + Playwright stub) |
| `tests/test_fetch_rest.py` | REST API fetch (httpx mocks) |
| `tests/test_extract.py` | Link extraction with include/exclude regex |
| `tests/test_step_gather.py` | gather CLI end-to-end with mocked source fetches |
| `tests/test_step_download.py` | download CLI with mocked Playwright |
| `tests/test_step_send.py` | send CLI HTML render + file output |
| `tests/test_step_show.py` | show CLI |
| `tests/test_step_resume.py` | resume CLI |
| `tests/test_step_reset.py` | reset CLI |

**Add dependencies** to `pyproject.toml`: `trafilatura>=1.10`, `feedparser>=6.0`, `beautifulsoup4>=4.12`, `playwright>=1.49`.

---

## Task 1: Schema migration — `sites.scrape_method` + `Site` dataclass

**Files:**
- Modify: `lib/db.py`
- Create: `tests/test_db_site.py`

- [ ] **Step 1: Write the failing tests**

Create `/Users/drucev/projects/news_agent/tests/test_db_site.py`:
```python
import sqlite3
from lib.db import init_db, Site


def test_sites_schema_has_scrape_method_column(tmp_db):
    init_db(tmp_db)
    with sqlite3.connect(tmp_db) as conn:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(sites)").fetchall()]
    assert "scrape_method" in cols


def test_site_upsert_and_get(tmp_db):
    init_db(tmp_db)
    with sqlite3.connect(tmp_db) as conn:
        Site(domain="example.com", name="Example", scrape_method=None).upsert(conn)
        s = Site.get_by_domain(conn, "example.com")
    assert s is not None
    assert s.name == "Example"
    assert s.scrape_method is None


def test_site_upsert_updates_scrape_method(tmp_db):
    init_db(tmp_db)
    with sqlite3.connect(tmp_db) as conn:
        Site(domain="bloomberg.com", name="Bloomberg",
             scrape_method=None).upsert(conn)
        Site(domain="bloomberg.com", name="Bloomberg",
             scrape_method="playwright").upsert(conn)
        s = Site.get_by_domain(conn, "bloomberg.com")
    assert s.scrape_method == "playwright"


def test_site_get_missing(tmp_db):
    init_db(tmp_db)
    with sqlite3.connect(tmp_db) as conn:
        assert Site.get_by_domain(conn, "nope.com") is None
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/drucev/projects/news_agent
.venv/bin/pytest tests/test_db_site.py -v
```
Expected: `ImportError: cannot import name 'Site'`.

- [ ] **Step 3: Update `lib/db.py`**

In `/Users/drucev/projects/news_agent/lib/db.py`:

(a) Replace the `sites` CREATE TABLE in `_SCHEMA_STATEMENTS` (find it; currently has columns `id, domain, name, reputation, last_seen`):
```python
    """
    CREATE TABLE IF NOT EXISTS sites (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        domain TEXT NOT NULL UNIQUE,
        name TEXT,
        reputation REAL DEFAULT 0.0,
        scrape_method TEXT,
        last_seen TEXT
    )
    """,
```

(b) Append at end of file (after `AgentState`):
```python
@dataclass
class Site:
    domain: str
    name: Optional[str] = None
    reputation: float = 0.0
    scrape_method: Optional[str] = None  # None | "http" | "playwright"
    last_seen: Optional[str] = None
    id: Optional[int] = None

    def upsert(self, conn: sqlite3.Connection) -> None:
        cur = conn.execute(
            """
            INSERT INTO sites (domain, name, reputation, scrape_method, last_seen)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(domain) DO UPDATE SET
                name = COALESCE(excluded.name, name),
                reputation = excluded.reputation,
                scrape_method = COALESCE(excluded.scrape_method, scrape_method),
                last_seen = COALESCE(excluded.last_seen, last_seen)
            """,
            (self.domain, self.name, self.reputation,
             self.scrape_method, self.last_seen),
        )
        if cur.lastrowid:
            self.id = cur.lastrowid
        conn.commit()

    @classmethod
    def get_by_domain(cls, conn: sqlite3.Connection, domain: str) -> Optional["Site"]:
        row = conn.execute(
            "SELECT id, domain, name, reputation, scrape_method, last_seen "
            "FROM sites WHERE domain=?",
            (domain,),
        ).fetchone()
        if not row:
            return None
        return cls(id=row[0], domain=row[1], name=row[2],
                   reputation=row[3] or 0.0,
                   scrape_method=row[4], last_seen=row[5])
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
.venv/bin/pytest tests/test_db_site.py -v
```
Expected: 4 passed. Also re-run `.venv/bin/pytest tests/test_db.py -v` to confirm no regression.

- [ ] **Step 5: Commit**

```bash
git add lib/db.py tests/test_db_site.py
git commit -m "feat(db): Site dataclass + sites.scrape_method column"
```

---

## Task 2: Add scraping dependencies

**Files:**
- Modify: `pyproject.toml`
- Modify: `requirements.txt`

- [ ] **Step 1: Update deps**

In `pyproject.toml`, extend `[project] dependencies`:
```toml
dependencies = [
  "pydantic>=2.7",
  "pyyaml>=6.0",
  "click>=8.1",
  "httpx>=0.27",
  "trafilatura>=1.10",
  "feedparser>=6.0",
  "beautifulsoup4>=4.12",
  "playwright>=1.49",
]
```

In `requirements.txt`, append the same four packages (one per line) so `pip install -r requirements.txt` also works.

- [ ] **Step 2: Reinstall + install Playwright Chromium**

```bash
cd /Users/drucev/projects/news_agent
.venv/bin/pip install -e ".[dev]"
.venv/bin/python -m playwright install chromium
```
Expected: chromium downloaded; no errors.

- [ ] **Step 3: Verify imports**

```bash
.venv/bin/python -c "import trafilatura, feedparser, bs4, playwright; print('OK')"
```
Expected: prints `OK`.

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml requirements.txt
git commit -m "chore: add trafilatura, feedparser, bs4, playwright deps"
```

---

## Task 3: `lib/fetch/rss.py` — RSS reader

**Files:**
- Create: `lib/fetch/__init__.py` (empty)
- Create: `lib/fetch/types.py` (shared types)
- Create: `lib/fetch/rss.py`
- Create: `tests/test_fetch_rss.py`

- [ ] **Step 1: Create types module**

Create `/Users/drucev/projects/news_agent/lib/fetch/types.py`:
```python
"""Shared types for the fetch package."""
from __future__ import annotations

from typing import Optional
from pydantic import BaseModel, Field


class Article(BaseModel):
    """A headline returned by a source fetcher."""
    source: str
    title: str
    url: str
    published: Optional[str] = None
    rss_summary: Optional[str] = None


class FetchResult(BaseModel):
    """Outcome of fetching one source."""
    source: str
    articles: list[Article] = Field(default_factory=list)
    ok: bool
    error: Optional[str] = None
```

Create `/Users/drucev/projects/news_agent/lib/fetch/__init__.py` (empty).

- [ ] **Step 2: Write failing tests**

Create `/Users/drucev/projects/news_agent/tests/test_fetch_rss.py`:
```python
import respx
import httpx
from lib.fetch.rss import fetch_rss

_FEED_XML = """<?xml version="1.0"?>
<rss version="2.0">
<channel>
  <title>Example Feed</title>
  <item>
    <title>OpenAI ships GPT-6</title>
    <link>https://example.com/gpt6</link>
    <pubDate>Sun, 17 May 2026 10:00:00 GMT</pubDate>
    <description>Big AI news.</description>
  </item>
  <item>
    <title>Apple announces MacBook</title>
    <link>https://example.com/macbook</link>
    <pubDate>Sun, 17 May 2026 11:00:00 GMT</pubDate>
  </item>
</channel>
</rss>
"""


@respx.mock
def test_fetch_rss_parses_entries():
    respx.get("https://example.com/feed.xml").mock(
        return_value=httpx.Response(200, content=_FEED_XML.encode("utf-8"))
    )
    result = fetch_rss("Example", "https://example.com/feed.xml")
    assert result.ok is True
    assert result.source == "Example"
    assert len(result.articles) == 2
    assert result.articles[0].title == "OpenAI ships GPT-6"
    assert result.articles[0].url == "https://example.com/gpt6"
    assert result.articles[0].published is not None
    assert result.articles[0].rss_summary == "Big AI news."


@respx.mock
def test_fetch_rss_http_error_returns_not_ok():
    respx.get("https://example.com/feed.xml").mock(
        return_value=httpx.Response(500, text="server down")
    )
    result = fetch_rss("Example", "https://example.com/feed.xml")
    assert result.ok is False
    assert "500" in (result.error or "")
    assert result.articles == []


@respx.mock
def test_fetch_rss_caps_at_50():
    items = "".join(
        f"<item><title>Item {i}</title><link>https://example.com/{i}</link></item>"
        for i in range(100)
    )
    body = f"<?xml version='1.0'?><rss><channel>{items}</channel></rss>"
    respx.get("https://example.com/feed.xml").mock(
        return_value=httpx.Response(200, content=body.encode("utf-8"))
    )
    result = fetch_rss("Example", "https://example.com/feed.xml")
    assert len(result.articles) == 50
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
.venv/bin/pytest tests/test_fetch_rss.py -v
```
Expected: ImportError.

- [ ] **Step 4: Implement `lib/fetch/rss.py`**

Create `/Users/drucev/projects/news_agent/lib/fetch/rss.py`:
```python
"""RSS feed fetcher.

Ported semantics from legacy ~/projects/OpenAIAgentsSDK/fetch.py:142 (Fetcher.fetch_rss).
Uses synchronous httpx + feedparser; caps at 50 entries per feed.
"""
from __future__ import annotations

import httpx
import feedparser

from lib.fetch.types import Article, FetchResult


_MAX_ENTRIES = 50
_TIMEOUT = httpx.Timeout(15.0, connect=5.0)


def fetch_rss(source: str, rss_url: str) -> FetchResult:
    """Fetch and parse an RSS feed.

    Args:
        source: Source name (top-level key from sources.yaml).
        rss_url: Feed URL.

    Returns:
        FetchResult with up to 50 Article entries.
    """
    try:
        with httpx.Client(timeout=_TIMEOUT, follow_redirects=True) as client:
            resp = client.get(rss_url)
    except httpx.HTTPError as exc:
        return FetchResult(source=source, ok=False, error=f"HTTP error: {exc}")

    if resp.status_code != 200:
        return FetchResult(
            source=source, ok=False,
            error=f"HTTP {resp.status_code} from {rss_url}",
        )

    feed = feedparser.parse(resp.content)
    articles: list[Article] = []
    for entry in feed.entries[:_MAX_ENTRIES]:
        title = entry.get("title", "")
        if not title:
            continue
        url = entry.get("link", "")
        if not url:
            continue
        articles.append(Article(
            source=source,
            title=title,
            url=url,
            published=entry.get("published") or None,
            rss_summary=entry.get("summary") or entry.get("description") or None,
        ))

    return FetchResult(source=source, articles=articles, ok=True)
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
.venv/bin/pytest tests/test_fetch_rss.py -v
```
Expected: 3 passed.

- [ ] **Step 6: Commit**

```bash
git add lib/fetch/__init__.py lib/fetch/types.py lib/fetch/rss.py tests/test_fetch_rss.py
git commit -m "feat(fetch): RSS reader via httpx + feedparser"
```

---

## Task 4: `lib/fetch/extract.py` — link extraction from HTML

**Files:**
- Create: `lib/fetch/extract.py`
- Create: `tests/test_extract.py`

This is the BeautifulSoup link-list extraction logic from legacy `scrape.py:1144` (`parse_source_file`). We adapt it to take HTML *content* directly instead of reading from disk.

- [ ] **Step 1: Write failing tests**

Create `/Users/drucev/projects/news_agent/tests/test_extract.py`:
```python
from lib.fetch.extract import extract_article_links


_HTML = """
<html><body>
<a href="https://example.com/news/2026/05/article-1">Article One Headline (Long Enough)</a>
<a href="https://example.com/news/2026/05/article-2">Article Two Headline (Long Enough)</a>
<a href="https://example.com/ads/banner-1">SponsoredAdShort</a>
<a href="https://other.com/x">Off-domain link</a>
<a href="/relative/path">Relative link should resolve via base</a>
<a href="https://example.com/news/short">x</a>
<a href="javascript:void(0)">js link</a>
<a href="mailto:hi@example.com">email</a>
</body></html>
"""


def test_extract_respects_include_pattern():
    cfg = {
        "url": "https://example.com/",
        "include": [r"^https://example\.com/news/"],
        "minlength": 10,
    }
    links = extract_article_links(_HTML, cfg, source_name="Example")
    urls = [a.url for a in links]
    assert "https://example.com/news/2026/05/article-1" in urls
    assert "https://example.com/news/2026/05/article-2" in urls
    assert "https://example.com/ads/banner-1" not in urls
    assert "https://other.com/x" not in urls


def test_extract_respects_exclude_pattern():
    cfg = {
        "url": "https://example.com/",
        "exclude": [r"^https://example\.com/ads/"],
        "minlength": 10,
    }
    links = extract_article_links(_HTML, cfg, source_name="Example")
    urls = [a.url for a in links]
    assert "https://example.com/ads/banner-1" not in urls


def test_extract_drops_short_titles():
    cfg = {"url": "https://example.com/", "minlength": 20}
    links = extract_article_links(_HTML, cfg, source_name="Example")
    titles = [a.title for a in links]
    assert "x" not in titles
    assert "SponsoredAdShort" not in titles  # under 20 chars


def test_extract_drops_javascript_and_mailto():
    cfg = {"url": "https://example.com/", "minlength": 1}
    links = extract_article_links(_HTML, cfg, source_name="Example")
    urls = [a.url for a in links]
    assert not any(u.startswith("javascript:") for u in urls)
    assert not any(u.startswith("mailto:") for u in urls)


def test_extract_resolves_relative_urls():
    cfg = {"url": "https://example.com/", "minlength": 1}
    links = extract_article_links(_HTML, cfg, source_name="Example")
    urls = [a.url for a in links]
    assert "https://example.com/relative/path" in urls


def test_extract_returns_article_objects():
    cfg = {"url": "https://example.com/",
           "include": [r"^https://example\.com/news/"], "minlength": 10}
    links = extract_article_links(_HTML, cfg, source_name="Example")
    assert all(a.source == "Example" for a in links)
    assert all(a.published is None for a in links)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
.venv/bin/pytest tests/test_extract.py -v
```

- [ ] **Step 3: Implement `lib/fetch/extract.py`**

Reference: legacy `~/projects/OpenAIAgentsSDK/scrape.py:1144-1280` (`parse_source_file`). Port the logic; do NOT read from disk — operate on the HTML string passed in.

Create `/Users/drucev/projects/news_agent/lib/fetch/extract.py`:
```python
"""Extract article links from a landing-page HTML document.

Ported from legacy ~/projects/OpenAIAgentsSDK/scrape.py:1144 (parse_source_file)
with one change: takes HTML content directly instead of reading from disk.
"""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from lib.fetch.types import Article


_DEFAULT_MINLENGTH = 28


def _clean_url(url: str) -> str:
    """Strip fragments/empty queries; minimal normalization."""
    return url.split("#")[0]


def extract_article_links(
    html: str,
    source_cfg: dict[str, Any],
    source_name: str,
) -> list[Article]:
    """Parse landing-page HTML and return article links matching the source's include/exclude rules."""
    minlength = source_cfg.get("minlength") or _DEFAULT_MINLENGTH
    exclude: list[str] = source_cfg.get("exclude") or []
    include: list[str] = source_cfg.get("include") or []
    base_page_url = source_cfg.get("url", "")

    soup = BeautifulSoup(html, "html.parser")

    base_tag = soup.find("base")
    base_url = base_tag.get("href") if base_tag and base_tag.get("href") else base_page_url

    raw_links = soup.find_all("a")
    candidates: list[Article] = []
    for link in raw_links:
        text = link.get_text(strip=True)
        href = link.get("href") or ""
        if not text or not href:
            continue
        if re.match(r"^\d+$", text):  # bare numbers (comment counts on Ars, etc.)
            continue
        absolute = urljoin(base_url, href)
        absolute = _clean_url(absolute)
        if not absolute.startswith("http"):  # drop javascript:, mailto:, etc.
            continue
        path = urlparse(absolute).path
        if len(path) <= 1:  # drop bare-domain links
            continue
        if len(text) < minlength:
            continue
        if exclude and any(re.match(p, absolute) for p in exclude):
            continue
        if include and not any(re.match(p, absolute) for p in include):
            continue
        candidates.append(Article(
            source=source_name,
            title=text,
            url=absolute,
        ))

    # Dedup within this page by URL, preserving order
    seen: set[str] = set()
    unique: list[Article] = []
    for a in candidates:
        if a.url not in seen:
            seen.add(a.url)
            unique.append(a)
    return unique
```

- [ ] **Step 4: Run tests, commit**

```bash
.venv/bin/pytest tests/test_extract.py -v
# Expect 6 passed
git add lib/fetch/extract.py tests/test_extract.py
git commit -m "feat(fetch): extract_article_links from landing-page HTML"
```

---

## Task 5: `lib/fetch/playwright_runner.py` — minimal Playwright fetcher

**Files:**
- Create: `lib/fetch/playwright_runner.py`

This is a thin wrapper. Single async function that opens chromium, navigates to URL, returns rendered HTML. Used by `html.py` (fallback) and `download.py` (always). Keep it small — full feature port (rate limiting, fingerprinting, etc.) is out of scope for Phase 2.

- [ ] **Step 1: Implement (no TDD test — integration only)**

Create `/Users/drucev/projects/news_agent/lib/fetch/playwright_runner.py`:
```python
"""Thin Playwright wrapper for fetching rendered HTML.

For Phase 2 we don't port the full legacy scrape.py — no rate limiting, no
fingerprinting, no stealth scripts. Just open chromium, navigate, return HTML.
Failure modes (timeout, no network, etc.) propagate as Exceptions; callers
decide how to handle.
"""
from __future__ import annotations

import asyncio
from typing import Optional


_DEFAULT_NAV_TIMEOUT_MS = 30_000


async def fetch_url_html_async(url: str, timeout_ms: int = _DEFAULT_NAV_TIMEOUT_MS) -> str:
    """Open chromium headless, navigate to URL, return rendered HTML body."""
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            context = await browser.new_context()
            page = await context.new_page()
            await page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
            html = await page.content()
        finally:
            await browser.close()
    return html


def fetch_url_html(url: str, timeout_ms: int = _DEFAULT_NAV_TIMEOUT_MS) -> str:
    """Sync facade for fetch_url_html_async."""
    return asyncio.run(fetch_url_html_async(url, timeout_ms=timeout_ms))
```

- [ ] **Step 2: Smoke test it (manual)**

```bash
.venv/bin/python -c "from lib.fetch.playwright_runner import fetch_url_html; html = fetch_url_html('https://example.com/'); print(len(html), 'chars'); assert 'Example Domain' in html"
```
If this fails because chromium isn't installed, run `.venv/bin/python -m playwright install chromium` first.

- [ ] **Step 3: Commit**

```bash
git add lib/fetch/playwright_runner.py
git commit -m "feat(fetch): minimal Playwright wrapper"
```

---

## Task 6: `lib/fetch/html.py` — adaptive HTML fetcher with Playwright fallback

**Files:**
- Create: `lib/fetch/html.py`
- Create: `tests/test_fetch_html.py`

**Contract:**
```python
def fetch_html(
    source_name: str,
    source_cfg: dict,
    *,
    scrape_method: Optional[str],  # None | "http" | "playwright"
) -> tuple[FetchResult, str]:
    """Returns (result, used_method). used_method is the method that actually succeeded
    ('http' or 'playwright'). Caller persists to sites.scrape_method.
    """
```

Behavior:
- If `scrape_method == "playwright"`: skip httpx, go straight to Playwright.
- Else: try httpx + trafilatura first. If response status != 200 OR `len(extracted_links) < MIN_LINKS_FOR_SUCCESS` (=3), fall back to Playwright.
- After fetching HTML, hand off to `extract_article_links` from Task 4.

- [ ] **Step 1: Write failing tests**

Create `/Users/drucev/projects/news_agent/tests/test_fetch_html.py`:
```python
import respx
import httpx
from unittest.mock import patch
from lib.fetch.html import fetch_html


_HTML_GOOD = """
<html><body>
<a href="/2026/05/story-one">Story One Headline About Something Long Enough</a>
<a href="/2026/05/story-two">Story Two Headline About Something Long Enough</a>
<a href="/2026/05/story-three">Story Three Headline About Something Long Enough</a>
<a href="/2026/05/story-four">Story Four Headline About Something Long Enough</a>
</body></html>
"""

_HTML_THIN = "<html><body><a href='/x'>x</a></body></html>"

_CFG_GOOD = {
    "url": "https://news.example.com/",
    "include": [r"^https://news\.example\.com/2026/"],
    "minlength": 10,
}


@respx.mock
def test_fetch_html_uses_httpx_when_method_is_none_or_http():
    respx.get("https://news.example.com/").mock(
        return_value=httpx.Response(200, text=_HTML_GOOD)
    )
    result, method = fetch_html("Example", _CFG_GOOD, scrape_method=None)
    assert result.ok is True
    assert method == "http"
    assert len(result.articles) >= 3


@respx.mock
def test_fetch_html_falls_back_to_playwright_on_thin_content():
    respx.get("https://news.example.com/").mock(
        return_value=httpx.Response(200, text=_HTML_THIN)
    )
    with patch("lib.fetch.html.fetch_url_html", return_value=_HTML_GOOD) as mock_pw:
        result, method = fetch_html("Example", _CFG_GOOD, scrape_method=None)
    assert method == "playwright"
    assert mock_pw.called
    assert len(result.articles) >= 3


@respx.mock
def test_fetch_html_falls_back_to_playwright_on_http_error():
    respx.get("https://news.example.com/").mock(
        return_value=httpx.Response(500, text="explode")
    )
    with patch("lib.fetch.html.fetch_url_html", return_value=_HTML_GOOD) as mock_pw:
        result, method = fetch_html("Example", _CFG_GOOD, scrape_method=None)
    assert method == "playwright"
    assert mock_pw.called


def test_fetch_html_goes_straight_to_playwright_when_pinned():
    with patch("lib.fetch.html.fetch_url_html", return_value=_HTML_GOOD) as mock_pw:
        with respx.mock:  # ensure no HTTP call is made
            result, method = fetch_html("Example", _CFG_GOOD, scrape_method="playwright")
    assert method == "playwright"
    assert mock_pw.called
    assert result.ok is True


@respx.mock
def test_fetch_html_reports_failure_if_both_methods_fail():
    respx.get("https://news.example.com/").mock(
        return_value=httpx.Response(500)
    )
    with patch("lib.fetch.html.fetch_url_html",
               side_effect=RuntimeError("pw broken")):
        result, method = fetch_html("Example", _CFG_GOOD, scrape_method=None)
    assert result.ok is False
    assert method == "playwright"  # last attempted
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
.venv/bin/pytest tests/test_fetch_html.py -v
```

- [ ] **Step 3: Implement `lib/fetch/html.py`**

Create `/Users/drucev/projects/news_agent/lib/fetch/html.py`:
```python
"""Adaptive HTML fetcher: trafilatura+httpx first, Playwright fallback."""
from __future__ import annotations

from typing import Optional
import httpx

from lib.fetch.types import FetchResult
from lib.fetch.extract import extract_article_links
from lib.fetch.playwright_runner import fetch_url_html


_TIMEOUT = httpx.Timeout(20.0, connect=5.0)
_MIN_LINKS_FOR_SUCCESS = 3
_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) news-agent/0.1"


def _http_fetch(url: str) -> tuple[Optional[str], Optional[str]]:
    """Returns (html, error). One of them is None."""
    try:
        with httpx.Client(timeout=_TIMEOUT, follow_redirects=True,
                          headers={"User-Agent": _UA}) as client:
            resp = client.get(url)
    except httpx.HTTPError as exc:
        return None, f"HTTP error: {exc}"
    if resp.status_code != 200:
        return None, f"HTTP {resp.status_code}"
    return resp.text, None


def fetch_html(
    source_name: str,
    source_cfg: dict,
    *,
    scrape_method: Optional[str],
) -> tuple[FetchResult, str]:
    """Fetch a landing page and extract article links.

    Strategy:
      - scrape_method == "playwright" → skip httpx, go straight to Playwright.
      - Otherwise: try httpx; fall back to Playwright on HTTP failure or thin content.

    Returns:
      (FetchResult, used_method) where used_method ∈ {"http", "playwright"}.
      Caller persists used_method to sites.scrape_method.
    """
    url = source_cfg.get("url", "")
    if not url:
        return FetchResult(source=source_name, ok=False,
                           error="No URL in source config"), "http"

    if scrape_method == "playwright":
        return _try_playwright(source_name, source_cfg, url)

    # Try HTTP first
    html, http_err = _http_fetch(url)
    if html is not None:
        articles = extract_article_links(html, source_cfg, source_name)
        if len(articles) >= _MIN_LINKS_FOR_SUCCESS:
            return FetchResult(source=source_name, articles=articles, ok=True), "http"

    # Fallback to Playwright
    return _try_playwright(source_name, source_cfg, url)


def _try_playwright(source_name: str, source_cfg: dict, url: str) -> tuple[FetchResult, str]:
    try:
        html = fetch_url_html(url)
    except Exception as exc:
        return FetchResult(source=source_name, ok=False,
                           error=f"Playwright fetch failed: {exc}"), "playwright"
    articles = extract_article_links(html, source_cfg, source_name)
    if not articles:
        return FetchResult(source=source_name, ok=False,
                           error="Playwright fetched but no links extracted"), "playwright"
    return FetchResult(source=source_name, articles=articles, ok=True), "playwright"
```

- [ ] **Step 4: Run tests + commit**

```bash
.venv/bin/pytest tests/test_fetch_html.py -v
# Expect 5 passed
git add lib/fetch/html.py tests/test_fetch_html.py
git commit -m "feat(fetch): adaptive HTML fetcher with Playwright fallback"
```

---

## Task 7: `lib/fetch/rest.py` — REST API fetcher (NewsAPI-style)

**Files:**
- Create: `lib/fetch/rest.py`
- Create: `tests/test_fetch_rest.py`

Generic JSON API. Source config provides `url`, optional `headers`, optional `api_key_env` (env var name to inject as Bearer token or query param), and a `mapping` describing how to pull title/url/published from each article.

For Phase 2 keep it minimal: support NewsAPI shape (`{ "articles": [{ "title", "url", "publishedAt", "description" }] }`). Source config:
```yaml
NewsAPI:
  type: rest
  url: https://newsapi.org/v2/top-headlines?country=us&category=technology
  api_key_env: NEWSAPI_API_KEY
  api_key_header: X-Api-Key
  items_path: articles
  title_field: title
  url_field: url
  published_field: publishedAt
  summary_field: description
```

- [ ] **Step 1: Write failing tests**

Create `/Users/drucev/projects/news_agent/tests/test_fetch_rest.py`:
```python
import respx
import httpx
from lib.fetch.rest import fetch_rest


_CFG = {
    "url": "https://api.example.com/articles",
    "api_key_env": "TEST_API_KEY",
    "api_key_header": "X-Api-Key",
    "items_path": "articles",
    "title_field": "title",
    "url_field": "url",
    "published_field": "publishedAt",
    "summary_field": "description",
}


@respx.mock
def test_fetch_rest_extracts_articles(monkeypatch):
    monkeypatch.setenv("TEST_API_KEY", "k123")
    route = respx.get("https://api.example.com/articles").mock(
        return_value=httpx.Response(200, json={
            "articles": [
                {"title": "Article A", "url": "https://e.com/a",
                 "publishedAt": "2026-05-17T10:00:00Z", "description": "desc A"},
                {"title": "Article B", "url": "https://e.com/b",
                 "publishedAt": "2026-05-17T11:00:00Z", "description": "desc B"},
            ]
        })
    )
    result = fetch_rest("ExampleAPI", _CFG)
    assert result.ok
    assert len(result.articles) == 2
    assert result.articles[0].title == "Article A"
    assert result.articles[0].rss_summary == "desc A"
    # Auth header sent
    assert route.calls.last.request.headers["x-api-key"] == "k123"


@respx.mock
def test_fetch_rest_skips_articles_missing_url(monkeypatch):
    monkeypatch.setenv("TEST_API_KEY", "k")
    respx.get("https://api.example.com/articles").mock(
        return_value=httpx.Response(200, json={
            "articles": [
                {"title": "A", "url": "https://e.com/a"},
                {"title": "B", "url": ""},   # skipped
                {"title": "C"},              # skipped
            ]
        })
    )
    result = fetch_rest("ExampleAPI", _CFG)
    assert len(result.articles) == 1


@respx.mock
def test_fetch_rest_http_error(monkeypatch):
    monkeypatch.setenv("TEST_API_KEY", "k")
    respx.get("https://api.example.com/articles").mock(
        return_value=httpx.Response(401, text="bad key")
    )
    result = fetch_rest("ExampleAPI", _CFG)
    assert not result.ok
    assert "401" in (result.error or "")


def test_fetch_rest_missing_api_key_env(monkeypatch):
    monkeypatch.delenv("TEST_API_KEY", raising=False)
    result = fetch_rest("ExampleAPI", _CFG)
    assert not result.ok
    assert "TEST_API_KEY" in (result.error or "")
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
.venv/bin/pytest tests/test_fetch_rest.py -v
```

- [ ] **Step 3: Implement `lib/fetch/rest.py`**

Create `/Users/drucev/projects/news_agent/lib/fetch/rest.py`:
```python
"""Generic JSON REST API fetcher (NewsAPI-style)."""
from __future__ import annotations

import os
import httpx

from lib.fetch.types import Article, FetchResult


_TIMEOUT = httpx.Timeout(15.0, connect=5.0)


def fetch_rest(source_name: str, source_cfg: dict) -> FetchResult:
    url = source_cfg.get("url")
    if not url:
        return FetchResult(source=source_name, ok=False, error="No URL in source config")

    headers: dict[str, str] = {}
    api_key_env = source_cfg.get("api_key_env")
    if api_key_env:
        key = os.environ.get(api_key_env)
        if not key:
            return FetchResult(source=source_name, ok=False,
                               error=f"Env var {api_key_env} not set")
        headers[source_cfg.get("api_key_header", "Authorization")] = key

    try:
        with httpx.Client(timeout=_TIMEOUT, follow_redirects=True) as client:
            resp = client.get(url, headers=headers)
    except httpx.HTTPError as exc:
        return FetchResult(source=source_name, ok=False, error=f"HTTP error: {exc}")

    if resp.status_code != 200:
        return FetchResult(source=source_name, ok=False,
                           error=f"HTTP {resp.status_code}: {resp.text[:200]}")

    try:
        payload = resp.json()
    except Exception as exc:
        return FetchResult(source=source_name, ok=False, error=f"Non-JSON response: {exc}")

    items_path = source_cfg.get("items_path", "articles")
    items = payload.get(items_path, [])
    title_field = source_cfg.get("title_field", "title")
    url_field = source_cfg.get("url_field", "url")
    published_field = source_cfg.get("published_field", "publishedAt")
    summary_field = source_cfg.get("summary_field", "description")

    articles: list[Article] = []
    for it in items:
        url_val = it.get(url_field) or ""
        title_val = it.get(title_field) or ""
        if not url_val or not title_val:
            continue
        articles.append(Article(
            source=source_name,
            title=title_val,
            url=url_val,
            published=it.get(published_field),
            rss_summary=it.get(summary_field),
        ))

    return FetchResult(source=source_name, articles=articles, ok=True)
```

- [ ] **Step 4: Run tests + commit**

```bash
.venv/bin/pytest tests/test_fetch_rest.py -v
# Expect 4 passed
git add lib/fetch/rest.py tests/test_fetch_rest.py
git commit -m "feat(fetch): generic REST/JSON API fetcher"
```

---

## Task 8: `lib/steps/gather.py` + `skills/gather/SKILL.md`

**Files:**
- Create: `lib/steps/gather.py`
- Create: `skills/gather/SKILL.md`
- Create: `tests/test_step_gather.py`

This is the orchestration step. Logic:

1. Load latest state for the session (must have `init` complete).
2. Loop over `state.sources`:
   - Determine source type from cfg (`type: rss | html | rest`).
   - Look up `sites.scrape_method` (for HTML sources only) by extracting the domain from `cfg["url"]`.
   - Dispatch to the right fetcher.
   - On HTML success, upsert `Site(domain=..., scrape_method=method)`.
3. Collect all `Article`s, dedup against existing `urls` table (by `initial_url`), insert new ones.
4. Update `state.headline_data` with new articles only.
5. Write `runs/<session_id>/gather.json` with per-source success/failure report.
6. Mark step complete, save checkpoint.

- [ ] **Step 1: Write failing tests**

Create `/Users/drucev/projects/news_agent/tests/test_step_gather.py`:
```python
import json
import sqlite3
from pathlib import Path
from unittest.mock import patch
from click.testing import CliRunner

from lib.db import init_db, Site
from lib.fetch.types import Article, FetchResult
from lib.state import NewsletterAgentState
from lib.steps.gather import cli as gather_cli


def _make_session(tmp_db, sources_yaml_path):
    """Helper to set up a session ready for gather."""
    import yaml
    sources = yaml.safe_load(Path(sources_yaml_path).read_text())["sources"]
    init_db(tmp_db)
    state = NewsletterAgentState(
        session_id="g1", db_path=tmp_db,
        sources_file=sources_yaml_path, sources=sources,
    )
    state.complete_step("init")
    state.save_checkpoint("init")
    return state


def test_gather_calls_rss_fetcher_for_rss_sources(tmp_path, tmp_db):
    yaml_path = tmp_path / "sources.yaml"
    yaml_path.write_text(
        "sources:\n"
        "  Feed1:\n    type: rss\n    url: https://feed.example.com/rss\n    enabled: true\n"
    )
    _make_session(tmp_db, str(yaml_path))

    rss_result = FetchResult(source="Feed1", ok=True, articles=[
        Article(source="Feed1", title="A", url="https://feed.example.com/a"),
    ])
    with patch("lib.steps.gather.fetch_rss", return_value=rss_result) as mock_rss:
        runner = CliRunner()
        result = runner.invoke(gather_cli, ["--db", tmp_db, "--session", "g1"])
    assert result.exit_code == 0, result.output
    mock_rss.assert_called_once()


def test_gather_writes_urls_to_db_and_state(tmp_path, tmp_db):
    yaml_path = tmp_path / "sources.yaml"
    yaml_path.write_text(
        "sources:\n  Feed1:\n    type: rss\n    url: https://feed.example.com/rss\n    enabled: true\n"
    )
    _make_session(tmp_db, str(yaml_path))

    rss_result = FetchResult(source="Feed1", ok=True, articles=[
        Article(source="Feed1", title="A long enough headline", url="https://feed.example.com/a"),
        Article(source="Feed1", title="B long enough headline", url="https://feed.example.com/b"),
    ])
    with patch("lib.steps.gather.fetch_rss", return_value=rss_result):
        runner = CliRunner()
        runner.invoke(gather_cli, ["--db", tmp_db, "--session", "g1"])

    with sqlite3.connect(tmp_db) as conn:
        rows = conn.execute("SELECT initial_url FROM urls").fetchall()
    assert {r[0] for r in rows} == {"https://feed.example.com/a", "https://feed.example.com/b"}

    state = NewsletterAgentState(session_id="g1", db_path=tmp_db).load_latest_from_db()
    assert state is not None
    assert len(state.headline_data) == 2


def test_gather_dedups_against_existing_urls(tmp_path, tmp_db):
    yaml_path = tmp_path / "sources.yaml"
    yaml_path.write_text(
        "sources:\n  Feed1:\n    type: rss\n    url: https://feed.example.com/rss\n    enabled: true\n"
    )
    _make_session(tmp_db, str(yaml_path))

    with sqlite3.connect(tmp_db) as conn:
        conn.execute(
            "INSERT INTO urls (initial_url, final_url, title, source) VALUES (?,?,?,?)",
            ("https://feed.example.com/a", "https://feed.example.com/a", "A", "Feed1"),
        )
        conn.commit()

    rss_result = FetchResult(source="Feed1", ok=True, articles=[
        Article(source="Feed1", title="A long enough headline", url="https://feed.example.com/a"),  # dupe
        Article(source="Feed1", title="B long enough headline", url="https://feed.example.com/b"),  # new
    ])
    with patch("lib.steps.gather.fetch_rss", return_value=rss_result):
        runner = CliRunner()
        runner.invoke(gather_cli, ["--db", tmp_db, "--session", "g1"])

    state = NewsletterAgentState(session_id="g1", db_path=tmp_db).load_latest_from_db()
    assert len(state.headline_data) == 1
    assert state.headline_data[0]["url"] == "https://feed.example.com/b"


def test_gather_updates_scrape_method_for_html_sources(tmp_path, tmp_db):
    yaml_path = tmp_path / "sources.yaml"
    yaml_path.write_text(
        "sources:\n  Site1:\n    type: html\n    url: https://news.example.com/\n    enabled: true\n"
    )
    _make_session(tmp_db, str(yaml_path))

    html_result = FetchResult(source="Site1", ok=True, articles=[
        Article(source="Site1", title="Long enough headline X", url="https://news.example.com/a"),
    ])
    with patch("lib.steps.gather.fetch_html", return_value=(html_result, "playwright")):
        runner = CliRunner()
        runner.invoke(gather_cli, ["--db", tmp_db, "--session", "g1"])

    with sqlite3.connect(tmp_db) as conn:
        s = Site.get_by_domain(conn, "news.example.com")
    assert s is not None
    assert s.scrape_method == "playwright"


def test_gather_writes_report_json(tmp_path, tmp_db, monkeypatch):
    monkeypatch.chdir(tmp_path)
    yaml_path = tmp_path / "sources.yaml"
    yaml_path.write_text(
        "sources:\n  Feed1:\n    type: rss\n    url: https://feed.example.com/rss\n    enabled: true\n"
    )
    _make_session(tmp_db, str(yaml_path))

    with patch("lib.steps.gather.fetch_rss",
               return_value=FetchResult(source="Feed1", ok=True, articles=[])):
        runner = CliRunner()
        runner.invoke(gather_cli, ["--db", tmp_db, "--session", "g1"])

    report_path = Path("runs/g1/gather.json")
    assert report_path.exists()
    report = json.loads(report_path.read_text())
    assert "sources" in report
    assert any(s["source"] == "Feed1" for s in report["sources"])
```

- [ ] **Step 2: Run tests to fail**

```bash
.venv/bin/pytest tests/test_step_gather.py -v
```

- [ ] **Step 3: Implement `lib/steps/gather.py`**

Create `/Users/drucev/projects/news_agent/lib/steps/gather.py`:
```python
"""news:gather — fetch headlines from configured sources.

Source types:
  - rss   : feedparser via httpx (lib.fetch.rss)
  - html  : adaptive trafilatura+httpx → Playwright fallback (lib.fetch.html);
            persists per-site working method in sites.scrape_method
  - rest  : generic JSON API (lib.fetch.rest)
"""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import click

from lib.db import Site
from lib.fetch.rss import fetch_rss
from lib.fetch.html import fetch_html
from lib.fetch.rest import fetch_rest
from lib.fetch.types import FetchResult
from lib.state import NewsletterAgentState


def _domain_of(url: str) -> str:
    return urlparse(url).hostname or ""


def _fetch_one(source_name: str, cfg: dict, db_path: str) -> tuple[FetchResult, str | None]:
    """Returns (result, used_method) where used_method is set only for HTML sources."""
    stype = cfg.get("type", "html")
    if stype == "rss":
        rss_url = cfg.get("rss") or cfg.get("url")
        if not rss_url:
            return FetchResult(source=source_name, ok=False,
                               error="No rss/url in source config"), None
        return fetch_rss(source_name, rss_url), None
    if stype == "html":
        url = cfg.get("url", "")
        domain = _domain_of(url)
        prior_method: str | None = None
        if domain:
            with sqlite3.connect(db_path) as conn:
                site = Site.get_by_domain(conn, domain)
                if site:
                    prior_method = site.scrape_method
        result, used = fetch_html(source_name, cfg, scrape_method=prior_method)
        return result, used
    if stype == "rest":
        return fetch_rest(source_name, cfg), None
    return FetchResult(source=source_name, ok=False,
                       error=f"Unknown source type: {stype}"), None


@click.command()
@click.option("--db", "db_path", default="newsletter_agent.db")
@click.option("--session", "session_id", required=True)
def cli(db_path: str, session_id: str) -> None:
    state = NewsletterAgentState(session_id=session_id, db_path=db_path).load_latest_from_db()
    if state is None:
        raise click.ClickException(f"No state found for session {session_id}")

    state.start_step("gather")
    state.save_checkpoint("gather")

    enabled_sources = {
        name: cfg for name, cfg in state.sources.items()
        if not (isinstance(cfg, dict) and cfg.get("enabled") is False)
    }

    report_sources: list[dict] = []
    all_articles: list[dict] = []

    for name, cfg in enabled_sources.items():
        result, used_method = _fetch_one(name, cfg, db_path)
        report_sources.append({
            "source": name,
            "type": cfg.get("type", "html") if isinstance(cfg, dict) else "html",
            "ok": result.ok,
            "method": used_method,
            "count": len(result.articles),
            "error": result.error,
        })
        if result.ok:
            for a in result.articles:
                all_articles.append(a.model_dump())

        # Persist scrape_method for HTML sources whether successful or not
        if used_method and isinstance(cfg, dict):
            domain = _domain_of(cfg.get("url", ""))
            if domain:
                with sqlite3.connect(db_path) as conn:
                    Site(domain=domain, name=name,
                         scrape_method=used_method,
                         last_seen=datetime.now().isoformat()).upsert(conn)

    # Dedup against urls table; insert new ones
    new_count = 0
    with sqlite3.connect(db_path) as conn:
        existing = {r[0] for r in conn.execute("SELECT initial_url FROM urls").fetchall()}
        new_articles_for_state: list[dict] = []
        now_iso = datetime.now().isoformat()
        for a in all_articles:
            if a["url"] in existing:
                continue
            conn.execute(
                "INSERT OR IGNORE INTO urls "
                "(initial_url, final_url, title, source, isAI, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (a["url"], a["url"], a["title"], a["source"], None, now_iso),
            )
            existing.add(a["url"])
            new_articles_for_state.append(a)
            new_count += 1
        conn.commit()

    state.headline_data.extend(new_articles_for_state)
    state.complete_step(
        "gather",
        message=f"{len(enabled_sources)} sources, {new_count} new headlines",
    )
    state.save_checkpoint("gather")

    # Write per-source report
    runs_dir = Path("runs") / session_id
    runs_dir.mkdir(parents=True, exist_ok=True)
    (runs_dir / "gather.json").write_text(json.dumps({
        "session_id": session_id,
        "completed_at": datetime.now().isoformat(),
        "sources": report_sources,
        "new_headlines": new_count,
    }, indent=2))

    click.echo(f"Gathered {new_count} new headlines from {len(enabled_sources)} sources.")
    for s in report_sources:
        status = "OK" if s["ok"] else "FAIL"
        method = f" [{s['method']}]" if s.get("method") else ""
        click.echo(f"  {status:<5} {s['source']:<24} {s['count']:>3} headlines{method}")


if __name__ == "__main__":  # pragma: no cover
    sys.exit(cli())
```

- [ ] **Step 4: Run tests + commit**

```bash
.venv/bin/pytest tests/test_step_gather.py -v
# Expect 5 passed
git add lib/steps/gather.py tests/test_step_gather.py
git commit -m "feat(steps): news:gather with adaptive scraping + per-site memoization"
```

- [ ] **Step 5: Add `skills/gather/SKILL.md`**

Create `/Users/drucev/projects/news_agent/skills/gather/SKILL.md`:
```markdown
---
name: news:gather
description: Fetch headlines from all configured sources (RSS/HTML/REST). HTML sources use adaptive scraping — trafilatura+httpx first, Playwright fallback, with per-site working method memoized in sites.scrape_method. Inserts new URLs into the `urls` table and updates session headline_data.
---

# news:gather

Step 2 of `/news:run`. Fetches headlines and dedups against `urls`.

## How to invoke

```bash
python -m lib.steps.gather --db newsletter_agent.db --session SID
```

## Behavior

For each enabled source in `sources.yaml`:
- `type: rss` → feedparser via httpx
- `type: html` → trafilatura+httpx first; on failure or thin content (<3 links), fall back to Playwright. Persists the working method in `sites.scrape_method` for next run.
- `type: rest` → JSON API (NewsAPI-style)

After all sources fetched:
- Dedups against `urls.initial_url`
- Inserts new URLs into `urls` table
- Appends new headlines to `headline_data`
- Writes per-source report to `runs/<SID>/gather.json`
- Marks `gather` step complete

## Errors

- Missing session or session without `init` complete → exit 1
- Per-source failures are recorded but do not abort the step. Check `runs/<SID>/gather.json`.
```

Commit:
```bash
git add skills/gather/SKILL.md
git commit -m "docs(skills): news:gather SKILL.md"
```

---

## Task 9: `lib/steps/download.py` + `skills/download/SKILL.md`

**Files:**
- Create: `lib/steps/download.py`
- Create: `skills/download/SKILL.md`
- Create: `tests/test_step_download.py`

Logic:
1. Load state. For each headline in `state.headline_data` without `text_path`:
2. Fetch via Playwright (always — these are full articles, paywalls likely).
3. Extract main text via `trafilatura.extract(html)`.
4. Write to `download/<sha256-of-url>.txt`. Store path in `headline["text_path"]`.
5. Concurrency: serial in Phase 2 (1 browser at a time). Cap at `--max N` URLs per run.
6. Per-URL success/failure to `runs/<SID>/download.json`.
7. Dedup deferred to Phase 3.

- [ ] **Step 1: Write failing tests**

Create `/Users/drucev/projects/news_agent/tests/test_step_download.py`:
```python
import json
import sqlite3
from pathlib import Path
from unittest.mock import patch
from click.testing import CliRunner

from lib.db import init_db
from lib.state import NewsletterAgentState
from lib.steps.download import cli as download_cli


def _setup(tmp_db, monkeypatch_chdir):
    init_db(tmp_db)
    state = NewsletterAgentState(session_id="d1", db_path=tmp_db)
    state.complete_step("init")
    state.complete_step("gather")
    state.headline_data = [
        {"source": "S", "title": "T1", "url": "https://example.com/a"},
        {"source": "S", "title": "T2", "url": "https://example.com/b"},
    ]
    state.save_checkpoint("gather")
    return state


def test_download_fetches_and_extracts_each_article(tmp_path, tmp_db, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _setup(tmp_db, monkeypatch)

    html_a = "<html><body><article>Body text A long enough</article></body></html>"
    html_b = "<html><body><article>Body text B long enough</article></body></html>"

    def fake_fetch(url, **_):
        return html_a if url.endswith("/a") else html_b

    with patch("lib.steps.download.fetch_url_html", side_effect=fake_fetch):
        runner = CliRunner()
        result = runner.invoke(download_cli, ["--db", tmp_db, "--session", "d1"])
    assert result.exit_code == 0, result.output

    state = NewsletterAgentState(session_id="d1", db_path=tmp_db).load_latest_from_db()
    assert all("text_path" in h and h["text_path"] for h in state.headline_data)
    for h in state.headline_data:
        p = Path(h["text_path"])
        assert p.exists()
        assert "Body text" in p.read_text()


def test_download_respects_max_flag(tmp_path, tmp_db, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _setup(tmp_db, monkeypatch)

    with patch("lib.steps.download.fetch_url_html",
               return_value="<html><article>txt long enough</article></html>") as pw:
        runner = CliRunner()
        runner.invoke(download_cli, ["--db", tmp_db, "--session", "d1", "--max", "1"])
    assert pw.call_count == 1


def test_download_skips_already_downloaded(tmp_path, tmp_db, monkeypatch):
    monkeypatch.chdir(tmp_path)
    state = _setup(tmp_db, monkeypatch)
    state.headline_data[0]["text_path"] = "download/existing.txt"
    state.save_checkpoint("gather")
    Path("download").mkdir()
    Path("download/existing.txt").write_text("already there")

    with patch("lib.steps.download.fetch_url_html",
               return_value="<html><article>txt long enough</article></html>") as pw:
        runner = CliRunner()
        runner.invoke(download_cli, ["--db", tmp_db, "--session", "d1"])
    assert pw.call_count == 1  # only the second one


def test_download_writes_report(tmp_path, tmp_db, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _setup(tmp_db, monkeypatch)

    with patch("lib.steps.download.fetch_url_html",
               return_value="<html><article>txt long enough</article></html>"):
        runner = CliRunner()
        runner.invoke(download_cli, ["--db", tmp_db, "--session", "d1"])

    report = json.loads(Path("runs/d1/download.json").read_text())
    assert report["downloaded"] == 2
```

- [ ] **Step 2: Run tests to fail**

```bash
.venv/bin/pytest tests/test_step_download.py -v
```

- [ ] **Step 3: Implement `lib/steps/download.py`**

Create `/Users/drucev/projects/news_agent/lib/steps/download.py`:
```python
"""news:download — fetch full article HTML and extract main text."""
from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

import click
import trafilatura

from lib.fetch.playwright_runner import fetch_url_html
from lib.state import NewsletterAgentState


_DOWNLOAD_DIR = Path("download")


def _safe_filename(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:24] + ".txt"


def _extract_text(html: str) -> Optional[str]:
    return trafilatura.extract(html, include_comments=False, include_tables=False)


@click.command()
@click.option("--db", "db_path", default="newsletter_agent.db")
@click.option("--session", "session_id", required=True)
@click.option("--max", "max_urls", type=int, default=None,
              help="Cap on number of URLs downloaded this run.")
def cli(db_path: str, session_id: str, max_urls: int | None) -> None:
    state = NewsletterAgentState(session_id=session_id, db_path=db_path).load_latest_from_db()
    if state is None:
        raise click.ClickException(f"No state found for session {session_id}")

    state.start_step("download")
    state.save_checkpoint("download")
    _DOWNLOAD_DIR.mkdir(exist_ok=True)

    pending = [h for h in state.headline_data if not h.get("text_path")]
    if max_urls:
        pending = pending[:max_urls]

    downloaded = 0
    failures: list[dict] = []
    for h in pending:
        url = h["url"]
        try:
            html = fetch_url_html(url)
            text = _extract_text(html)
            if not text:
                failures.append({"url": url, "error": "no text extracted"})
                continue
            target = _DOWNLOAD_DIR / _safe_filename(url)
            target.write_text(text)
            h["text_path"] = str(target)
            downloaded += 1
        except Exception as exc:
            failures.append({"url": url, "error": str(exc)[:300]})

    state.complete_step(
        "download",
        message=f"downloaded {downloaded}/{len(pending)} articles",
    )
    state.save_checkpoint("download")

    runs_dir = Path("runs") / session_id
    runs_dir.mkdir(parents=True, exist_ok=True)
    (runs_dir / "download.json").write_text(json.dumps({
        "session_id": session_id,
        "completed_at": datetime.now().isoformat(),
        "downloaded": downloaded,
        "failures": failures,
    }, indent=2))

    click.echo(f"Downloaded {downloaded}/{len(pending)} articles.")
    if failures:
        click.echo(f"Failures: {len(failures)} (see runs/{session_id}/download.json)")


if __name__ == "__main__":  # pragma: no cover
    sys.exit(cli())
```

- [ ] **Step 4: Run tests + commit**

```bash
.venv/bin/pytest tests/test_step_download.py -v
# Expect 4 passed
git add lib/steps/download.py tests/test_step_download.py
git commit -m "feat(steps): news:download fetches articles + extracts text"
```

- [ ] **Step 5: Add `skills/download/SKILL.md`**

Create `/Users/drucev/projects/news_agent/skills/download/SKILL.md`:
```markdown
---
name: news:download
description: Fetch full article HTML via Playwright for every kept URL, extract main text with trafilatura, store under download/. Skips URLs that already have a text_path. Phase 2 ships without near-duplicate dedup (Phase 3 adds it).
---

# news:download

Step 4 of `/news:run`.

## How to invoke

```bash
python -m lib.steps.download --db newsletter_agent.db --session SID [--max N]
```

## Behavior

For each headline in `state.headline_data` without a `text_path`:
1. Fetch full HTML via Playwright (chromium, headless).
2. Extract main article text via `trafilatura.extract`.
3. Write to `download/<sha256-of-url>.txt`.
4. Set `headline["text_path"]` to that path.

Failed fetches/extractions are logged to `runs/<SID>/download.json`; they don't abort the step.

## Phase 2 limits

- Serial execution (one browser at a time). Concurrent downloads come in Phase 2b.
- No embedding-based dedup. That ships in Phase 3 after the embedding API is wired up.
```

Commit:
```bash
git add skills/download/SKILL.md
git commit -m "docs(skills): news:download SKILL.md"
```

---

## Task 10: `lib/steps/send.py` + `skills/send/SKILL.md`

**Files:**
- Create: `lib/steps/send.py`
- Create: `skills/send/SKILL.md`
- Create: `tests/test_step_send.py`

Logic:
1. Load state. If `state.final_newsletter` is empty, render a placeholder ("Dummy newsletter — pipeline ran through gather/download/send without the LLM steps.") with timestamp + headline count.
2. Wrap in minimal styled HTML.
3. Write to `out/YYYY-MM-DD.html` and update `out/latest.html` symlink.
4. Insert row in `newsletters` table.
5. NO Gmail in Phase 2. Add `--notify` flag stub that errors with "Gmail send not implemented in Phase 2; use Phase 2b."

- [ ] **Step 1: Write failing tests**

Create `/Users/drucev/projects/news_agent/tests/test_step_send.py`:
```python
import sqlite3
from pathlib import Path
from datetime import date
from click.testing import CliRunner

from lib.db import init_db
from lib.state import NewsletterAgentState
from lib.steps.send import cli as send_cli


def _setup(tmp_db, headline_count=2, final_newsletter=""):
    init_db(tmp_db)
    state = NewsletterAgentState(session_id="s1", db_path=tmp_db)
    state.complete_step("init")
    state.complete_step("gather")
    state.headline_data = [
        {"source": "S", "title": f"Title {i}", "url": f"https://e.com/{i}"}
        for i in range(headline_count)
    ]
    state.final_newsletter = final_newsletter
    state.newsletter_title = "Test Newsletter" if final_newsletter else ""
    state.save_checkpoint("gather")
    return state


def test_send_writes_html_to_out_dir_with_date(tmp_path, tmp_db, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _setup(tmp_db)
    runner = CliRunner()
    result = runner.invoke(send_cli, ["--db", tmp_db, "--session", "s1"])
    assert result.exit_code == 0, result.output

    today = date.today().isoformat()
    out_file = Path(f"out/{today}.html")
    assert out_file.exists()
    body = out_file.read_text()
    assert "<html" in body
    assert "Dummy" in body or "newsletter" in body.lower()


def test_send_writes_latest_symlink(tmp_path, tmp_db, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _setup(tmp_db)
    runner = CliRunner()
    runner.invoke(send_cli, ["--db", tmp_db, "--session", "s1"])

    latest = Path("out/latest.html")
    assert latest.is_symlink() or latest.exists()
    body = latest.read_text()
    assert "<html" in body


def test_send_inserts_newsletters_row(tmp_path, tmp_db, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _setup(tmp_db, final_newsletter="<p>my custom newsletter body</p>")
    runner = CliRunner()
    runner.invoke(send_cli, ["--db", tmp_db, "--session", "s1"])

    with sqlite3.connect(tmp_db) as conn:
        rows = conn.execute("SELECT session_id, title, html FROM newsletters").fetchall()
    assert len(rows) == 1
    assert rows[0][0] == "s1"
    assert "my custom newsletter" in rows[0][2]


def test_send_uses_final_newsletter_when_set(tmp_path, tmp_db, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _setup(tmp_db, final_newsletter="<p>real content</p>")
    runner = CliRunner()
    runner.invoke(send_cli, ["--db", tmp_db, "--session", "s1"])

    body = Path(f"out/{date.today().isoformat()}.html").read_text()
    assert "real content" in body


def test_send_notify_flag_errors_in_phase_2(tmp_path, tmp_db, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _setup(tmp_db)
    runner = CliRunner()
    result = runner.invoke(send_cli, ["--db", tmp_db, "--session", "s1", "--notify"])
    assert result.exit_code != 0
    assert "not implemented" in result.output.lower() or "phase" in result.output.lower()
```

- [ ] **Step 2: Run tests to fail**

```bash
.venv/bin/pytest tests/test_step_send.py -v
```

- [ ] **Step 3: Implement `lib/steps/send.py`**

Create `/Users/drucev/projects/news_agent/lib/steps/send.py`:
```python
"""news:send — render newsletter as HTML and write to out/."""
from __future__ import annotations

import sqlite3
import sys
from datetime import date, datetime
from pathlib import Path

import click

from lib.state import NewsletterAgentState


_OUT_DIR = Path("out")


def _render_html(title: str, body_html: str, headline_count: int) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>{title or 'AI Newsletter'}</title>
  <style>
    body {{ font-family: -apple-system, sans-serif; max-width: 720px; margin: 2em auto; padding: 0 1em; line-height: 1.5; }}
    h1 {{ border-bottom: 2px solid #333; padding-bottom: .3em; }}
    .meta {{ color: #666; font-size: .9em; }}
  </style>
</head>
<body>
  <h1>{title or 'AI Newsletter'}</h1>
  <p class="meta">{datetime.now().isoformat()} — {headline_count} headlines</p>
  {body_html}
</body>
</html>
"""


@click.command()
@click.option("--db", "db_path", default="newsletter_agent.db")
@click.option("--session", "session_id", required=True)
@click.option("--notify", is_flag=True, help="Send via Gmail (not implemented in Phase 2)")
def cli(db_path: str, session_id: str, notify: bool) -> None:
    if notify:
        raise click.ClickException(
            "--notify Gmail send is not implemented in Phase 2. "
            "Use Phase 2b or send the file manually."
        )

    state = NewsletterAgentState(session_id=session_id, db_path=db_path).load_latest_from_db()
    if state is None:
        raise click.ClickException(f"No state found for session {session_id}")

    state.start_step("send")
    state.save_checkpoint("send")

    body = state.final_newsletter or (
        "<p><em>Dummy newsletter — pipeline ran through gather/download/send "
        "without the LLM steps.</em></p>"
    )
    html = _render_html(state.newsletter_title, body, len(state.headline_data))

    _OUT_DIR.mkdir(exist_ok=True)
    today = date.today().isoformat()
    out_file = _OUT_DIR / f"{today}.html"
    out_file.write_text(html)

    latest = _OUT_DIR / "latest.html"
    if latest.exists() or latest.is_symlink():
        latest.unlink()
    try:
        latest.symlink_to(out_file.name)
    except OSError:
        # Symlinks may fail on some platforms; copy as fallback
        latest.write_text(html)

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO newsletters (session_id, title, html, sent_at, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (session_id, state.newsletter_title or "AI Newsletter", html,
             None, datetime.now().isoformat()),
        )
        conn.commit()

    state.complete_step("send", message=f"wrote {out_file}")
    state.save_checkpoint("send")
    click.echo(f"Newsletter written to {out_file}")


if __name__ == "__main__":  # pragma: no cover
    sys.exit(cli())
```

- [ ] **Step 4: Run tests + commit**

```bash
.venv/bin/pytest tests/test_step_send.py -v
# Expect 5 passed
git add lib/steps/send.py tests/test_step_send.py
git commit -m "feat(steps): news:send renders HTML to out/ (no Gmail in Phase 2)"
```

- [ ] **Step 5: Add `skills/send/SKILL.md`**

Create `/Users/drucev/projects/news_agent/skills/send/SKILL.md`:
```markdown
---
name: news:send
description: Render the session's newsletter as styled HTML, write to out/YYYY-MM-DD.html, update out/latest.html symlink, and insert a row in the newsletters table. Phase 2: preview only — no Gmail.
---

# news:send

Step 11 (final) of `/news:run`.

## How to invoke

```bash
python -m lib.steps.send --db newsletter_agent.db --session SID
```

Phase 2 does NOT implement Gmail send. `--notify` will error.

## Output

- `out/<date>.html` — styled newsletter
- `out/latest.html` — symlink (or copy on platforms without symlinks)
- Row in `newsletters` table with title + html

## Behavior

Uses `state.final_newsletter` if set. Otherwise renders a "dummy" placeholder useful for testing the full pipeline before LLM steps are in place.
```

Commit:
```bash
git add skills/send/SKILL.md
git commit -m "docs(skills): news:send SKILL.md"
```

---

## Task 11: `lib/steps/show.py` + `skills/show/SKILL.md`

**Files:**
- Create: `lib/steps/show.py`
- Create: `skills/show/SKILL.md`
- Create: `tests/test_step_show.py`

Dumps full state record for a session. Optional `--step STEP` to show one step's checkpoint.

- [ ] **Step 1: Write failing tests**

Create `/Users/drucev/projects/news_agent/tests/test_step_show.py`:
```python
from click.testing import CliRunner
from lib.db import init_db
from lib.state import NewsletterAgentState
from lib.steps.show import cli as show_cli


def _setup(tmp_db):
    init_db(tmp_db)
    state = NewsletterAgentState(session_id="x1", db_path=tmp_db,
                                 sources_file="sources.yaml",
                                 sources={"A": {"type": "rss"}})
    state.complete_step("init", message="setup")
    state.start_step("gather")
    state.save_checkpoint("init")
    state.save_checkpoint("gather")


def test_show_dumps_full_state(tmp_db):
    _setup(tmp_db)
    runner = CliRunner()
    result = runner.invoke(show_cli, ["x1", "--db", tmp_db])
    assert result.exit_code == 0
    assert "x1" in result.output
    assert "init" in result.output
    assert "gather" in result.output
    assert "complete" in result.output.lower()


def test_show_specific_step(tmp_db):
    _setup(tmp_db)
    runner = CliRunner()
    result = runner.invoke(show_cli, ["x1", "--db", tmp_db, "--step", "init"])
    assert result.exit_code == 0
    assert "init" in result.output


def test_show_missing_session(tmp_db):
    init_db(tmp_db)
    runner = CliRunner()
    result = runner.invoke(show_cli, ["nope", "--db", tmp_db])
    assert "No state" in result.output or "not found" in result.output.lower()
```

- [ ] **Step 2: Implement `lib/steps/show.py`**

Create `/Users/drucev/projects/news_agent/lib/steps/show.py`:
```python
"""news:show — dump full state record for a session."""
from __future__ import annotations

import json
import sys

import click

from lib.state import NewsletterAgentState


@click.command()
@click.argument("session_id")
@click.option("--db", "db_path", default="newsletter_agent.db")
@click.option("--step", default=None, help="Show only one step's checkpoint")
def cli(session_id: str, db_path: str, step: str | None) -> None:
    state = NewsletterAgentState(session_id=session_id, db_path=db_path)
    loaded = state.load_from_db(step) if step else state.load_latest_from_db()
    if loaded is None:
        click.echo(f"No state found for session {session_id}"
                   + (f" at step {step}" if step else ""))
        return

    click.echo(f"Session: {session_id}")
    click.echo(loaded.get_workflow_status_report())
    click.echo("")
    click.echo("Steps written:")
    for s in loaded.list_session_steps():
        click.echo(f"  - {s['step_name']:<14} {s['updated_at']}")
    click.echo("")
    click.echo(f"Headlines: {len(loaded.headline_data)}")
    click.echo(f"Sections:  {len(loaded.newsletter_section_data)}")
    click.echo(f"Newsletter title: {loaded.newsletter_title or '(none)'}")
    click.echo(f"Newsletter length: {len(loaded.final_newsletter)} chars")


if __name__ == "__main__":  # pragma: no cover
    sys.exit(cli())
```

- [ ] **Step 3: Run tests + commit**

```bash
.venv/bin/pytest tests/test_step_show.py -v
# Expect 3 passed
git add lib/steps/show.py tests/test_step_show.py
git commit -m "feat(steps): news:show dumps full session state"
```

- [ ] **Step 4: Add SKILL.md**

Create `/Users/drucev/projects/news_agent/skills/show/SKILL.md`:
```markdown
---
name: news:show
description: Dump full state record for a session — workflow status report, per-step checkpoints, headline/section counts, newsletter title and length. Optional --step to show a single step's checkpoint.
---

# news:show

```bash
python -m lib.steps.show <SID> [--db newsletter_agent.db] [--step STEP]
```

Useful for: deep diagnosis after a failed run, comparing session details, confirming what's in a checkpoint before resuming.
```

Commit:
```bash
git add skills/show/SKILL.md
git commit -m "docs(skills): news:show SKILL.md"
```

---

## Task 12: `lib/steps/reset.py` + `skills/reset/SKILL.md`

**Files:**
- Create: `lib/steps/reset.py`
- Create: `skills/reset/SKILL.md`
- Create: `tests/test_step_reset.py`

Logic:
- `--errors` → `state.clear_errors()` (reset ERROR steps to NOT_STARTED)
- `--from STEP` → reset STEP and everything after it
- `--all` → `state.reset()`
- Confirms by default; `--yes` to skip prompt.

- [ ] **Step 1: Write failing tests**

Create `/Users/drucev/projects/news_agent/tests/test_step_reset.py`:
```python
from click.testing import CliRunner
from lib.db import init_db
from lib.state import NewsletterAgentState, StepStatus
from lib.steps.reset import cli as reset_cli


def _setup(tmp_db):
    init_db(tmp_db)
    state = NewsletterAgentState(session_id="r1", db_path=tmp_db)
    state.complete_step("init")
    state.error_step("gather", error_message="boom")
    state.save_checkpoint("init")
    state.save_checkpoint("gather")


def test_reset_errors_clears_error_state(tmp_db):
    _setup(tmp_db)
    runner = CliRunner()
    result = runner.invoke(reset_cli, ["r1", "--db", tmp_db, "--errors", "--yes"])
    assert result.exit_code == 0
    state = NewsletterAgentState(session_id="r1", db_path=tmp_db).load_latest_from_db()
    assert state.get_step("gather").status == StepStatus.NOT_STARTED
    assert state.get_step("gather").error_message == ""


def test_reset_from_step_resets_step_and_after(tmp_db):
    init_db(tmp_db)
    state = NewsletterAgentState(session_id="r2", db_path=tmp_db)
    state.complete_step("init")
    state.complete_step("gather")
    state.complete_step("filter")
    state.save_checkpoint("filter")
    runner = CliRunner()
    runner.invoke(reset_cli, ["r2", "--db", tmp_db, "--from", "gather", "--yes"])

    state = NewsletterAgentState(session_id="r2", db_path=tmp_db).load_latest_from_db()
    assert state.get_step("init").status == StepStatus.COMPLETE
    assert state.get_step("gather").status == StepStatus.NOT_STARTED
    assert state.get_step("filter").status == StepStatus.NOT_STARTED


def test_reset_all(tmp_db):
    _setup(tmp_db)
    runner = CliRunner()
    runner.invoke(reset_cli, ["r1", "--db", tmp_db, "--all", "--yes"])
    state = NewsletterAgentState(session_id="r1", db_path=tmp_db).load_latest_from_db()
    assert all(s.status == StepStatus.NOT_STARTED for s in state.steps)


def test_reset_requires_one_action_flag(tmp_db):
    _setup(tmp_db)
    runner = CliRunner()
    result = runner.invoke(reset_cli, ["r1", "--db", tmp_db, "--yes"])
    assert result.exit_code != 0
    assert "one of" in result.output.lower() or "--errors" in result.output
```

- [ ] **Step 2: Implement `lib/steps/reset.py`**

Create `/Users/drucev/projects/news_agent/lib/steps/reset.py`:
```python
"""news:reset — reset workflow step(s) for a session."""
from __future__ import annotations

import sys

import click

from lib.state import NewsletterAgentState, StepStatus, WORKFLOW_STEPS


@click.command()
@click.argument("session_id")
@click.option("--db", "db_path", default="newsletter_agent.db")
@click.option("--errors", is_flag=True, help="Reset only ERROR steps to NOT_STARTED")
@click.option("--from", "from_step", default=None, help="Reset this step and all after it")
@click.option("--all", "reset_all", is_flag=True, help="Reset every step to NOT_STARTED")
@click.option("--yes", is_flag=True, help="Skip confirmation prompt")
def cli(session_id: str, db_path: str, errors: bool, from_step: str | None,
        reset_all: bool, yes: bool) -> None:
    flags = [errors, bool(from_step), reset_all]
    if sum(1 for f in flags if f) != 1:
        raise click.ClickException(
            "Specify exactly one of: --errors, --from STEP, --all"
        )

    state = NewsletterAgentState(session_id=session_id, db_path=db_path).load_latest_from_db()
    if state is None:
        raise click.ClickException(f"No state found for session {session_id}")

    if errors:
        affected = state.get_failed_steps()
        action = "clear errors"
    elif from_step:
        ids = [sid for sid, *_ in WORKFLOW_STEPS]
        if from_step not in ids:
            raise click.ClickException(f"Unknown step: {from_step}")
        idx = ids.index(from_step)
        affected = ids[idx:]
        action = f"reset from {from_step}"
    else:
        affected = [s.id for s in state.steps]
        action = "reset ALL steps"

    if not yes:
        click.echo(f"About to {action} on session {session_id}:")
        for sid in affected:
            click.echo(f"  - {sid}")
        if not click.confirm("Proceed?"):
            click.echo("Aborted.")
            return

    if errors:
        state.clear_errors()
    elif from_step:
        for sid in affected:
            step = state.get_step(sid)
            if step:
                step.status = StepStatus.NOT_STARTED
                step.started_at = None
                step.completed_at = None
                step.error_message = ""
                step.status_message = ""
    else:
        state.reset()

    # Save checkpoint at whatever the current step is (or init if none)
    current = state.get_current_step() or "init"
    state.save_checkpoint(current)
    click.echo(f"Reset complete ({action}).")


if __name__ == "__main__":  # pragma: no cover
    sys.exit(cli())
```

- [ ] **Step 3: Run tests + commit**

```bash
.venv/bin/pytest tests/test_step_reset.py -v
# Expect 4 passed
git add lib/steps/reset.py tests/test_step_reset.py
git commit -m "feat(steps): news:reset --errors/--from/--all"
```

- [ ] **Step 4: Add SKILL.md**

Create `/Users/drucev/projects/news_agent/skills/reset/SKILL.md`:
```markdown
---
name: news:reset
description: Reset workflow step(s) on a session. --errors clears ERROR steps; --from STEP resets a step and everything after; --all resets every step. Confirms by default; --yes to skip.
---

# news:reset

```bash
python -m lib.steps.reset <SID> [--db ...] (--errors | --from STEP | --all) [--yes]
```

## Use cases

- `--errors`: an LLM-step retry needs a clean slate after a transient failure.
- `--from gather`: starting fresh from gather but keeping init.
- `--all`: rerun the whole session from scratch (rare).
```

Commit:
```bash
git add skills/reset/SKILL.md
git commit -m "docs(skills): news:reset SKILL.md"
```

---

## Task 13: `lib/steps/resume.py` + `skills/resume/SKILL.md`

**Files:**
- Create: `lib/steps/resume.py`
- Create: `skills/resume/SKILL.md`
- Create: `tests/test_step_resume.py`

Logic:
1. Load latest state.
2. Clear any ERROR steps back to NOT_STARTED (auto, unless `--no-clear`).
3. Print which step we'll resume from.
4. Print invocation hints — Phase 2 doesn't have `/news:run` orchestrator yet, so `resume` mostly prints "resume at STEP — run `python -m lib.steps.STEP --session SID` to continue."
5. With `--yes`, automatically invoke the next step's CLI via subprocess. (Optional; keep it print-only if subprocess invocation feels too clever for Phase 2.)

For Phase 2, keep this skill **print-only** — actually invoking subsequent steps comes when `/news:run` orchestrator ships in Phase 6.

- [ ] **Step 1: Write failing tests**

Create `/Users/drucev/projects/news_agent/tests/test_step_resume.py`:
```python
from click.testing import CliRunner
from lib.db import init_db
from lib.state import NewsletterAgentState, StepStatus
from lib.steps.resume import cli as resume_cli


def _setup(tmp_db):
    init_db(tmp_db)
    state = NewsletterAgentState(session_id="rs1", db_path=tmp_db)
    state.complete_step("init")
    state.error_step("gather", "boom")
    state.save_checkpoint("gather")


def test_resume_clears_errors(tmp_db):
    _setup(tmp_db)
    runner = CliRunner()
    result = runner.invoke(resume_cli, ["rs1", "--db", tmp_db])
    assert result.exit_code == 0
    state = NewsletterAgentState(session_id="rs1", db_path=tmp_db).load_latest_from_db()
    assert state.get_step("gather").status == StepStatus.NOT_STARTED


def test_resume_prints_next_step(tmp_db):
    _setup(tmp_db)
    runner = CliRunner()
    result = runner.invoke(resume_cli, ["rs1", "--db", tmp_db])
    assert "gather" in result.output


def test_resume_all_complete(tmp_db):
    init_db(tmp_db)
    state = NewsletterAgentState(session_id="done", db_path=tmp_db)
    for sid, *_ in [
        ("init",), ("gather",), ("filter",), ("download",), ("summarize",),
        ("rate",), ("cluster",), ("select",), ("draft",), ("rewrite",), ("send",),
    ]:
        state.complete_step(sid)
    state.save_checkpoint("send")

    runner = CliRunner()
    result = runner.invoke(resume_cli, ["done", "--db", tmp_db])
    assert "complete" in result.output.lower()


def test_resume_missing_session(tmp_db):
    init_db(tmp_db)
    runner = CliRunner()
    result = runner.invoke(resume_cli, ["nope", "--db", tmp_db])
    assert "No state" in result.output or "not found" in result.output.lower()
```

- [ ] **Step 2: Implement `lib/steps/resume.py`**

Create `/Users/drucev/projects/news_agent/lib/steps/resume.py`:
```python
"""news:resume — clear errors and report the next step to invoke."""
from __future__ import annotations

import sys

import click

from lib.state import NewsletterAgentState


@click.command()
@click.argument("session_id")
@click.option("--db", "db_path", default="newsletter_agent.db")
@click.option("--no-clear", is_flag=True, help="Don't auto-clear ERROR steps")
def cli(session_id: str, db_path: str, no_clear: bool) -> None:
    state = NewsletterAgentState(session_id=session_id, db_path=db_path).load_latest_from_db()
    if state is None:
        click.echo(f"No state found for session {session_id}")
        return

    cleared = []
    if not no_clear:
        cleared = state.get_failed_steps()
        if cleared:
            state.clear_errors()
            state.save_checkpoint(state.get_current_step() or "init")

    cur = state.get_current_step()
    click.echo(f"Session: {session_id}")
    if cleared:
        click.echo(f"Cleared {len(cleared)} error step(s): {', '.join(cleared)}")
    if cur is None:
        click.echo("All steps complete. Nothing to resume.")
        return
    click.echo(f"Next step: {cur}")
    click.echo(f"To continue: python -m lib.steps.{cur} --db {db_path} --session {session_id}")


if __name__ == "__main__":  # pragma: no cover
    sys.exit(cli())
```

- [ ] **Step 3: Run tests + commit**

```bash
.venv/bin/pytest tests/test_step_resume.py -v
# Expect 4 passed
git add lib/steps/resume.py tests/test_step_resume.py
git commit -m "feat(steps): news:resume clears errors + prints next step"
```

- [ ] **Step 4: Add SKILL.md**

Create `/Users/drucev/projects/news_agent/skills/resume/SKILL.md`:
```markdown
---
name: news:resume
description: Inspect a session, clear any ERROR steps back to NOT_STARTED, and print the next step to invoke. Phase 2 is print-only — the /news:run orchestrator (Phase 6) will actually re-enter the pipeline.
---

# news:resume

```bash
python -m lib.steps.resume <SID> [--db ...] [--no-clear]
```

## Behavior

1. Loads latest state.
2. By default, clears all ERROR steps to NOT_STARTED.
3. Prints the next step (first incomplete) and the exact command to invoke it.

Phase 2 doesn't yet auto-invoke; `/news:run` (Phase 6) will.
```

Commit:
```bash
git add skills/resume/SKILL.md
git commit -m "docs(skills): news:resume SKILL.md"
```

---

## Task 14: End-to-end Phase 2 verification

**Files:** none modified.

- [ ] **Step 1: Full test suite + coverage**

```bash
cd /Users/drucev/projects/news_agent
.venv/bin/pytest tests/ -v --cov=lib --cov-report=term-missing
```
Expected: all tests pass. New coverage target: `lib/fetch/*` ≥ 70%, `lib/steps/*` ≥ 80%.

- [ ] **Step 2: End-to-end dummy-newsletter smoke test**

Use a temp directory so we don't pollute the repo:

```bash
cd /tmp
rm -rf p2-smoke && mkdir p2-smoke && cd p2-smoke
ln -s /Users/drucev/projects/news_agent/sources.yaml sources.yaml
# Init
/Users/drucev/projects/news_agent/.venv/bin/python -m lib.steps.init --db p2.db --sources sources.yaml --session p2 2>&1 | tail -5
# Gather (will actually hit the network — may take minutes)
/Users/drucev/projects/news_agent/.venv/bin/python -m lib.steps.gather --db p2.db --session p2 2>&1 | tail -10
# Status
/Users/drucev/projects/news_agent/.venv/bin/python -m lib.steps.status --db p2.db --session p2
# Show full state
/Users/drucev/projects/news_agent/.venv/bin/python -m lib.steps.show p2 --db p2.db
# Skip download for smoke (it's slow); jump to send with dummy content
/Users/drucev/projects/news_agent/.venv/bin/python -m lib.steps.send --db p2.db --session p2
# Check output
ls -la out/
cat runs/p2/gather.json | head -30
```

Expected:
- `init` → "Session created: p2"
- `gather` → "Gathered N new headlines from M sources"
- `status` → progress > init step, headline count > 0
- `show` → workflow report + headline count
- `send` → "Newsletter written to out/YYYY-MM-DD.html" (dummy content)
- `runs/p2/gather.json` has per-source success/failure breakdown
- `sites` table has rows for each HTML domain with their working `scrape_method`

To verify sites learning:
```bash
sqlite3 p2.db "SELECT domain, scrape_method FROM sites"
```

- [ ] **Step 3: Resume / reset smoke test**

```bash
cd /tmp/p2-smoke
# Force an error on gather (simulate by manually editing state or just inducing a real error)
# Then:
/Users/drucev/projects/news_agent/.venv/bin/python -m lib.steps.resume p2 --db p2.db
/Users/drucev/projects/news_agent/.venv/bin/python -m lib.steps.reset p2 --db p2.db --errors --yes
```

- [ ] **Step 4: Tag**

```bash
cd /Users/drucev/projects/news_agent
git tag phase-2-complete
git log --oneline phase-1-complete..phase-2-complete
```

---

## Notes for the implementer

- **TDD discipline.** Write failing tests BEFORE implementation. Skipping this once is fine occasionally for trivial config edits but not for the substantive tasks.
- **The legacy code is reference, not gospel.** Where the legacy has 50 lines of edge-case handling that aren't covered by tests in the new system, prefer the minimal correct path. Add edge-case handling only when a test demands it.
- **Don't port what we deferred.** Embeddings/dedup, Gmail send, rate limiting, fingerprinting, concurrent Playwright pooling — all deferred. If you find yourself porting them, stop.
- **`monkeypatch.chdir` matters.** Many tests change to `tmp_path` because the code writes to relative paths (`out/`, `runs/`, `download/`). The fixture handles this; don't drop it.
- **`.venv/bin/pytest` not `pytest`.** The venv is the authoritative interpreter.
- **Pyright "import could not be resolved" warnings.** These are an IDE/pyright env issue, not real bugs. Pytest finds modules fine. Don't chase these.

## Out of scope for Phase 2

- Embedding-based deduplication (Phase 3)
- Gmail send / actual notification (later phase or when user explicitly asks)
- `lib/steps/checkpoint.py` (`news:checkpoint`) and `news:diff`, `news:gc` (Phase 8 — polish)
- True concurrency in `download` (Phase 2b or later)
- LLM-based extraction (Phase 3+)
- The `/news:run` orchestrator skill (Phase 6)
