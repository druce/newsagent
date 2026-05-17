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
