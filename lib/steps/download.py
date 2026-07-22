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
import lib.prompts  # noqa: F401 — register SITENAME

from lib.fetch.brightdata import scrape_urls_brightdata
from lib.fetch.canonical import extract_canonical_url
from lib.fetch.extract import extract_article_text
from lib.fetch.playwright_runner import fetch_urls_html_batch
from lib.llm import call_prompt, get_prompt
from lib.prompts.sitename import SitenameOutput
from lib.sources import (
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
_MIN_TEXT_CHARS = 400
_SITENAME_BATCHES_SUBDIR = "sitename-batches"
_SITENAME_RESULTS_SUBDIR = "sitename-results"
_SITENAME_BATCH_SIZE = 25


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
    return extract_article_text(html)


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
) -> None:
    """Update urls.final_url for successful fetches.

    Does NOT touch sites.scrape_method — that column is user-curated config
    (a hard pin), not a heuristic cache. Operators decide which domains need
    Playwright by setting it manually; the runtime adapts via the
    httpx → Playwright → BD fallback chain regardless.
    """
    with sqlite3.connect(db_path) as conn:
        for initial_url, final_url, _ in successes:
            conn.execute(
                "UPDATE urls SET final_url=? WHERE initial_url=?",
                (final_url, initial_url),
            )
        conn.commit()


def _unknown_domains(state: NewsletterAgentState, db_path: str) -> list[str]:
    """Sorted bare domains referenced by headlines but missing a sites.name.

    Subdomain stripping counts as known: if 'finance.yahoo.com' resolves via
    'yahoo.com', it is not unknown.
    """
    candidate_domains: set[str] = set()
    for h in state.headline_data:
        url = h.get("final_url") or h.get("url")
        d = _bare_domain(url)
        if d:
            candidate_domains.add(d)
    with sqlite3.connect(db_path) as conn:
        known = {
            row[0]
            for row in conn.execute(
                "SELECT domain FROM sites WHERE name IS NOT NULL AND name != ''"
            )
        }
    return sorted(
        d for d in candidate_domains
        if not any(c in known for c in _candidate_domains(d))
    )


def _upsert_site_names(records, db_path: str) -> int:
    """Upsert (domain, site_name) records into sites; clear the per-process
    cache so pretty_source sees the new names. Returns rows upserted."""
    n = 0
    with sqlite3.connect(db_path) as conn:
        for rec in records:
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
            n += 1
        conn.commit()
    reset_db_cache()
    return n


def _assign_site_names(state: NewsletterAgentState, db_path: str) -> int:
    """Set h['site_name'] on every headline via pretty_source's fallback
    chain (sites.name → gather-time source label → bare domain → 'Unknown')."""
    n = 0
    for h in state.headline_data:
        url = h.get("final_url") or h.get("url")
        h["site_name"] = pretty_source(url, h.get("source"), db_path=db_path)
        n += 1
    return n


def _write_sitename_batches(session_id: str, domains: list[str]) -> list[Path]:
    """Write self-contained sitename batch prompt files (filter pattern).
    ids are stringified positions in the sorted `domains` list."""
    from lib.prompts.sitename import SitenameInput, SitenameOutput

    cfg = get_prompt("sitename")
    batches_dir = Path("runs") / session_id / _SITENAME_BATCHES_SUBDIR
    if batches_dir.exists():
        for p in batches_dir.glob("batch-*.json"):
            p.unlink()
    batches_dir.mkdir(parents=True, exist_ok=True)

    paths: list[Path] = []
    for batch_idx, start in enumerate(range(0, len(domains), _SITENAME_BATCH_SIZE)):
        chunk = domains[start:start + _SITENAME_BATCH_SIZE]
        items = [{"id": str(start + j), "domain": d} for j, d in enumerate(chunk)]
        validated = SitenameInput.model_validate({"items": items})
        payload = {
            "batch_id": batch_idx,
            "session_id": session_id,
            "ids": [it["id"] for it in items],
            "items": items,
            "system_prompt": cfg.system_prompt,
            "user_prompt": cfg.user_prompt.format(**validated.model_dump()),
            "output_schema": SitenameOutput.model_json_schema(),
        }
        path = batches_dir / f"batch-{batch_idx:03d}.json"
        path.write_text(json.dumps(payload, indent=2))
        paths.append(path.resolve())
    return paths


def _load_sitename_results(
    results_dir: Path, expected_ids: set[str]
) -> tuple[list, list[str]]:
    """Load Haiku result JSONs. Returns (records, problems) — records are
    SitenameRecord objects; problems are human-readable strings (filter's
    _load_results pattern)."""
    from pydantic import ValidationError
    from lib.prompts.sitename import SitenameOutput

    records: list = []
    problems: list[str] = []
    files = sorted(results_dir.glob("batch-*.json")) if results_dir.exists() else []
    if not files:
        problems.append(f"no batch-*.json files in {results_dir}")
        return records, problems

    seen_ids: set[str] = set()
    for f in files:
        try:
            raw = json.loads(f.read_text())
        except json.JSONDecodeError as exc:
            problems.append(f"{f.name}: invalid JSON ({exc})")
            continue
        try:
            parsed = SitenameOutput.model_validate(raw)
        except ValidationError as exc:
            problems.append(f"{f.name}: schema mismatch ({exc.error_count()} errors)")
            continue
        for rec in parsed.results:
            if rec.id in seen_ids:
                problems.append(f"{f.name}: duplicate id {rec.id!r}")
                continue
            seen_ids.add(rec.id)
            records.append(rec)

    missing = expected_ids - seen_ids
    extra = seen_ids - expected_ids
    if missing:
        problems.append(f"missing sitenames for ids: {sorted(missing)[:10]}")
    if extra:
        problems.append(f"unexpected ids in results: {sorted(extra)[:10]}")
    return records, problems


@click.command()
@click.option("--db", "db_path", default="newsletter_agent.db")
@click.option("--session", "session_id", required=True)
@click.option("--max", "max_urls", type=int, default=None,
              help="Cap on number of URLs downloaded this run.")
@click.option("--parallel", type=int, default=8, show_default=True,
              help="Concurrent fetches per phase.")
@click.option("--apply-sitenames", "apply_sitenames", default=None,
              help="Read sitename result JSONs from this dir, upsert into "
                   "sites, and refresh headline site_names (no downloading)")
@click.option("--engine", default=None,
              help="Classic mode: resolve unknown site names in-process via "
                   "this engine (cron/CI only; e.g. google:gemini-3.1-flash-lite). "
                   "Default: write sitename batches for Haiku subagent dispatch.")
def cli(db_path: str, session_id: str, max_urls: int | None, parallel: int,
        apply_sitenames: str | None, engine: str | None) -> None:
    state = NewsletterAgentState(session_id=session_id, db_path=db_path).load_latest_from_db()
    if state is None:
        raise click.ClickException(f"No state found for session {session_id}")

    if apply_sitenames:
        batches_dir = Path("runs") / session_id / _SITENAME_BATCHES_SUBDIR
        expected_ids: set[str] = set()
        if batches_dir.exists():
            for bf in sorted(batches_dir.glob("batch-*.json")):
                expected_ids.update(str(i) for i in json.loads(bf.read_text()).get("ids", []))
        if not expected_ids:
            click.echo("No sitename batches found; nothing to apply.")
            return
        records, problems = _load_sitename_results(Path(apply_sitenames), expected_ids)
        if problems:
            click.echo("Apply found problems:", err=True)
            for p in problems:
                click.echo(f"  - {p}", err=True)
            if not records:
                raise click.ClickException(
                    "no usable sitename results; redispatch failed batches")
        n = _upsert_site_names(records, db_path)
        _assign_site_names(state, db_path)
        state.save_checkpoint("download")
        runs_dir = Path("runs") / session_id
        (runs_dir / "sitename.json").write_text(json.dumps({
            "session_id": session_id,
            "completed_at": datetime.now().isoformat(),
            "unknown": len(expected_ids),
            "resolved": n,
        }, indent=2))
        click.echo(f"Applied {n} site name(s); headline site_names refreshed.")
        return

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

    def _refine_with_canonical(post_redirect_url: str, html: str | None) -> str:
        """Return the same-domain canonical if HTML carries one, else the
        post-redirect URL. Pure: no DB or network I/O."""
        if not html:
            return post_redirect_url
        canonical = extract_canonical_url(html, post_redirect_url)
        return canonical or post_redirect_url

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
        click.echo(f"Phase 0 (bright_data): {len(bd_urls)} URLs (async, concurrency 8)")
        bd_results = scrape_urls_brightdata(bd_urls)
        for url, (text, html, final_url, err) in bd_results.items():
            if text is None or html is None:
                bd_failed_initial.append(url)
                continue
            final = _refine_with_canonical(final_url or url, html)
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
                final = _refine_with_canonical(final_url or url, html)
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
            final = _refine_with_canonical(final_url or url, html)
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
            f"Phase 3 (bright_data fallback): {len(bd_retry_urls)} URLs (async, concurrency 8)"
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
            final = _refine_with_canonical(final_url or url, html)
            _persist(url, text, html, final)
            domain_method.setdefault(_domain_of(final) or "", "bright_data")

    _commit_results(db_path, successes)

    # Surface httpx→Playwright fallback domains so the operator can pin them
    # in sites.scrape_method='playwright' if it's a persistent pattern.
    pw_fallback_domains: set[str] = set()
    for url in http_failed:
        d = _domain_of(url)
        if d and domain_method.get(d) == "playwright":
            pw_fallback_domains.add(d)
    if pw_fallback_domains:
        click.echo(
            "Note: httpx failed → Playwright recovered for these domains; "
            "consider pinning them with "
            "UPDATE sites SET scrape_method='playwright' WHERE domain IN (...):"
        )
        for d in sorted(pw_fallback_domains):
            click.echo(f"  - {d}")

    # Resolve h['site_name']. Unknown domains become sitename batch files for
    # Haiku subagent dispatch by the parent session (no in-process LLM call);
    # until --apply-sitenames runs, pretty_source falls back to source label /
    # bare domain.
    unknown = _unknown_domains(state, db_path)
    if unknown and engine:
        items = [{"id": str(i), "domain": d} for i, d in enumerate(unknown)]
        try:
            result = call_prompt("sitename", {"items": items}, engine=engine)
            assert isinstance(result, SitenameOutput)
            n = _upsert_site_names(result.results, db_path)
            click.echo(f"sitename resolved {n} new domain(s) via {engine}.")
        except Exception as exc:
            click.echo(
                f"sitename LLM call failed ({exc}); leaving {len(unknown)} "
                f"domain(s) as bare-domain fallback.", err=True)
    elif unknown:
        batch_paths = _write_sitename_batches(session_id, unknown)
        click.echo(
            f"Prepared {len(batch_paths)} sitename batch(es) "
            f"({len(unknown)} unknown domains)."
        )
        for p in batch_paths:
            click.echo(f"  {p}")
        click.echo("Dispatch one Haiku subagent per batch. Write each result to:")
        click.echo(f"  runs/{session_id}/{_SITENAME_RESULTS_SUBDIR}/batch-NNN.json")
        click.echo(
            f"Then run: python -m lib.steps.download --session {session_id} "
            f"--apply-sitenames runs/{session_id}/{_SITENAME_RESULTS_SUBDIR}"
        )
    _assign_site_names(state, db_path)

    # Always surface which URLs failed to download (after all fallbacks),
    # so the operator sees them on the console without grepping download.json.
    if failures:
        click.echo(f"Failed to download {len(failures)} URL(s):", err=True)
        for f in failures:
            click.echo(f"  - [{f['phase']}] {f['url']}: {f['error']}", err=True)

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
