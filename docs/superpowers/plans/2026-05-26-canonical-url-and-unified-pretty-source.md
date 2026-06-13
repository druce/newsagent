# Canonical URL + Unified Pretty Source Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop mislabeling articles with their gather-time source name (e.g. "- Hacker News" on a NYT URL). Always resolve the publisher from the post-redirect URL refined by `<link rel="canonical">`, and look the pretty name up in `sites.name` — LLM-resolving any new domain we haven't seen before.

**Architecture:** Two pure additions and one simplification.
1. New `lib/fetch/canonical.py` parses `<link rel="canonical">` from HTML with a same-bare-domain guard.
2. `download.py:_persist` consults the canonical extractor before keying the cache and writing `urls.final_url`. The existing LLM-sitename pathway already covers any new domain; we just drop the aggregator gate so it runs for every unresolved domain.
3. `lib/sources.py` loses its `_AGGREGATORS` special-case. `pretty_source` becomes: derive domain from `final_url`, look up in `sites`, fall back to the gather-time source label only when no domain is available (i.e. failed downloads with an aggregator-style gather URL).

**Tech Stack:** Python 3.11, BeautifulSoup (already a dep), pytest, Click. No new dependencies.

---

## File Structure

**Create:**
- `lib/fetch/canonical.py` — pure-HTML canonical URL extractor with same-domain guard.
- `tests/test_fetch_canonical.py` — unit tests for the extractor.
- `tests/test_sources.py` — unit tests for `pretty_source` (no existing coverage).

**Modify:**
- `lib/sources.py` — remove `_AGGREGATORS`, simplify `pretty_source`, change `_resolve_domain` to return `Optional[str]` so callers can distinguish "found in DB" from "fell through".
- `lib/steps/download.py` — call canonical extractor inside the three phase loops (httpx, playwright, brightdata) before `_persist`; drop the `_AGGREGATORS` gate in `_populate_site_names` so every unresolved domain goes through the LLM-sitename path.
- `tests/test_step_download.py` — add canonical-refinement assertions; update the site-name expectations so aggregator-source articles get re-attributed by domain alone.

**Out of scope (deliberately deferred):**
- Module-level Playwright context cache.
- Per-domain rate limiter / worker pool.
- `last_updated` extraction from meta / JSON-LD.

---

## Task 1: Canonical URL extractor

**Files:**
- Create: `lib/fetch/canonical.py`
- Test:   `tests/test_fetch_canonical.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_fetch_canonical.py`:

```python
"""Tests for lib.fetch.canonical.extract_canonical_url."""
from __future__ import annotations

from lib.fetch.canonical import extract_canonical_url


def _html(canonical: str | None) -> str:
    link = f'<link rel="canonical" href="{canonical}">' if canonical else ""
    return f"<html><head>{link}</head><body>x</body></html>"


def test_absolute_same_domain_returned():
    html = _html("https://example.com/foo")
    assert extract_canonical_url(html, "https://example.com/foo?utm=x") == \
        "https://example.com/foo"


def test_relative_resolved_against_page_url():
    html = _html("/foo/bar")
    assert extract_canonical_url(html, "https://example.com/baz") == \
        "https://example.com/foo/bar"


def test_www_prefix_treated_as_same_domain():
    html = _html("https://example.com/foo")
    assert extract_canonical_url(html, "https://www.example.com/foo") == \
        "https://example.com/foo"


def test_subdomain_is_not_same_domain():
    # news.example.com canonicalizing to example.com — reject, too risky.
    html = _html("https://example.com/foo")
    assert extract_canonical_url(html, "https://news.example.com/foo") is None


def test_cross_domain_canonical_rejected():
    html = _html("https://attacker.com/x")
    assert extract_canonical_url(html, "https://example.com/foo") is None


def test_no_canonical_returns_none():
    assert extract_canonical_url(_html(None), "https://example.com/foo") is None


def test_malformed_href_returns_none():
    html = _html("javascript:void(0)")
    assert extract_canonical_url(html, "https://example.com/foo") is None


def test_empty_href_returns_none():
    html = '<html><head><link rel="canonical" href=""></head></html>'
    assert extract_canonical_url(html, "https://example.com/foo") is None


def test_first_valid_canonical_when_multiple():
    html = (
        '<html><head>'
        '<link rel="canonical" href="https://example.com/first">'
        '<link rel="canonical" href="https://example.com/second">'
        '</head></html>'
    )
    assert extract_canonical_url(html, "https://example.com/foo") == \
        "https://example.com/first"


def test_garbage_html_returns_none():
    assert extract_canonical_url("not html", "https://example.com/foo") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_fetch_canonical.py -v`
Expected: all 10 tests FAIL with `ModuleNotFoundError: No module named 'lib.fetch.canonical'`.

- [ ] **Step 3: Implement the extractor**

Create `lib/fetch/canonical.py`:

```python
"""Extract a same-domain <link rel="canonical"> from an article HTML page.

The publisher's canonical URL is the right dedup/identification key when it
exists — it strips tracking params and resolves CDN variants. We accept a
canonical only when its bare domain (minus a leading 'www.') equals the
post-redirect URL's bare domain. Anything else (cross-domain canonicals,
subdomain canonicals, junk schemes) is rejected, so a hostile publisher can't
hijack one of our cache entries.
"""
from __future__ import annotations

from typing import Optional
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup


def _bare(host: str) -> str:
    host = (host or "").lower()
    return host[4:] if host.startswith("www.") else host


def extract_canonical_url(html: str, page_url: str) -> Optional[str]:
    """Return a validated canonical URL or None.

    Validation rules:
      - <link rel="canonical"> present with a non-empty href.
      - href resolves (via urljoin) to an http(s) absolute URL.
      - Resolved URL's bare host equals the page_url's bare host
        (case-insensitive, www-prefix-tolerant). Cross-domain or
        subdomain-mismatch canonicals are rejected.
    """
    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception:
        return None

    page_host = _bare(urlparse(page_url).hostname or "")
    if not page_host:
        return None

    for link in soup.find_all("link", rel="canonical"):
        href = (link.get("href") or "").strip()
        if not href:
            continue
        try:
            resolved = urljoin(page_url, href)
            parsed = urlparse(resolved)
        except Exception:
            continue
        if parsed.scheme not in ("http", "https"):
            continue
        if _bare(parsed.hostname or "") != page_host:
            continue
        return resolved
    return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_fetch_canonical.py -v`
Expected: all 10 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add lib/fetch/canonical.py tests/test_fetch_canonical.py
git commit -m "feat(fetch): extract <link rel=canonical> with same-domain guard"
```

---

## Task 2: Simplify `pretty_source` — drop the aggregator special case

**Files:**
- Modify: `lib/sources.py` (lines 28, 75-87, 90-123)
- Test:   `tests/test_sources.py` (new file)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_sources.py`:

```python
"""Tests for lib.sources.pretty_source — the unified, no-aggregator behavior."""
from __future__ import annotations

import sqlite3

import pytest

from lib.db import init_db
from lib.sources import pretty_source, reset_db_cache


def _seed_site(db_path: str, domain: str, name: str) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO sites(domain, name) VALUES(?, ?) "
            "ON CONFLICT(domain) DO UPDATE SET name=excluded.name",
            (domain, name),
        )
        conn.commit()
    reset_db_cache()


def test_known_domain_returns_db_name(tmp_db):
    init_db(tmp_db)
    _seed_site(tmp_db, "nytimes.com", "The New York Times")
    assert pretty_source(
        "https://www.nytimes.com/2026/05/26/foo",
        fallback_source="Hacker News",
        db_path=tmp_db,
    ) == "The New York Times"


def test_unknown_domain_falls_back_to_source_label(tmp_db):
    init_db(tmp_db)
    assert pretty_source(
        "https://obscure.example.org/post",
        fallback_source="Ars Technica",
        db_path=tmp_db,
    ) == "Ars Technica"


def test_unknown_domain_no_fallback_returns_bare_domain(tmp_db):
    init_db(tmp_db)
    assert pretty_source(
        "https://obscure.example.org/post",
        fallback_source=None,
        db_path=tmp_db,
    ) == "obscure.example.org"


def test_no_final_url_falls_back_to_source(tmp_db):
    init_db(tmp_db)
    assert pretty_source(
        None,
        fallback_source="Hacker News",
        db_path=tmp_db,
    ) == "Hacker News"


def test_no_final_url_no_source_returns_unknown(tmp_db):
    init_db(tmp_db)
    assert pretty_source(None, fallback_source=None, db_path=tmp_db) == "Unknown"


def test_subdomain_falls_through_to_parent(tmp_db):
    init_db(tmp_db)
    _seed_site(tmp_db, "yahoo.com", "Yahoo")
    assert pretty_source(
        "https://finance.yahoo.com/news/foo",
        fallback_source="NewsAPI",
        db_path=tmp_db,
    ) == "Yahoo"


def test_aggregator_source_no_longer_special_cased(tmp_db):
    """Old behavior preserved coincidentally: aggregator-gathered articles
    with a known publisher domain still resolve to the publisher name —
    but now for the same reason every other source does, not via a hard-coded set."""
    init_db(tmp_db)
    _seed_site(tmp_db, "nytimes.com", "The New York Times")
    # Was: special-cased because "Techmeme" in _AGGREGATORS.
    # Now: same path as any other source — domain wins.
    assert pretty_source(
        "https://www.nytimes.com/foo",
        fallback_source="Techmeme",
        db_path=tmp_db,
    ) == "The New York Times"


def test_known_domain_overrides_direct_source_label(tmp_db):
    """If we have a domain in sites, trust the DB. The gather-time source
    label is the fallback, not the override."""
    init_db(tmp_db)
    _seed_site(tmp_db, "arstechnica.com", "Ars Technica")
    assert pretty_source(
        "https://arstechnica.com/post",
        fallback_source="Ars Technica feed v2",  # noisy gather label
        db_path=tmp_db,
    ) == "Ars Technica"
```

Add a shared `tmp_db` fixture if one doesn't already exist. Check `tests/conftest.py`:

```bash
grep -n "tmp_db" tests/conftest.py
```

If `tmp_db` is not defined there, add it. Otherwise this task uses the existing fixture.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_sources.py -v`

Expected: at least `test_known_domain_overrides_direct_source_label` FAILs — current `pretty_source` returns the fallback_source unconditionally for non-aggregator sources (`lib/sources.py:117-118`). The other tests may pass for the wrong reason (aggregator path).

- [ ] **Step 3: Simplify `_resolve_domain` to return Optional[str]**

In `lib/sources.py`, replace lines 75-87:

```python
def _resolve_domain(domain: str, db_path: Optional[str]) -> Optional[str]:
    """Look up the domain in the sites table, with subdomain stripping.
    Returns the pretty name, or None if no row matches.
    """
    if not domain:
        return None
    if not db_path:
        return None
    db_map = _load_db_map(db_path)
    for c in _candidate_domains(domain):
        if c in db_map:
            return db_map[c]
    return None
```

- [ ] **Step 4: Rewrite `pretty_source` and drop `_AGGREGATORS`**

In `lib/sources.py`, remove the `_AGGREGATORS` definition (line 28) and replace lines 90-123 with:

```python
def pretty_source(
    final_url: str | None,
    fallback_source: str | None = None,
    *,
    db_path: Optional[str] = "newsletter_agent.db",
) -> str:
    """Best human-friendly source name for an article.

    Resolution order:
      1. If `final_url` resolves to a domain present in `sites.name`
         (with subdomain stripping), return that name. This is the
         authoritative path — the publisher is whoever's serving the URL.
      2. Else, return `fallback_source` if provided. This covers headlines
         that failed to download (no final_url) and unknown publishers where
         the gather-time source label is the best we have.
      3. Else, the bare domain of `final_url`.
      4. Else, "Unknown".

    `db_path` is the SQLite database holding the `sites` table.
    Pass `None` to skip the DB lookup (useful in tests).
    """
    domain = _bare_domain(final_url)
    if domain:
        name = _resolve_domain(domain, db_path=db_path)
        if name:
            return name
    if fallback_source:
        return fallback_source
    if domain:
        return domain
    return "Unknown"
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_sources.py -v`
Expected: all 8 tests PASS.

- [ ] **Step 6: Run the full suite to catch fallout**

Run: `.venv/bin/pytest tests/ -x -q`
Expected: any pre-existing test that depended on `_AGGREGATORS` being importable will fail with `ImportError`. Two known importers are `lib/steps/download.py` and `lib/utilities.py:87` (verified by grep at planning time). The download caller is fixed in Task 3; `lib/utilities.py` only imports `pretty_source` (no `_AGGREGATORS`), so should be unaffected.

If unexpected failures appear in tests outside `test_step_download.py`, stop and re-grep:

```bash
grep -rn "_AGGREGATORS" /Users/drucev/projects/newsagent --include="*.py"
```

Then add the missing importer to Task 3 before proceeding.

- [ ] **Step 7: Commit**

```bash
git add lib/sources.py tests/test_sources.py
git commit -m "refactor(sources): unify pretty_source via sites.name, drop aggregator special-case"
```

Note: at this point `lib/steps/download.py` still imports `_AGGREGATORS` and will raise ImportError at runtime. Task 3 fixes that immediately and should follow without delay. (The whole-suite run above will surface this in tests that actually import `download.cli`.)

---

## Task 3: Use canonical URL + drop aggregator gate in `download.py`

**Files:**
- Modify: `lib/steps/download.py` (imports, `_persist` callers in phases 0/1/2/3, `_populate_site_names` body)
- Modify: `tests/test_step_download.py` (add canonical assertion, drop aggregator assumptions)

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_step_download.py` (existing file — append at the bottom):

```python
def test_download_uses_canonical_url_when_present(tmp_path, tmp_db, monkeypatch):
    """When the fetched HTML carries a same-domain <link rel='canonical'>,
    final_url and the cache key should follow the canonical, not the
    post-redirect URL."""
    monkeypatch.chdir(tmp_path)
    _setup(tmp_db)

    canonical_html = (
        '<html><head>'
        '<link rel="canonical" href="https://example.com/canonical-a">'
        '</head><body>' + ("body text " * 100) + '</body></html>'
    )

    def fake_http(url):
        if url.endswith("/a"):
            # Post-redirect URL is /redirected-a, but canonical says /canonical-a
            return _body("A"), canonical_html, "https://example.com/redirected-a", None
        return _body("B"), "<html>nope</html>", "https://example.com/b", None

    with patch("lib.steps.download._http_fetch", side_effect=fake_http):
        with patch("lib.steps.download.fetch_urls_html_batch"):
            runner = CliRunner()
            result = runner.invoke(download_cli, ["--db", tmp_db, "--session", "d1"])
            assert result.exit_code == 0, result.output

    with sqlite3.connect(tmp_db) as conn:
        row = conn.execute(
            "SELECT final_url FROM urls WHERE initial_url=?",
            ("https://example.com/a",),
        ).fetchone()
    assert row[0] == "https://example.com/canonical-a"


def test_download_ignores_cross_domain_canonical(tmp_path, tmp_db, monkeypatch):
    """A canonical pointing to a different domain must be rejected; the
    post-redirect URL wins."""
    monkeypatch.chdir(tmp_path)
    _setup(tmp_db)

    hostile_html = (
        '<html><head>'
        '<link rel="canonical" href="https://attacker.com/take-over">'
        '</head><body>' + ("body text " * 100) + '</body></html>'
    )

    def fake_http(url):
        return _body("X"), hostile_html, "https://example.com/redirected-a", None

    with patch("lib.steps.download._http_fetch", side_effect=fake_http):
        with patch("lib.steps.download.fetch_urls_html_batch"):
            runner = CliRunner()
            result = runner.invoke(download_cli, ["--db", tmp_db, "--session", "d1"])
            assert result.exit_code == 0, result.output

    with sqlite3.connect(tmp_db) as conn:
        rows = {r[0] for r in conn.execute("SELECT final_url FROM urls").fetchall()}
    assert "https://example.com/redirected-a" in rows
    assert not any("attacker.com" in u for u in rows)


def test_site_name_resolves_via_domain_regardless_of_source_label(tmp_path, tmp_db, monkeypatch):
    """An article gathered with source='Hacker News' whose final_url is on
    nytimes.com must be labeled with the NYT name from sites.name — no
    aggregator allowlist required."""
    monkeypatch.chdir(tmp_path)
    init_db(tmp_db)
    # Seed the publisher domain in sites.
    with sqlite3.connect(tmp_db) as conn:
        conn.execute(
            "INSERT INTO sites(domain, name) VALUES(?, ?)",
            ("nytimes.com", "The New York Times"),
        )
        conn.commit()

    state = NewsletterAgentState(session_id="d1", db_path=tmp_db)
    state.complete_step("start")
    state.complete_step("gather")
    state.headline_data = [
        {"source": "Hacker News", "title": "T", "url": "https://news.ycombinator.com/item?id=1"},
    ]
    state.save_checkpoint("gather")
    with sqlite3.connect(tmp_db) as conn:
        conn.execute(
            "INSERT INTO urls(initial_url, final_url, title, source) VALUES(?, ?, ?, ?)",
            ("https://news.ycombinator.com/item?id=1",
             "https://news.ycombinator.com/item?id=1",
             "T", "Hacker News"),
        )
        conn.commit()

    def fake_http(url):
        # HN linkout resolves to NYT after redirect.
        return _body("nyt"), "<html><body>" + ("text " * 200) + "</body></html>", \
            "https://www.nytimes.com/2026/foo", None

    with patch("lib.steps.download._http_fetch", side_effect=fake_http):
        with patch("lib.steps.download.fetch_urls_html_batch"):
            runner = CliRunner()
            result = runner.invoke(download_cli, ["--db", tmp_db, "--session", "d1"])
            assert result.exit_code == 0, result.output

    reloaded = NewsletterAgentState(session_id="d1", db_path=tmp_db).load_latest_from_db()
    h = reloaded.headline_data[0]
    assert h.get("site_name") == "The New York Times", h


def test_unknown_domain_triggers_sitename_llm_for_any_source(tmp_path, tmp_db, monkeypatch):
    """The LLM-sitename resolution path used to fire only for aggregator
    sources. It must now fire for any source whose final_url domain isn't
    in sites."""
    from lib.prompts.sitename import SitenameOutput, SitenameResult

    monkeypatch.chdir(tmp_path)
    init_db(tmp_db)

    state = NewsletterAgentState(session_id="d1", db_path=tmp_db)
    state.complete_step("start")
    state.complete_step("gather")
    state.headline_data = [
        # Direct (non-aggregator) source, unknown publisher domain.
        {"source": "Some Direct Feed", "title": "T", "url": "https://brand-new-site.example/post"},
    ]
    state.save_checkpoint("gather")
    with sqlite3.connect(tmp_db) as conn:
        conn.execute(
            "INSERT INTO urls(initial_url, final_url, title, source) VALUES(?, ?, ?, ?)",
            ("https://brand-new-site.example/post",
             "https://brand-new-site.example/post",
             "T", "Some Direct Feed"),
        )
        conn.commit()

    def fake_http(url):
        return _body("x"), "<html><body>" + ("text " * 200) + "</body></html>", \
            "https://brand-new-site.example/post", None

    fake_result = SitenameOutput(results=[
        SitenameResult(domain="brand-new-site.example", site_name="Brand New Site"),
    ])

    with patch("lib.steps.download._http_fetch", side_effect=fake_http), \
         patch("lib.steps.download.fetch_urls_html_batch"), \
         patch("lib.steps.download.call_prompt", return_value=fake_result) as call:
        runner = CliRunner()
        result = runner.invoke(download_cli, ["--db", tmp_db, "--session", "d1"])
        assert result.exit_code == 0, result.output
        # The LLM was actually consulted.
        assert call.called
        domains_sent = {item["domain"] for item in call.call_args.args[1]["items"]}
        assert "brand-new-site.example" in domains_sent

    with sqlite3.connect(tmp_db) as conn:
        row = conn.execute(
            "SELECT name FROM sites WHERE domain=?", ("brand-new-site.example",),
        ).fetchone()
    assert row is not None and row[0] == "Brand New Site"
```

If `SitenameResult` has a different field name, check `lib/prompts/sitename.py` and adjust the import. Likely it's `SitenameResult(domain=..., site_name=...)` per the existing `_llm_resolve_unknown_domains` usage at `lib/steps/download.py:170-172`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_step_download.py -v`

Expected on a clean tree:
- `test_download_uses_canonical_url_when_present` FAILS — current code stores `https://example.com/redirected-a`, not the canonical.
- `test_download_ignores_cross_domain_canonical` PASSES (we don't honor canonical at all today, so the post-redirect URL is already what wins — but we keep this test as a guard against future regressions).
- `test_site_name_resolves_via_domain_regardless_of_source_label` may PASS today only because "Hacker News" → "nytimes.com" lookup goes through the bare-domain fallback. If it fails, that's because `pretty_source` is returning the fallback `"Hacker News"`. Either outcome confirms we need the Task 2 + Task 3 changes.
- `test_unknown_domain_triggers_sitename_llm_for_any_source` FAILS — current `_populate_site_names` filters by `_AGGREGATORS` (`download.py:198-200`) and skips non-aggregator sources entirely.

Also expect `ImportError: cannot import name '_AGGREGATORS' from 'lib.sources'` if Task 2 already merged — that's the next step's job to fix.

- [ ] **Step 3: Remove the `_AGGREGATORS` import from download.py**

In `lib/steps/download.py`, replace the import block at lines 38-44:

```python
from lib.sources import (
    _bare_domain,
    _candidate_domains,
    pretty_source,
    reset_db_cache,
)
```

(Removed `_AGGREGATORS`.)

- [ ] **Step 4: Add canonical refinement helper**

Add a new top-level import in `lib/steps/download.py` (group with other `lib.fetch` imports near line 34):

```python
from lib.fetch.canonical import extract_canonical_url
```

Add a helper near `_persist` (around line 297):

```python
def _refine_with_canonical(post_redirect_url: str, html: str | None) -> str:
    """Return the same-domain canonical if HTML carries one, else the
    post-redirect URL. Pure: no DB or network I/O."""
    if not html:
        return post_redirect_url
    canonical = extract_canonical_url(html, post_redirect_url)
    return canonical or post_redirect_url
```

- [ ] **Step 5: Apply the helper at every `_persist` call site**

In `lib/steps/download.py`, replace the three `_persist` invocations.

Phase 0 (Bright Data), around line 326-330. Change:

```python
            final = final_url or url
            _persist(url, text, html, final)
```

to:

```python
            final = _refine_with_canonical(final_url or url, html)
            _persist(url, text, html, final)
```

Phase 1 (httpx), around line 343-344. Change:

```python
                final = final_url or url
                _persist(url, text, html, final)
```

to:

```python
                final = _refine_with_canonical(final_url or url, html)
                _persist(url, text, html, final)
```

Phase 2 (Playwright), around line 374-375. Change:

```python
            final = final_url or url
            _persist(url, text, html, final)
```

to:

```python
            final = _refine_with_canonical(final_url or url, html)
            _persist(url, text, html, final)
```

Phase 3 (BD fallback), around line 406-407. Change:

```python
            final = final_url or url
            _persist(url, text, html, final)
```

to:

```python
            final = _refine_with_canonical(final_url or url, html)
            _persist(url, text, html, final)
```

- [ ] **Step 6: Drop the aggregator gate in `_populate_site_names`**

In `lib/steps/download.py`, replace lines 186-236 (the entire `_populate_site_names` function body) with:

```python
def _populate_site_names(state: NewsletterAgentState, db_path: str) -> tuple[int, int]:
    """Set h['site_name'] on every headline. For any headline whose
    final_url domain isn't yet in the sites table, LLM-resolve via the
    sitename prompt and persist before assignment.

    No aggregator allowlist: the publisher domain wins regardless of
    gather-time source label. Articles that failed to download (no
    final_url) keep their gather-time source as the displayable name.

    Returns (n_llm_resolved, n_headlines_with_site_name).
    """
    # Collect every bare domain we'll need to resolve.
    candidate_domains: set[str] = set()
    for h in state.headline_data:
        url = h.get("final_url") or h.get("url")
        d = _bare_domain(url)
        if d:
            candidate_domains.add(d)

    # Filter to domains genuinely missing from sites table (subdomain
    # stripping: 'finance.yahoo.com' -> 'yahoo.com' that DO resolve count
    # as known).
    with sqlite3.connect(db_path) as conn:
        known = {
            row[0]
            for row in conn.execute(
                "SELECT domain FROM sites "
                "WHERE name IS NOT NULL AND name != ''"
            )
        }
    unknown: set[str] = set()
    for d in candidate_domains:
        if any(c in known for c in _candidate_domains(d)):
            continue
        unknown.add(d)

    n_resolved = _llm_resolve_unknown_domains(unknown, db_path)

    # Set h['site_name'] for every headline. pretty_source handles the
    # full fallback chain (DB → fallback_source → bare domain → "Unknown").
    n_set = 0
    for h in state.headline_data:
        url = h.get("final_url") or h.get("url")
        h["site_name"] = pretty_source(url, h.get("source"), db_path=db_path)
        n_set += 1

    return n_resolved, n_set
```

Key changes vs. the existing version:
- No `_AGGREGATORS` filter when collecting candidates.
- Falls back to `h.get("url")` when `final_url` missing (covers download failures).
- Always sets `site_name` for every headline (the prior `elif h.get("source")` branch is now redundant — `pretty_source` returns `fallback_source` when no domain resolves).

- [ ] **Step 7: Run the new tests to verify they pass**

Run: `.venv/bin/pytest tests/test_step_download.py -v`
Expected: all 4 new tests PASS, all existing tests still PASS.

- [ ] **Step 8: Run the full suite**

Run: `.venv/bin/pytest tests/ -q`
Expected: 100% pass. If anything outside `tests/test_step_download.py` and `tests/test_sources.py` fails, the failure is likely a test that mutated headline state in a way that depended on the old aggregator-only `site_name` write. Inspect and update those test expectations directly — do not relax the new logic to accommodate them.

- [ ] **Step 9: Commit**

```bash
git add lib/steps/download.py tests/test_step_download.py
git commit -m "feat(download): refine final_url via canonical, resolve site_name by domain only"
```

---

## Verification

After all three tasks, manually sanity-check on a real session:

```bash
# Pick a recent session that downloaded HN-linked articles.
.venv/bin/python -m lib.steps.sessions --limit 5
# Inspect site_name distribution.
.venv/bin/python - <<'PY'
import sqlite3, collections
from lib.state import NewsletterAgentState
state = NewsletterAgentState(session_id="<SID>", db_path="newsletter_agent.db").load_latest_from_db()
labels = collections.Counter(h.get("site_name") for h in state.headline_data)
for name, n in labels.most_common(20):
    print(f"{n:4d}  {name}")
PY
```

Expectation: no "- Hacker News" suffix on URLs that left the HN domain. Articles from `nytimes.com`, `arstechnica.com`, etc. carry the publisher's pretty name.

Re-run `newsagent:download` on a copied DB to confirm cache regeneration uses the canonical-keyed filenames (the stem hash changes when canonical refines the URL, so the first re-run will re-download — this is expected and acceptable).

---

## Self-Review Notes

**Spec coverage:**
- Item 1 (Hacker News mislabeling): Task 2 + Task 3 step 6 (no aggregator gate; domain-only resolution).
- Item 5 (canonical URL): Task 1 + Task 3 steps 4-5 (extractor + four `_persist` call sites).
- User's refinement ("update pretty name of domain from sqlite if we get a new domain"): Task 3 step 6 — LLM-resolve fires for every unresolved domain regardless of source label.

**Placeholder scan:** None found. All test code, all production code shown inline.

**Type consistency:** `_resolve_domain` return type changed `str` → `Optional[str]` in Task 2 step 3; only call site is `pretty_source` itself, updated in Task 2 step 4. `_refine_with_canonical` accepts `str | None` for html (the BD/Playwright phases can legitimately have html=None on failure paths, though those paths don't reach `_persist`). `extract_canonical_url` consistently returns `Optional[str]`.

**Idempotency:** Re-running `download` on the same session overwrites the cache files (the `_clear_download_dirs` call at `download.py:256` already wipes them per run), so canonical refinement on a re-run is safe.
