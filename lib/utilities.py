"""Shared utility helpers: Gmail SMTP + rated-digest HTML rendering.

Ported from ~/projects/OpenAIAgentsSDK/utilities.py with light cleanup.
"""
from __future__ import annotations

import html as _html
import os
import re
import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Iterable, Optional
from urllib.parse import quote, urlparse, urlunparse


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


# Strip a redundant trailing source-attribution clause — digest renderers
# already append the source name separately ("&mdash; source" in the rated
# digest, "- <em>source</em>" in the Bluesky digest), so an inline
# "..., Bloomberg reports" / "..., FT analysis says" / "..., per industry
# analysis" reads as duplicate credit. Conservative: only trailing clauses set
# off from the sentence (a comma, or a "per"/"according to" lead-in), and only
# with clear journalism verbs — so primary-source "..., Goldman says" and a
# sentence's own verb ("Nvidia reports record revenue") are left intact.
_ATTRIB_TAIL_RES = [
    # ", Bloomberg reports" / ", the Financial Times reported" / ", TechCrunch writes"
    # (capitalized publication + journalism verb as the final clause; NOT "says",
    # which is usually a primary source rather than the reporting outlet).
    re.compile(
        r",\s*(?:[Tt]he\s+)?[A-Z][A-Za-z0-9.&'’\- ]*?\s"
        r"(?:reports?|reported|writes?|wrote|notes?|noted|adds?|added)\.?\s*$"
    ),
    # ", per industry analysis." / ", according to Reuters"
    re.compile(r",\s*(?:per|according to)\s+[A-Za-z0-9.&'’\- ]{1,45}?\.?\s*$", re.I),
    # ", FT analysis says" / ", a new study finds" / ", the report found"
    re.compile(
        r",\s*(?:the\s+|an?\s+)?[A-Za-z0-9.&'’\- ]*?\b(?:analysis|report|study|survey|poll)\b\s+"
        r"(?:says?|said|finds?|found|shows?|showed|suggests?|reveals?|reported|estimates?)\.?\s*$",
        re.I,
    ),
]


def strip_source_attribution(text: str) -> str:
    """Remove a trailing, redundant source-attribution clause from headline text.

    The digest already shows the source next to each headline, so an inline
    "..., Bloomberg reports" is duplicate credit. Only strips a clause that is
    clearly a media attribution set off at the very end; leaves the text
    untouched (and never returns an empty/near-empty string) otherwise.
    """
    for rx in _ATTRIB_TAIL_RES:
        m = rx.search(text)
        if m and m.start() > 0:
            stripped = text[: m.start()].rstrip(" ,;—-").strip()
            if len(stripped) >= 10:
                return stripped
    return text


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


# ── Per-row action links (copy headline + queue-to-Bluesky) ──────────────────


def _js_attr(text: str) -> str:
    """Escape `text` for use as a JS double-quoted string inside an HTML attr.

    First escape backslashes/quotes for the JS string literal, then HTML-escape
    so it is safe inside the double-quoted `onclick="..."` attribute. The
    browser HTML-unescapes before the JS engine sees it, so `\\&quot;` becomes a
    valid `\\"` escaped quote and the `&quot;` delimiters become `"`.
    """
    js = text.replace("\\", "\\\\").replace('"', '\\"')
    js = " ".join(js.split())  # collapse newlines/runs of whitespace
    return _html.escape(js, quote=True)


def render_row_action_links(
    headline: str, url: Optional[str], *, queue_port: Optional[int] = None
) -> str:
    """Return the two per-row action glyphs: copy-headline (⎘) + queue-to-Bluesky (🦋).

    Shared by the full newsletter (`send.py`) and the rated short digest so both
    render the identical links. `headline` is raw text; `url` is the primary
    article URL — the 🦋 enqueue link is omitted when it is falsy. The enqueue
    link targets the local `bsky_share` daemon on `queue_port` (defaults to
    $BSKY_QUEUE_PORT or 8765).
    """
    if queue_port is None:
        queue_port = int(os.environ.get("BSKY_QUEUE_PORT", "8765"))

    copy_js = _js_attr(headline)
    copy = (
        '<a href="#" onclick="navigator.clipboard.writeText(&quot;'
        f'{copy_js}&quot;); return false;" '
        'style="text-decoration:none; color:#aaaaaa; margin-left:6px; font-size:12px;" '
        'title="Copy headline">&#x2398;</a>'
    )

    # Fires a fire-and-forget fetch at the local daemon and flips the glyph to a
    # check in place; falls back to opening the /enqueue URL if scripting is off.
    share = ""
    if url:
        share_url = (
            f"http://localhost:{queue_port}/enqueue"
            f"?u={quote(url, safe='')}&t={quote(headline, safe='')}"
        )
        share = (
            f'<a href="{_html.escape(share_url, quote=True)}" target="_blank" '
            'onclick="fetch(this.href,{mode:&#39;no-cors&#39;});'
            "this.textContent=&#39;✓&#39;;return false;\" "
            'style="text-decoration:none; color:#aaaaaa; margin-left:6px; font-size:12px;" '
            'title="Queue to Bluesky">\U0001f98b</a>'
        )

    return f"{copy}{share}"


# ── Rated-digest HTML rendering ──────────────────────────────────────────────


def _strip_no_content(value: Optional[str]) -> str:
    """Drop the summarize step's 'no content' sentinel.

    When an article body cannot be extracted, the summarize prompt emits
    short_summary "no content" and summary "- no content". Those are not real
    content, so callers should treat them as empty (e.g. to fall back to the
    scraped title) rather than display them.
    """
    if not value:
        return ""
    if value.strip().lower() in ("no content", "- no content"):
        return ""
    return value


def _render_rated_item(h: dict) -> str:
    # Lazy import to keep utilities.py importable without the full app stack.
    from lib.sources import pretty_source

    rating = float(h.get("rating", 0.0))
    # Prefer the distilled one-liner; fall back to scraped title (which can
    # be garbage like image-caption text on some sites). The 'no content'
    # sentinel is treated as empty so it doesn't shadow a real title.
    headline = _html.escape(
        _strip_no_content(h.get("short_summary")) or h.get("title") or "(no title)"
    )
    final_url = h.get("final_url") or h.get("url") or ""
    url = _html.escape(final_url or "#")
    # Prefer the stored site_name (set at download time); fall back to a
    # live pretty_source() lookup for old sessions that pre-date that field.
    source = _html.escape(
        h.get("site_name") or pretty_source(final_url, h.get("source"))
    )
    summary_raw = _strip_no_content(h.get("summary")).strip()
    summary_html = _html.escape(summary_raw).replace("\n", "<br>")

    # Same copy-headline + queue-to-Bluesky links as the full newsletter, at the
    # end of the row. `headline` is already HTML-escaped; pass the raw one-liner
    # so the copy/enqueue payloads aren't double-escaped.
    raw_headline = _strip_no_content(h.get("short_summary")) or h.get("title") or "(no title)"
    actions = render_row_action_links(raw_headline, final_url or None)

    return (
        '<div style="margin-bottom:20px;padding:10px;'
        'border-left:3px solid #4CAF50;">\n'
        f'  <h3 style="margin:0 0 5px 0;font-size:15px;">'
        f'{rating:.2f} &mdash; '
        f'<a href="{url}" style="color:#0366d6;text-decoration:none;">{headline}</a>'
        f' &mdash; <span style="color:#666;">{source}</span></h3>\n'
        f'  <p style="margin:5px 0 0 0;color:#444;font-size:13px;'
        f'line-height:1.5;">{summary_html}{actions}</p>\n'
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
