"""newsagent:rate — additive multi-axis composite (legacy do_rating.py:511 parity).

Composite:
    rating = c_reputation   * reputation
           + c_adjusted_len * adjusted_len
           + c_on_topic     * on_topic
           + c_importance   * importance
           - c_quality_low  * quality_low
           + c_bt_z         * bt_z
           + c_recency      * recency_score

Terms:
  reputation     — sites.reputation lookup by URL domain (default 0.0)
  adjusted_len   — clip(log10(content_length) - 3, 0, 2); content_length from text_path
  on_topic/importance/quality_low — LLM 0..1 confidences
  bt_z           — z-scored Bradley-Terry score
  recency_score  — 2*exp(-ln2 * age_days) - 1   (≈ +1 today, 0 at 1d, -0.5 at 2d, -1 floor)

Published-date fallback chain (resolved at rate-time, best→worst):
  1. h["published"] parsed via parsedate_to_datetime or fromisoformat
  2. trafilatura.extract_metadata(html_path).date
  3. regex `/(20\\d\\d)[/-](\\d\\d)[/-](\\d\\d)/` on the URL
  4. now - 1 day (legacy sentinel)

File mtime is intentionally NOT a fallback: it's when we fetched the article,
not when it was published. Using mtime would set age≈0 for any article we
downloaded today, even one published years ago, and inflate the recency term.

Articles older than MAX_ARTICLE_AGE_DAYS are dropped before rating.
"""
from __future__ import annotations

import json
import math
import re
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import urlparse

import click
import numpy as np
import trafilatura
from dotenv import load_dotenv

import lib.prompts  # noqa: F401 — register all prompts including rating prompts
from lib.config import MAX_ARTICLE_AGE_DAYS, RATING_COEFFS
from lib.db import Site
from lib.llm import call_prompt
from lib.rating import bradley_terry_scores
from lib.state import NewsletterAgentState
from lib.utilities import send_gmail, write_short_digest

_BATCH = 25
_URL_DATE_RE = re.compile(r"/(20\d\d)[/-](\d{1,2})[/-](\d{1,2})(?:[/-]|$)")
_RECENCY_K = math.log(2)  # half-life: 1 day


# ───────────────────────── helpers ─────────────────────────

def _collect_confidences(
    items: List[dict],
    prompt_name: str,
    engine: str | None,
) -> Dict[str, float]:
    """Call the given rating prompt in batches; return {id: confidence}."""
    result: Dict[str, float] = {}
    n_batches = math.ceil(len(items) / _BATCH)
    for batch_idx, batch_start in enumerate(range(0, len(items), _BATCH), start=1):
        batch = items[batch_start:batch_start + _BATCH]
        click.echo(f"  {prompt_name}: batch {batch_idx}/{n_batches} ({len(batch)} items)")
        out = call_prompt(prompt_name, {"items": batch}, engine=engine)
        for sc in out.results_list:
            result[sc.id] = sc.confidence
    return result


def _z_score(scores: Dict[str, float]) -> Dict[str, float]:
    values = list(scores.values())
    if not values:
        return {}
    mean = float(np.mean(values))
    std = float(np.std(values))
    if std == 0:
        return {k: 0.0 for k in scores}
    return {k: (v - mean) / std for k, v in scores.items()}


def _parse_date_string(raw: str) -> Optional[datetime]:
    """Try RFC 2822 then ISO. Returns tz-aware UTC datetime, or None."""
    if not raw or not isinstance(raw, str):
        return None
    for parser in (parsedate_to_datetime, datetime.fromisoformat):
        try:
            dt = parser(raw)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except (TypeError, ValueError):
            continue
    return None


def _date_from_trafilatura(html_path: Optional[str]) -> Optional[datetime]:
    if not html_path:
        return None
    p = Path(html_path)
    if not p.is_file():
        return None
    try:
        html = p.read_text(errors="ignore")
        meta = trafilatura.extract_metadata(html)
        if meta is None:
            return None
        raw = meta.date  # ISO YYYY-MM-DD per trafilatura/htmldate
    except Exception:  # noqa: BLE001
        return None
    return _parse_date_string(raw) if raw else None


def _date_from_url(url: Optional[str]) -> Optional[datetime]:
    if not url:
        return None
    m = _URL_DATE_RE.search(url)
    if not m:
        return None
    try:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return datetime(y, mo, d, tzinfo=timezone.utc)
    except ValueError:
        return None


def _resolve_published(h: dict, now: datetime) -> tuple[datetime, str]:
    """Return (published_dt, source_tag) using the fallback chain.

    File mtime is intentionally excluded: it's when we fetched, not when the
    article was published, and would under-estimate age for old articles.

    Side effect: writes the resolved ISO string back to `h["published"]` so
    subsequent steps (and re-runs) see the enriched value.
    """
    # 1. Existing RSS/feed date
    dt = _parse_date_string(h.get("published") or "")
    if dt is not None:
        return dt, "feed"
    # 2. Trafilatura metadata from cached HTML
    dt = _date_from_trafilatura(h.get("html_path"))
    if dt is not None:
        h["published"] = dt.isoformat()
        return dt, "trafilatura"
    # 3. URL date pattern
    dt = _date_from_url(h.get("final_url") or h.get("url"))
    if dt is not None:
        h["published"] = dt.isoformat()
        return dt, "url"
    # 4. Sentinel: 1 day ago
    fallback = now - timedelta(days=1)
    h["published"] = fallback.isoformat()
    return fallback, "sentinel"


def _content_length(text_path: Optional[str]) -> int:
    """Byte size of the cached extracted text file (0 if missing)."""
    if not text_path:
        return 0
    p = Path(text_path)
    if not p.is_file():
        return 0
    try:
        return p.stat().st_size
    except OSError:
        return 0


def _adjusted_len(content_length: int) -> float:
    """Legacy do_rating.py:506-509 — clip(log10(max(len,1)) - 3, 0, 2)."""
    n = max(content_length, 1)
    return float(np.clip(np.log10(n) - 3, 0.0, 2.0))


def _recency_score(age_days: float) -> float:
    """Legacy do_rating.py:429 — 2*exp(-ln2 * age_days) - 1, clipped at -1."""
    age = max(age_days, 0.0)
    return float(2.0 * math.exp(-_RECENCY_K * age) - 1.0)


def _lookup_reputation(db_path: str, url: str) -> float:
    """Reputation for the URL's domain from the sites table. 0.0 if unknown."""
    if not url:
        return 0.0
    domain = urlparse(url).netloc.lower()
    if domain.startswith("www."):
        domain = domain[4:]
    if not domain:
        return 0.0
    try:
        with sqlite3.connect(db_path) as conn:
            site = Site.get_by_domain(conn, domain)
    except sqlite3.Error:
        return 0.0
    return float(site.reputation) if site else 0.0


# ───────────────────────── CLI ─────────────────────────

@click.command()
@click.option("--db", "db_path", default="newsletter_agent.db")
@click.option("--session", "session_id", required=True)
@click.option("--engine", default=None, help="Override engine for rating prompts")
@click.option("--to", "email_to", default=None,
              help="Email recipient for the rated digest (defaults to GMAIL_USER).")
@click.option("--no-email", "no_email", is_flag=True,
              help="Write out/<date>_short.html but skip sending email.")
def cli(
    db_path: str,
    session_id: str,
    engine: str | None,
    email_to: str | None,
    no_email: bool,
) -> None:
    load_dotenv()
    state = NewsletterAgentState(session_id=session_id, db_path=db_path).load_latest_from_db()
    if state is None:
        raise click.ClickException(f"No state found for session {session_id}")

    state.start_step("rate")
    state.save_checkpoint("rate")

    now = datetime.now(tz=timezone.utc)

    # ── Resolve published date for every headline that has a summary, then
    #    drop anything older than MAX_ARTICLE_AGE_DAYS (legacy parity).
    rated_indices: List[int] = []
    items: List[dict] = []
    drop_reasons: Dict[int, str] = {}
    date_source_counts: Dict[str, int] = {"feed": 0, "trafilatura": 0, "url": 0, "sentinel": 0}

    for i, h in enumerate(state.headline_data):
        if not h.get("summary"):
            continue
        dt, src = _resolve_published(h, now)
        date_source_counts[src] = date_source_counts.get(src, 0) + 1
        age_days = (now - dt).total_seconds() / 86400.0
        h["age_days"] = age_days
        h["date_source"] = src
        if age_days >= MAX_ARTICLE_AGE_DAYS:
            drop_reasons[i] = f"age {age_days:.1f}d >= {MAX_ARTICLE_AGE_DAYS}"
            continue
        input_text = f"{h.get('title', '')}\n{h['summary']}"
        items.append({"id": str(i), "input_text": input_text})
        rated_indices.append(i)

    if drop_reasons:
        click.echo(f"Dropped {len(drop_reasons)} article(s) older than {MAX_ARTICLE_AGE_DAYS} days.")
    click.echo(
        f"Date sources: " + ", ".join(f"{k}={v}" for k, v in date_source_counts.items() if v)
    )

    if not items:
        state.complete_step("rate", message="nothing to rate")
        state.save_checkpoint("rate")
        click.echo("Nothing to rate.")
        return

    click.echo(f"Rating {len(items)} articles across 3 axes + Bradley-Terry battles.")

    # --- Per-axis confidence scores ---
    click.echo("[1/4] rate_quality")
    quality_dict = _collect_confidences(items, "rate_quality", engine)
    click.echo("[2/4] rate_on_topic")
    on_topic_dict = _collect_confidences(items, "rate_on_topic", engine)
    click.echo("[3/4] rate_importance")
    importance_dict = _collect_confidences(items, "rate_importance", engine)

    # --- Bradley-Terry ---
    click.echo("[4/4] Bradley-Terry Swiss-paired battles")
    bt_items = [
        {
            "id": str(i),
            "title": state.headline_data[i].get("title", ""),
            "summary": state.headline_data[i]["summary"],
        }
        for i in rated_indices
    ]

    def _bt_progress(round_done: int, total_rounds: int, n_battles: int) -> None:
        click.echo(f"  BT round {round_done}/{total_rounds} ({n_battles} battles)")

    raw_bt = bradley_terry_scores(bt_items, progress_callback=_bt_progress)
    bt_z = _z_score(raw_bt)

    click.echo("Computing composite ratings (additive)...")

    c = RATING_COEFFS
    for idx in rated_indices:
        h = state.headline_data[idx]
        key = str(idx)

        quality_low = quality_dict.get(key, 0.5)
        on_topic = on_topic_dict.get(key, 0.5)
        importance = importance_dict.get(key, 0.5)
        btz = bt_z.get(key, 0.0)

        content_len = _content_length(h.get("text_path"))
        adjusted_len = _adjusted_len(content_len)
        reputation = _lookup_reputation(db_path, h.get("final_url") or h.get("url", ""))
        recency = _recency_score(h.get("age_days", 1.0))

        rating = (
            c["reputation"]   * reputation
            + c["adjusted_len"] * adjusted_len
            + c["on_topic"]     * on_topic
            + c["importance"]   * importance
            - c["quality_low"]  * quality_low
            + c["bt_z"]         * btz
            + c["recency"]      * recency
        )

        h["quality_low"] = quality_low
        h["on_topic"] = on_topic
        h["importance"] = importance
        h["bt_z"] = btz
        h["bt_score"] = raw_bt.get(key, 0.0)
        h["reputation"] = reputation
        h["content_length"] = content_len
        h["adjusted_len"] = adjusted_len
        h["recency_score"] = recency
        h["rating"] = rating

    state.complete_step("rate", message=f"{len(rated_indices)} articles rated")
    state.save_checkpoint("rate")

    ratings = [state.headline_data[i]["rating"] for i in rated_indices]
    runs_dir = Path("runs") / session_id
    runs_dir.mkdir(parents=True, exist_ok=True)
    (runs_dir / "rate.json").write_text(json.dumps({
        "session_id": session_id,
        "completed_at": datetime.now().isoformat(),
        "total_rated": len(rated_indices),
        "dropped_too_old": len(drop_reasons),
        "date_source_counts": date_source_counts,
        "rating_distribution": {
            "min": float(min(ratings)),
            "max": float(max(ratings)),
            "mean": float(np.mean(ratings)),
        },
    }, indent=2))

    click.echo(f"Rated: {len(rated_indices)} articles. "
               f"Rating range: [{min(ratings):.3f}, {max(ratings):.3f}], "
               f"mean {float(np.mean(ratings)):.3f}.")

    # --- Render + (optionally) email the rated digest ---
    sorted_rated = sorted(
        [state.headline_data[i] for i in rated_indices],
        key=lambda h: float(h.get("rating", 0.0)),
        reverse=True,
    )
    dated_path, html_content = write_short_digest(session_id, sorted_rated)
    click.echo(f"Wrote rated digest: {dated_path} (and out/latest_short.html)")

    if no_email:
        return
    subject = f"AI news items - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    try:
        send_gmail(subject, html_content, to=email_to)
        click.echo(f"Emailed digest to {email_to or '<GMAIL_USER>'}.")
    except RuntimeError as e:
        click.echo(f"Skipped email ({e}). Pass --to or set GMAIL_USER/GMAIL_PASSWORD.",
                   err=True)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(cli())
