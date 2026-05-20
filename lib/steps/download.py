"""newsagent:download — fetch full article text with adaptive http→playwright strategy.

Two-phase concurrent fetch:
  Phase 1 (http): every URL whose domain is not pinned to playwright tries
    httpx + trafilatura concurrently. Successes → text saved, sites.scrape_method='http'.
    Failures (HTTP error, paywall, thin/no extracted text) → deferred to phase 2.
  Phase 2 (playwright): URLs whose domain is pinned, or whose http attempt
    failed, fetched concurrently through one stealth Playwright context.
    Successes → text saved, sites.scrape_method='playwright'.

Both phases capture the post-redirect final URL (httpx: resp.url; Playwright:
page.url after navigation) and write it to urls.final_url + headline['final_url'].
Text files are keyed by sha256(final_url) so different feed redirects pointing
at the same canonical article share one cached file.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import click
import httpx
import trafilatura

import lib.prompts  # noqa: F401 — register SITENAME

from lib.db import Site
from lib.fetch.brightdata import scrape_urls_brightdata
from lib.fetch.playwright_runner import fetch_urls_html_batch
from lib.llm import call_prompt
from lib.prompts.sitename import SitenameOutput
from lib.sources import (
    _AGGREGATORS,
    _bare_domain,
    _candidate_domains,
    pretty_source,
    reset_db_cache,
)
from lib.state import NewsletterAgentState


_DOWNLOAD_DIR = Path("download")
_TXT_DIR = _DOWNLOAD_DIR / "txt"
_HTML_DIR = _DOWNLOAD_DIR / "html"
_HTTP_TIMEOUT = httpx.Timeout(20.0, connect=5.0)
_HTTP_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)
_MIN_TEXT_CHARS = 500


def _name_stem(final_url: str) -> str:
    """Domain-prefixed stem shared by the .txt and .html for a single article."""
    domain = _domain_of(final_url) or "unknown"
    h = hashlib.sha256(final_url.encode("utf-8")).hexdigest()[:24]
    return f"{domain}.{h}"


def _clear_download_dirs() -> None:
    """Wipe per-run caches so this download invocation starts clean."""
    _TXT_DIR.mkdir(parents=True, exist_ok=True)
    _HTML_DIR.mkdir(parents=True, exist_ok=True)
    for p in _TXT_DIR.glob("*.txt"):
        p.unlink()
    for p in _HTML_DIR.glob("*.html"):
        p.unlink()


def _extract_text(html: str) -> Optional[str]:
    return trafilatura.extract(html, include_comments=False, include_tables=False)


def _domain_of(url: str) -> str:
    return urlparse(url).hostname or ""


def _http_fetch(url: str) -> tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
    """Single-URL httpx fetch + trafilatura extract.

    Returns (text, html, final_url, error). On success: text, html, final_url set.
    On failure: text is None, error is a short message; html and final_url may
    be set if we got a response but extraction was too thin.
    """
    try:
        with httpx.Client(timeout=_HTTP_TIMEOUT, follow_redirects=True,
                          headers={"User-Agent": _HTTP_UA}) as client:
            resp = client.get(url)
    except httpx.HTTPError as exc:
        return None, None, None, f"http error: {exc}"[:200]

    final_url = str(resp.url)
    if resp.status_code != 200:
        return None, resp.text, final_url, f"http {resp.status_code}"
    text = _extract_text(resp.text)
    if not text or len(text) < _MIN_TEXT_CHARS:
        return None, resp.text, final_url, f"thin extract ({len(text) if text else 0} chars)"
    return text, resp.text, final_url, None


def _load_pinned_domains(db_path: str) -> set[str]:
    """Return the set of domains whose sites.scrape_method is 'playwright'."""
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT domain FROM sites WHERE scrape_method='playwright'"
        ).fetchall()
    return {r[0] for r in rows}


def _load_bright_data_domains(db_path: str) -> set[str]:
    """Return the set of domains marked sites.bright_data_enabled=1."""
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT domain FROM sites WHERE bright_data_enabled=1"
        ).fetchall()
    return {r[0] for r in rows}


def _commit_results(
    db_path: str,
    successes: list[tuple[str, str, str]],  # (initial_url, final_url, text_path)
    domain_method: dict[str, str],
) -> None:
    """Update urls.final_url and sites.scrape_method in one commit.

    `domain_method` may carry the synthetic value 'bright_data' for domains
    routed via the Web Unlocker — we do NOT write that to sites.scrape_method
    (BD routing is governed by the bright_data_enabled flag, not by
    scrape_method). Such rows still get last_seen bumped.
    """
    now_iso = datetime.now().isoformat()
    with sqlite3.connect(db_path) as conn:
        for initial_url, final_url, _ in successes:
            conn.execute(
                "UPDATE urls SET final_url=? WHERE initial_url=?",
                (final_url, initial_url),
            )
        for domain, method in domain_method.items():
            if not domain:
                continue
            persisted = method if method in ("http", "playwright") else None
            Site(domain=domain, scrape_method=persisted,
                 last_seen=now_iso).upsert(conn)
        conn.commit()


def _llm_resolve_unknown_domains(unknown: set[str], db_path: str) -> int:
    """Call the sitename prompt for `unknown` domains, upsert results into
    the sites table, clear the per-process cache. Returns the number of
    domains successfully resolved. On LLM error: logs and returns 0 (so the
    download step doesn't fail on transient API issues).
    """
    if not unknown:
        return 0
    items = [
        {"id": str(i), "domain": d}
        for i, d in enumerate(sorted(unknown))
    ]
    try:
        result = call_prompt("sitename", {"items": items})
    except Exception as exc:
        click.echo(
            f"sitename LLM call failed ({exc}); leaving {len(unknown)} "
            f"domains as bare-domain fallback.",
            err=True,
        )
        return 0
    assert isinstance(result, SitenameOutput)

    with sqlite3.connect(db_path) as conn:
        for rec in result.results:
            domain = rec.domain.lower()
            site_name = rec.site_name.strip()
            if not site_name:
                continue
            conn.execute(
                "INSERT INTO sites(domain, name) VALUES (?, ?) "
                "ON CONFLICT(domain) DO UPDATE SET name = excluded.name "
                "WHERE sites.name IS NULL OR sites.name = ''",
                (domain, site_name),
            )
        conn.commit()
    reset_db_cache()
    return len(result.results)


def _populate_site_names(state: NewsletterAgentState, db_path: str) -> tuple[int, int]:
    """Set h['site_name'] on every headline. For aggregator-sourced articles
    whose final_url domain isn't yet in the sites table, LLM-resolve via the
    sitename prompt and persist before assignment.

    Returns (n_llm_resolved, n_headlines_with_site_name).
    """
    # Build the set of bare domains that need a lookup (only from aggregator
    # sources — direct sources already carry their pretty name in `source`).
    candidate_domains: set[str] = set()
    for h in state.headline_data:
        if "final_url" not in h:
            continue
        if h.get("source") not in _AGGREGATORS:
            continue
        d = _bare_domain(h["final_url"])
        if d:
            candidate_domains.add(d)

    # Filter to domains genuinely missing from sites table (including parent-
    # domain matches like 'finance.yahoo.com' -> 'yahoo.com' that DO resolve).
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

    # Now set h['site_name'] for every headline. Articles without final_url
    # fall back to their gather-time source label.
    n_set = 0
    for h in state.headline_data:
        if "final_url" in h:
            h["site_name"] = pretty_source(
                h["final_url"], h.get("source"), db_path=db_path
            )
            n_set += 1
        elif h.get("source"):
            h["site_name"] = h["source"]
            n_set += 1

    return n_resolved, n_set


@click.command()
@click.option("--db", "db_path", default="newsletter_agent.db")
@click.option("--session", "session_id", required=True)
@click.option("--max", "max_urls", type=int, default=None,
              help="Cap on number of URLs downloaded this run.")
@click.option("--parallel", type=int, default=8, show_default=True,
              help="Concurrent fetches per phase.")
def cli(db_path: str, session_id: str, max_urls: int | None, parallel: int) -> None:
    state = NewsletterAgentState(session_id=session_id, db_path=db_path).load_latest_from_db()
    if state is None:
        raise click.ClickException(f"No state found for session {session_id}")

    state.start_step("download")
    state.save_checkpoint("download")

    # Fresh slate every run: wipe the per-article caches and any stale paths
    # left on headlines from a previous invocation.
    _clear_download_dirs()
    for h in state.headline_data:
        h.pop("text_path", None)
        h.pop("html_path", None)

    pending = list(state.headline_data)
    if max_urls:
        pending = pending[:max_urls]

    if not pending:
        state.complete_step("download", message="nothing to download")
        state.save_checkpoint("download")
        click.echo("Nothing to download.")
        return

    pinned_playwright = _load_pinned_domains(db_path)
    bd_enabled_domains = _load_bright_data_domains(db_path)

    # 3-way partition. bright_data_enabled trumps everything else; otherwise
    # playwright-pinned domains skip the http attempt; the rest try http first.
    bd_urls: list[str] = []
    pw_urls: list[str] = []
    http_urls: list[str] = []
    for h in pending:
        url = h["url"]
        d = _domain_of(url)
        if d in bd_enabled_domains:
            bd_urls.append(url)
        elif d in pinned_playwright:
            pw_urls.append(url)
        else:
            http_urls.append(url)

    # url → headline lookup
    by_url = {h["url"]: h for h in pending}
    successes: list[tuple[str, str, str]] = []  # (initial_url, final_url, text_path)
    failures: list[dict] = []
    # domain → method label. Precedence (best wins): bright_data > playwright > http.
    # We seed the methods we used; final write to sites.scrape_method only
    # records 'http' / 'playwright' (BD is gated by bright_data_enabled, not
    # scrape_method).
    domain_method: dict[str, str] = {}

    def _persist(url: str, text: str, html: str, final_url: str) -> tuple[Path, Path]:
        stem = _name_stem(final_url)
        txt_path = _TXT_DIR / f"{stem}.txt"
        html_path = _HTML_DIR / f"{stem}.html"
        if not txt_path.exists():
            txt_path.write_text(text, encoding="utf-8")
        if not html_path.exists():
            html_path.write_text(html, encoding="utf-8")
        h = by_url[url]
        h["final_url"] = final_url
        h["text_path"] = str(txt_path)
        h["html_path"] = str(html_path)
        successes.append((url, final_url, str(txt_path)))
        return txt_path, html_path

    # ── Phase 0: Bright Data for opt-in domains ────────────────────
    # These domains are paywalled / heavily protected; route them through
    # the Web Unlocker proxy. Sequential — SyncBrightDataClient is not
    # thread-safe.
    bd_failed_initial: list[str] = []
    if bd_urls:
        click.echo(f"Phase 0 (bright_data): {len(bd_urls)} URLs (sequential)")
        bd_results = scrape_urls_brightdata(bd_urls)
        for url, (text, html, final_url, err) in bd_results.items():
            if text is None or html is None:
                bd_failed_initial.append(url)
                continue
            final = final_url or url
            _persist(url, text, html, final)
            # Don't write to sites.scrape_method — BD routing is governed by
            # bright_data_enabled, not by scrape_method. Tag for the run log.
            domain_method.setdefault(_domain_of(final) or "", "bright_data")

    # ── Phase 1: httpx concurrent ──────────────────────────────────
    http_failed: list[str] = []
    if http_urls:
        click.echo(f"Phase 1 (http): {len(http_urls)} URLs, parallelism {parallel}")
        with ThreadPoolExecutor(max_workers=parallel) as pool:
            for url, (text, html, final_url, err) in zip(
                http_urls, pool.map(_http_fetch, http_urls)
            ):
                if text is None or html is None:
                    http_failed.append(url)
                    continue
                final = final_url or url
                _persist(url, text, html, final)
                d = _domain_of(final)
                if d:
                    domain_method.setdefault(d, "http")

    # ── Phase 2: playwright batch ──────────────────────────────────
    # Things to send to BD as a last resort. Carries (url, last_error_message)
    # so the failure record makes sense if BD also can't help.
    bd_fallback_candidates: list[tuple[str, str]] = []
    phase2_urls = pw_urls + http_failed
    if phase2_urls:
        click.echo(f"Phase 2 (playwright): {len(phase2_urls)} URLs, parallelism {parallel}")
        try:
            pw_results = fetch_urls_html_batch(phase2_urls, parallel=parallel)
        except Exception as exc:
            click.echo(f"Playwright batch failed: {exc}", err=True)
            pw_results = {u: (None, None, f"batch error: {exc}"[:200]) for u in phase2_urls}

        for url in phase2_urls:
            html, final_url, err = pw_results.get(url, (None, None, "no result"))
            if html is None:
                bd_fallback_candidates.append((url, err or "playwright: unknown"))
                continue
            text = _extract_text(html)
            if not text or len(text) < _MIN_TEXT_CHARS:
                bd_fallback_candidates.append((
                    url,
                    f"playwright thin extract ({len(text) if text else 0} chars)",
                ))
                continue
            final = final_url or url
            _persist(url, text, html, final)
            d = _domain_of(final)
            if d:
                domain_method[d] = "playwright"  # playwright wins over earlier 'http'

    # Phase-1 failures that never made it to Phase 2 (no fallback ran).
    if http_failed and not phase2_urls:
        for url in http_failed:
            bd_fallback_candidates.append((url, "http: no fallback"))

    # ── Phase 3: Bright Data fallback for phase-1/2 failures ───────
    # Also retry the bd_urls that failed in Phase 0 — same proxy, but a
    # second attempt might succeed past a transient error.
    bd_retry_urls = [u for u, _ in bd_fallback_candidates] + bd_failed_initial
    last_error_by_url: dict[str, str] = {u: e for u, e in bd_fallback_candidates}
    for u in bd_failed_initial:
        last_error_by_url.setdefault(u, "bright_data: initial attempt failed")
    if bd_retry_urls:
        click.echo(
            f"Phase 3 (bright_data fallback): {len(bd_retry_urls)} URLs (sequential)"
        )
        bd_retry_results = scrape_urls_brightdata(bd_retry_urls)
        for url, (text, html, final_url, err) in bd_retry_results.items():
            if text is None or html is None:
                prior = last_error_by_url.get(url, "unknown")
                failures.append({
                    "url": url,
                    "phase": "bright_data_fallback",
                    "error": f"{prior} | bd: {err or 'unknown'}",
                })
                continue
            final = final_url or url
            _persist(url, text, html, final)
            domain_method.setdefault(_domain_of(final) or "", "bright_data")

    _commit_results(db_path, successes, domain_method)

    # Resolve and store h['site_name'] on every headline. LLM-fill any
    # aggregator-sourced domains we don't recognize.
    n_llm_resolved, n_sites_set = _populate_site_names(state, db_path)
    if n_llm_resolved:
        click.echo(f"sitename LLM resolved {n_llm_resolved} new domains.")

    state.complete_step(
        "download",
        message=f"downloaded {len(successes)}/{len(pending)} articles",
    )
    state.save_checkpoint("download")

    runs_dir = Path("runs") / session_id
    runs_dir.mkdir(parents=True, exist_ok=True)
    (runs_dir / "download.json").write_text(json.dumps({
        "session_id": session_id,
        "completed_at": datetime.now().isoformat(),
        "downloaded": len(successes),
        "http_succeeded": sum(1 for m in domain_method.values() if m == "http"),
        "playwright_used_domains": sorted(
            d for d, m in domain_method.items() if m == "playwright"
        ),
        "failures": failures,
    }, indent=2))

    click.echo(
        f"Downloaded {len(successes)}/{len(pending)} articles "
        f"(http→playwright fallbacks: {len(http_failed)}; "
        f"failures: {len(failures)})."
    )


if __name__ == "__main__":  # pragma: no cover
    sys.exit(cli())
