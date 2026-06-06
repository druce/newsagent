"""newsagent:gather — fetch headlines from configured sources.

Source types:
  - rss   : feedparser via httpx (lib.fetch.rss)
  - html  : adaptive httpx + BeautifulSoup → Playwright fallback
            (lib.fetch.html parses <a> tags via lib.fetch.extract);
            reads sites.scrape_method as a hard pin (operator-controlled);
            does NOT write back — the runtime adapts at fetch time, but
            persistent failures should be pinned manually by the operator.
            Raw HTML saved to runs/<SID>/pages/<source>.html for inspection.
            NB: trafilatura is NOT used here — gather only needs the link graph
            of the landing page, not article body extraction.
  - rest  : generic JSON API (lib.fetch.rest)
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import click

from lib.db import Site
from lib.fetch.rss import fetch_rss
from lib.fetch.html import fetch_html
from lib.fetch.extract import extract_article_links
from lib.fetch.rest import fetch_rest
from lib.fetch.types import FetchResult
from lib.state import NewsletterAgentState
from lib.utilities import clean_url


def _domain_of(url: str) -> str:
    return urlparse(url).hostname or ""


def _safe_filename(source_name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", source_name).strip("_") or "source"


def _read_cached_page(session_id: str, source_name: str) -> str | None:
    path = Path("runs") / session_id / "pages" / f"{_safe_filename(source_name)}.html"
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")


def _fetch_html_cached(
    source_name: str, cfg: dict, session_id: str
) -> tuple[FetchResult, str | None, str | None]:
    """Build a FetchResult from a previously-saved landing page on disk.

    Missing cache file → ok=False (counts as an empty source at halt time).
    """
    html = _read_cached_page(session_id, source_name)
    if html is None:
        rel = f"runs/{session_id}/pages/{_safe_filename(source_name)}.html"
        return (
            FetchResult(source=source_name, ok=False,
                        error=f"cached page missing: {rel}"),
            "cached",
            None,
        )
    articles = extract_article_links(html, cfg, source_name)
    ok = len(articles) > 0
    err = None if ok else "no links extracted from cached page"
    return (
        FetchResult(source=source_name, articles=articles, ok=ok, error=err),
        "cached",
        html,
    )


def _fetch_one(
    source_name: str, cfg: dict, db_path: str,
    *, cached_pages: bool = False, session_id: str = "",
) -> tuple[FetchResult, str | None, str | None]:
    """Returns (result, used_method, raw_html). The latter two are only set for HTML sources."""
    stype = cfg.get("type", "html")
    if stype == "rss":
        rss_url = cfg.get("rss") or cfg.get("url")
        if not rss_url:
            return FetchResult(source=source_name, ok=False,
                               error="No rss/url in source config"), None, None
        return fetch_rss(source_name, rss_url), None, None
    if stype == "html":
        if cached_pages:
            return _fetch_html_cached(source_name, cfg, session_id)
        url = cfg.get("url", "")
        domain = _domain_of(url)
        prior_method: str | None = None
        if domain:
            with sqlite3.connect(db_path) as conn:
                site = Site.get_by_domain(conn, domain)
                if site:
                    prior_method = site.scrape_method
        result, used, raw_html = fetch_html(source_name, cfg, scrape_method=prior_method)
        return result, used, raw_html
    if stype == "rest":
        return fetch_rest(source_name, cfg), None, None
    return FetchResult(source=source_name, ok=False,
                       error=f"Unknown source type: {stype}"), None, None


@click.command()
@click.option("--db", "db_path", default="newsletter_agent.db")
@click.option("--session", "session_id", required=True)
@click.option("--cached-pages", "cached_pages", is_flag=True,
              help="Read html sources from runs/<SID>/pages/<source>.html "
                   "instead of fetching them; rss/rest are still fetched live.")
def cli(db_path: str, session_id: str, cached_pages: bool) -> None:
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

    pages_dir = Path("runs") / session_id / "pages"

    with sqlite3.connect(db_path) as conn:
        pinned_pw = {
            r[0] for r in conn.execute(
                "SELECT domain FROM sites WHERE scrape_method='playwright'"
            )
        }
    pw_fallback_sources: list[tuple[str, str]] = []  # (source_name, domain)

    for name, cfg in enabled_sources.items():
        result, used_method, raw_html = _fetch_one(
            name, cfg, db_path, cached_pages=cached_pages, session_id=session_id,
        )
        if (
            isinstance(cfg, dict)
            and used_method == "playwright"
            and _domain_of(cfg.get("url", "")) not in pinned_pw
        ):
            pw_fallback_sources.append((name, _domain_of(cfg.get("url", ""))))
        page_path: str | None = None
        if raw_html is not None:
            pages_dir.mkdir(parents=True, exist_ok=True)
            target = pages_dir / f"{_safe_filename(name)}.html"
            target.write_text(raw_html, encoding="utf-8")
            page_path = str(target)
        report_sources.append({
            "source": name,
            "type": cfg.get("type", "html") if isinstance(cfg, dict) else "html",
            "ok": result.ok,
            "method": used_method,
            "count": len(result.articles),
            "error": result.error,
            "page_path": page_path,
        })
        if result.ok:
            for a in result.articles:
                d = a.model_dump()
                d["url"] = clean_url(d.get("url", ""))
                if not d["url"]:
                    continue
                all_articles.append(d)

        # sites.scrape_method is operator-curated config (hard pin).
        # Do not auto-write — runtime adaptation happens in fetch_html.
        # Log when a source needed Playwright after httpx, so the operator
        # can decide whether to pin it.

    # Dedup against urls table; insert new ones
    new_count = 0
    new_by_source: dict[str, int] = {}
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
            new_by_source[a["source"]] = new_by_source.get(a["source"], 0) + 1
        conn.commit()

    for s in report_sources:
        s["new"] = new_by_source.get(s["source"], 0)

    state.headline_data.extend(new_articles_for_state)

    # A source is "empty" if it failed to fetch OR extracted 0 links (pre-dedup).
    # "0 new after dedup" is NOT empty — those links were simply already known.
    empty_sources = [
        {
            "source": s["source"],
            "type": s["type"],
            "error": s["error"],
            "saved": bool(s["page_path"]),
            "page_path": (
                f"runs/{session_id}/pages/{_safe_filename(s['source'])}.html"
                if s["type"] == "html" else None
            ),
        }
        for s in report_sources
        if not s["ok"] or s["count"] == 0
    ]

    if empty_sources:
        state.error_step(
            "gather",
            f"{len(empty_sources)} source(s) extracted 0 URLs: "
            + ", ".join(e["source"] for e in empty_sources),
        )
    else:
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
        "empty_sources": empty_sources,
    }, indent=2))

    if pw_fallback_sources:
        click.echo(
            "Note: httpx failed → Playwright recovered for these sources; "
            "consider pinning them with "
            "UPDATE sites SET scrape_method='playwright' WHERE domain=...:"
        )
        for name, domain in pw_fallback_sources:
            click.echo(f"  - {name} ({domain})")

    click.echo(f"Gathered {new_count} new headlines from {len(enabled_sources)} sources.")
    click.echo(f"  {'STATUS':<5} {'SOURCE':<24} {'FETCH':>5} {'NEW':>5}  METHOD")
    for s in report_sources:
        status = "OK" if s["ok"] else "FAIL"
        method = f"[{s['method']}]" if s.get("method") else ""
        click.echo(
            f"  {status:<5} {s['source']:<24} "
            f"{s['count']:>5} {s.get('new', 0):>5}  {method}"
        )

    if empty_sources:
        click.echo("")
        click.echo(f"gather halted: {len(empty_sources)} source(s) returned 0 URLs.")
        for e in empty_sources:
            if e["type"] == "html" and not e["saved"]:
                click.echo(
                    f"  - {e['source']} (html): fetch failed; download the landing "
                    f"page to {e['page_path']}, then resume."
                )
            elif e["type"] == "html":
                click.echo(
                    f"  - {e['source']} (html): fetched but extracted 0 links; "
                    f"replace {e['page_path']} with a good capture (or fix the "
                    f"source's include/exclude rules), then resume."
                )
            else:
                click.echo(
                    f"  - {e['source']} ({e['type']}): returned 0 "
                    f"({e['error'] or 'no links'}); fix upstream and resume."
                )
        click.echo(
            "Resume with: python -m lib.steps.pipeline "
            f"--resume {session_id} --cached-pages"
        )
        sys.exit(1)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(cli())
