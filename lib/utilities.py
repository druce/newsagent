"""Shared utility helpers: Gmail SMTP + rated-digest HTML rendering.

Ported from ~/projects/OpenAIAgentsSDK/utilities.py with light cleanup.
"""
from __future__ import annotations

import html as _html
import os
import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Iterable, Optional
from urllib.parse import urlparse, urlunparse


_SMTP_HOST = "smtp.gmail.com"
_SMTP_PORT = 587


def clean_url(url: str) -> str:
    """Strip query string, fragment, and ;params from a URL.

    Keeps scheme + netloc + path. Drops tracking params (utm_*, gclid),
    session ids, and #anchors that point at the same page. Non-strings
    and empty inputs return "".
    """
    if not isinstance(url, str) or not url:
        return ""
    parts = urlparse(url)
    return urlunparse((parts.scheme, parts.netloc, parts.path, "", "", ""))


def send_email(
    to_addresses: Iterable[str],
    subject: str,
    html_content: str,
    *,
    from_address: Optional[str] = None,
) -> None:
    """Send an HTML email via Gmail SMTP using STARTTLS.

    Reads GMAIL_USER (sender + login) and GMAIL_PASSWORD (Gmail app password)
    from the environment. Pass `from_address` to override the sender.
    """
    sender = from_address or os.environ.get("GMAIL_USER")
    password = os.environ.get("GMAIL_PASSWORD")
    if not sender:
        raise RuntimeError("GMAIL_USER not set in environment")
    if not password:
        raise RuntimeError("GMAIL_PASSWORD not set in environment")

    recipients = list(to_addresses)
    if not recipients:
        raise ValueError("send_email: at least one recipient required")

    msg = MIMEMultipart()
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = subject
    msg.attach(MIMEText(html_content, "html"))

    with smtplib.SMTP(_SMTP_HOST, _SMTP_PORT) as server:
        server.starttls()
        server.login(sender, password)
        server.sendmail(sender, recipients, msg.as_string())


def send_gmail(subject: str, html_str: str, *, to: Optional[str] = None) -> None:
    """Send an HTML email to a single recipient (defaults to GMAIL_USER).

    Convenience wrapper around send_email for the common single-recipient case.
    """
    sender = os.environ.get("GMAIL_USER")
    recipient = to or sender
    if not recipient:
        raise RuntimeError("No recipient: pass `to=` or set GMAIL_USER")
    send_email([recipient], subject, html_str)


# ── Rated-digest HTML rendering ──────────────────────────────────────────────


def _render_rated_item(h: dict) -> str:
    # Lazy import to keep utilities.py importable without the full app stack.
    from lib.sources import pretty_source

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


def render_rated_digest(session_id: str, rated: list[dict], timestamp: str) -> str:
    """Render a styled HTML digest of rated headlines, sorted by caller."""
    items_html = "\n".join(_render_rated_item(h) for h in rated)
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


def write_short_digest(
    session_id: str,
    headlines: list[dict],
    *,
    out_dir: str | Path = "out",
) -> tuple[Path, str]:
    """Render the rated digest, write to out/<date>_short.html, update latest_short.html.

    `headlines` must be filtered + sorted by the caller. Returns
    (dated_path, html_content).
    """
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    date_str = datetime.now().strftime("%Y-%m-%d")
    html_content = render_rated_digest(session_id, headlines, timestamp)

    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    dated = out_path / f"{date_str}_short.html"
    dated.write_text(html_content)

    latest = out_path / "latest_short.html"
    if latest.is_symlink() or latest.exists():
        latest.unlink()
    try:
        latest.symlink_to(dated.name)
    except OSError:
        # Filesystem without symlink support: write a copy.
        latest.write_text(html_content)

    return dated, html_content
