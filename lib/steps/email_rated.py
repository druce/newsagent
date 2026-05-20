"""newsagent:email-rated — email the rated headlines for a session.

Mirrors the post-rate email behavior of the legacy
~/projects/OpenAIAgentsSDK/news_agent.py:2063-2089 ("AI news items" email).
Sorts by rating descending, renders each headline as a styled HTML block,
and SMTPs it via lib.utilities.send_gmail.
"""
from __future__ import annotations

import html as _html
import sys
from datetime import datetime
from typing import Optional

import click
from dotenv import load_dotenv

from lib.sources import pretty_source
from lib.state import NewsletterAgentState
from lib.utilities import send_gmail


def _render_item(h: dict) -> str:
    rating = float(h.get("rating", 0.0))
    # Prefer the distilled one-liner; fall back to scraped title (which can
    # be garbage like image-caption text on some sites).
    headline = _html.escape(h.get("short_summary") or h.get("title") or "(no title)")
    final_url = h.get("final_url") or h.get("url") or ""
    url = _html.escape(final_url or "#")
    # Prefer the stored site_name (set at download time); fall back to a
    # live pretty_source() lookup for old sessions that pre-date that field.
    source = _html.escape(
        h.get("site_name") or pretty_source(final_url, h.get("source"))
    )
    summary_raw = (h.get("summary") or "").strip()
    # Preserve bullet line breaks from the extract_summaries prompt output.
    summary_html = _html.escape(summary_raw).replace("\n", "<br>")

    return (
        '<div style="margin-bottom:20px;padding:10px;'
        'border-left:3px solid #4CAF50;">\n'
        f'  <h3 style="margin:0 0 5px 0;font-size:15px;">'
        f'{rating:.2f} &mdash; '
        f'<a href="{url}" style="color:#0366d6;text-decoration:none;">{headline}</a>'
        f' &mdash; <span style="color:#666;">{source}</span></h3>\n'
        f'  <p style="margin:5px 0 0 0;color:#444;font-size:13px;'
        f'line-height:1.5;">{summary_html}</p>\n'
        "</div>"
    )


def _render_html(session_id: str, rated: list[dict], timestamp: str) -> str:
    items_html = "\n".join(_render_item(h) for h in rated)
    return (
        "<!DOCTYPE html>\n<html><head><meta charset=\"utf-8\">"
        f"<title>AI news items - {_html.escape(timestamp)}</title>"
        "<style>"
        "body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;"
        "max-width:900px;margin:20px auto;padding:0 16px;color:#222;}"
        "h1{font-size:18px;margin-bottom:12px;}"
        "</style></head><body>"
        f"<h1>AI news items &mdash; {_html.escape(timestamp)} "
        f"(session {_html.escape(session_id)}, {len(rated)} articles)</h1>\n"
        + items_html
        + "</body></html>"
    )


@click.command()
@click.option("--db", "db_path", default="newsletter_agent.db")
@click.option("--session", "session_id", required=True)
@click.option("--to", "to_address", default=None,
              help="Recipient (defaults to GMAIL_USER).")
@click.option("--min-rating", type=float, default=None,
              help="Drop headlines with rating below this threshold.")
@click.option("--limit", type=int, default=None,
              help="Send only the top N by rating.")
@click.option("--dry-run", is_flag=True,
              help="Render HTML and print preview to stdout; do not send.")
def cli(
    db_path: str,
    session_id: str,
    to_address: Optional[str],
    min_rating: Optional[float],
    limit: Optional[int],
    dry_run: bool,
) -> None:
    load_dotenv()

    state = NewsletterAgentState(
        session_id=session_id, db_path=db_path
    ).load_latest_from_db()
    if state is None:
        raise click.ClickException(f"No state found for session {session_id}")

    rated = [h for h in state.headline_data if "rating" in h]
    if min_rating is not None:
        rated = [h for h in rated if float(h.get("rating", 0)) >= min_rating]
    rated.sort(key=lambda h: float(h.get("rating", 0)), reverse=True)
    if limit is not None:
        rated = rated[:limit]

    if not rated:
        raise click.ClickException("No rated headlines to email.")

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    html_content = _render_html(session_id, rated, timestamp)
    subject = f"AI news items - {timestamp}"

    if dry_run:
        click.echo(f"Subject: {subject}")
        click.echo(f"Recipients: {to_address or '<GMAIL_USER>'}")
        click.echo(f"Items: {len(rated)}")
        click.echo("---")
        click.echo(html_content[:2000])
        click.echo("..." if len(html_content) > 2000 else "")
        return

    send_gmail(subject, html_content, to=to_address)
    click.echo(f"Sent {len(rated)} rated articles to {to_address or '<GMAIL_USER>'}.")


if __name__ == "__main__":  # pragma: no cover
    sys.exit(cli())
