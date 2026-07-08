"""newsagent:send — render newsletter as HTML, write to out/, email via Gmail."""
from __future__ import annotations

import html as _html
import os
import re
import sqlite3
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import click
import markdown as md
from dotenv import load_dotenv

from lib.db import PublishedArticle
from lib.embeddings import embed_texts
from lib.state import NewsletterAgentState
from lib.utilities import render_row_action_links, send_gmail


_OUT_DIR = Path("out")
_LEADING_H1_RE = re.compile(r"\A(?:\s*#\s+[^\n]*\n+)+", re.MULTILINE)

# --- newsletter HTML template constants -----------------------------------
_FONT = "'Open Sans', 'Helvetica Neue', Helvetica, Arial, sans-serif"
_BRAND = "Skynet&amp;Chill"
_BRAND_URL = "https://skynetandchill.com"
_SITE = "skynetandchill.com"
_TAGLINE = "AI News Digest"
_ACCENT = "#111155"        # header bar + section titles
_RULE_BAR = "#d2691e"      # headline left border
_CARD_BG = "#f6f6fc"       # headline background

# Port of the local share-to-Bluesky daemon (lib.steps.bsky_share). Must match
# the daemon's default so the rendered enqueue links resolve at runtime.
_QUEUE_PORT = int(os.environ.get("BSKY_QUEUE_PORT", "8765"))

# --- markdown parsing -------------------------------------------------------
_SECTION_RE = re.compile(r"^\s*##\s+(.*\S)\s*$")
_BULLET_RE = re.compile(r"^\s*[-*]\s+(.*\S)\s*$")
_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
# Trailing run of `[label](url)` source links, introduced by an em/en dash.
_SOURCES_RE = re.compile(r"\s+[—–]\s+((?:\[[^\]]+\]\([^)]+\)\s*,?\s*)+)$")


def _markdown_to_html(body_md: str) -> str:
    """Convert a markdown body into HTML (fallback rendering path).

    Strips any leading top-level `# Title` lines, then runs python-markdown
    with the extra/sane_lists/smarty extensions so [link](url), `**bold**`,
    and `- bullet` lists render as real HTML elements.
    """
    body = _LEADING_H1_RE.sub("", body_md or "")
    return md.markdown(
        body,
        extensions=["extra", "sane_lists", "smarty"],
        output_format="html",
    )


def _parse_bullet(text: str) -> Optional[dict]:
    """Split a bullet into its headline and trailing `[source](url)` links."""
    headline = text
    sources: list[tuple[str, str]] = []
    m = _SOURCES_RE.search(text)
    if m:
        headline = text[: m.start()].strip()
        for lm in _LINK_RE.finditer(m.group(1)):
            sources.append((lm.group(1).strip(), lm.group(2).strip()))
    headline = headline.replace("**", "").strip()
    if not headline:
        return None
    return {"headline": headline, "sources": sources}


def _parse_sections(body_md: str) -> list[dict]:
    """Parse the rewrite markdown into [{title, items:[{headline, sources}]}]."""
    sections: list[dict] = []
    cur: Optional[dict] = None
    for raw in (body_md or "").splitlines():
        line = raw.rstrip()
        sm = _SECTION_RE.match(line)
        if sm:
            cur = {"title": sm.group(1).strip(), "items": []}
            sections.append(cur)
            continue
        bm = _BULLET_RE.match(line)
        if bm:
            if cur is None:
                cur = {"title": None, "items": []}
                sections.append(cur)
            item = _parse_bullet(bm.group(1).strip())
            if item:
                cur["items"].append(item)
    return [s for s in sections if s["items"]]


def _render_item(item: dict) -> str:
    headline = item["headline"]
    sources = item["sources"]
    text_html = _html.escape(headline)
    primary = sources[0][1] if sources else None

    if primary:
        head = (
            f'<a href="{_html.escape(primary, quote=True)}" '
            f'style="font-weight:600; color:#111111; text-decoration:none;">{text_html}</a>'
        )
    else:
        head = f'<span style="font-weight:600; color:#111111;">{text_html}</span>'

    # Copy-headline (⎘) + queue-to-Bluesky (🦋) links — shared with the rated
    # short digest so both render identically. 🦋 is omitted without a source.
    actions = render_row_action_links(headline, primary, queue_port=_QUEUE_PORT)

    pills = "".join(
        f'<a href="{_html.escape(url, quote=True)}" '
        'style="display:inline-block; padding:1px 6px; border:1px solid #b0b0b0; '
        'border-radius:4px; font-size:11px; color:#828282; vertical-align:middle; '
        f'text-decoration:none; margin-right:5px;">{_html.escape(name)}</a>'
        for name, url in sources
    )
    pill_block = f"<br>{pills}" if pills else ""

    return (
        '<tr><td style="padding:0 0 10px 0;">\n'
        '  <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" '
        f'style="border-left:4px solid {_RULE_BAR}; background-color:{_CARD_BG};">\n'
        '  <tr><td style="padding:10px 14px 10px 20px; font-size:14px; line-height:1.5; '
        f'font-family:{_FONT};">\n'
        f"    {head}{actions}{pill_block}\n"
        "  </td></tr>\n"
        "  </table>\n"
        "</td></tr>"
    )


def _render_section(section: dict) -> str:
    rows = "\n".join(_render_item(it) for it in section["items"])
    title = section.get("title")
    title_row = (
        f'<tr><td style="font-size:19px; font-weight:600; color:{_ACCENT}; '
        f'padding-bottom:14px; font-family:{_FONT};">{_html.escape(title)}</td></tr>\n'
        if title
        else ""
    )
    return (
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" '
        'style="margin-bottom:32px;">\n'
        f"{title_row}"
        f"{rows}\n"
        "</table>"
    )


_DIVIDER = (
    '<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">'
    '<tr><td style="border-top:1px solid #e0e0e0; padding-bottom:24px;"></td></tr></table>'
)


def _render_html(title: str, body_md: str, local_path: Optional[str] = None) -> str:
    body = _LEADING_H1_RE.sub("", body_md or "")
    sections = _parse_sections(body)
    safe_title = _html.escape(title or "AI Newsletter")

    now = datetime.now()
    date_long = now.strftime("%B %d, %Y at %-I:%M %p")
    n_stories = sum(len(s["items"]) for s in sections)
    n_sources = len({name for s in sections for it in s["items"] for name, _ in it["sources"]})

    if sections:
        subtitle = f"{date_long} &nbsp;&middot;&nbsp; {n_stories} stories from {n_sources}+ sources"
        content = ("\n" + _DIVIDER + "\n").join(_render_section(s) for s in sections)
    else:
        # Fallback: no parseable sections — drop the markdown HTML straight in.
        subtitle = date_long
        content = _markdown_to_html(body)

    local_link = ""
    if local_path:
        href = f"file://{local_path}"
        local_link = (
            f'<tr><td style="padding:12px 0 0 0; text-align:center;">'
            f'<a href="{_html.escape(href, quote=True)}" '
            f'style="font-size:12px; font-weight:600; color:{_ACCENT}; text-decoration:none; '
            f'font-family:{_FONT};">&#x2398;&nbsp;Open local copy with interactive features</a>'
            "</td></tr>\n"
            f'<tr><td style="padding:3px 0 0 0; font-size:11px; color:#828282; text-align:center; '
            f'font-family:{_FONT}; word-break:break-all;">{_html.escape(href)}</td></tr>'
        )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{safe_title}</title>
<!--[if mso]><style>table,td{{font-family:Arial,sans-serif!important;}}</style><![endif]-->
</head>
<body style="margin:0; padding:0; background-color:#ededf0; font-family:{_FONT};">

<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="background-color:#ededf0;">
<tr><td align="center" style="padding:20px 12px;">

<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="680" style="max-width:680px; width:100%; background-color:#fdfdfd;">

<!-- Header bar -->
<tr>
<td style="background-color:{_ACCENT}; padding:16px 32px;">
  <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">
  <tr>
    <td style="font-size:22px; font-weight:600; color:#ffffff; font-family:{_FONT};">
      <a href="{_BRAND_URL}" style="color:#ffffff; text-decoration:none;">{_BRAND}</a>
    </td>
    <td align="right" style="font-size:13px; color:rgba(255,255,255,0.6); font-family:{_FONT};">
      {_TAGLINE}
    </td>
  </tr>
  </table>
</td>
</tr>

<!-- Title -->
<tr>
<td style="padding:40px 32px 8px 32px;">
  <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">
  <tr><td style="font-size:28px; font-weight:600; color:#111111; line-height:1.25; font-family:{_FONT};">{safe_title}</td></tr>
  <tr><td style="padding-top:10px; font-size:13px; color:#828282; font-family:{_FONT};">{subtitle}</td></tr>
  </table>
</td>
</tr>

<!-- Rule -->
<tr><td style="padding:20px 32px 0 32px;"><table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%"><tr><td style="border-top:1px solid #e0e0e0;"></td></tr></table></td></tr>

<!-- Content -->
<tr>
<td style="padding:24px 32px 16px 32px;">
{content}
</td>
</tr>

<!-- Footer -->
<tr>
<td style="padding:0 32px 28px 32px;">
  <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="border-top:1px solid #e0e0e0;">
  <tr><td style="padding:20px 0 0 0; text-align:center;">
    <a href="{_BRAND_URL}" style="font-size:14px; font-weight:600; color:{_ACCENT}; text-decoration:none; font-family:{_FONT};">{_SITE}</a>
  </td></tr>
  <tr><td style="padding:8px 0 0 0; font-size:12px; color:#828282; text-align:center; font-family:{_FONT};">Generated on {date_long} by AI Newsletter Agent</td></tr>
  {local_link}
  </table>
</td>
</tr>

</table>
</td></tr>
</table>

</body>
</html>
"""


def _record_published_articles(
    state: NewsletterAgentState, session_id: str, db_path: str
) -> int:
    """Store each published article (title + short_summary embedding) in the
    `published_articles` table so future runs can suppress cross-day repeats.

    Canonical representation = OpenAI text-embedding-3-large of
    `title + short_summary` — matches what `crossdedupe` embeds for candidates.
    Returns the number of rows written.
    """
    items = state.newsletter_section_data or []
    if not items:
        return 0
    now = datetime.now().isoformat()
    texts: list[str] = []
    metas: list[tuple[str, str, str]] = []  # (url, title, short_summary)
    for it in items:
        idx = it.get("id")
        h = (
            state.headline_data[idx]
            if isinstance(idx, int) and 0 <= idx < len(state.headline_data)
            else {}
        )
        title = it.get("headline") or h.get("title", "")
        short_summary = h.get("short_summary") or it.get("summary", "") or ""
        url = it.get("link") or h.get("final_url") or h.get("url", "")
        if not url:
            continue
        texts.append((title + "\n" + short_summary).strip())
        metas.append((url, title, short_summary))
    if not texts:
        return 0

    vectors = embed_texts(texts)
    with sqlite3.connect(db_path) as conn:
        for (url, title, short_summary), vec in zip(metas, vectors):
            PublishedArticle(
                session_id=session_id, url=url, title=title,
                short_summary=short_summary, embedding=vec, published_at=now,
            ).insert(conn)
    return len(texts)


def _already_sent(db_path: str, session_id: str) -> bool:
    """True if a newsletters row already exists for this session."""
    try:
        with sqlite3.connect(db_path) as conn:
            row = conn.execute(
                "SELECT 1 FROM newsletters WHERE session_id = ? LIMIT 1",
                (session_id,),
            ).fetchone()
    except sqlite3.Error:
        return False
    return row is not None


def deliver_newsletter(
    session_id: str,
    db_path: str = "newsletter_agent.db",
    *,
    to: Optional[str] = None,
    no_email: bool = False,
    force_email: bool = False,
    echo: bool = True,
) -> Path:
    """Render + write the newsletter HTML and (optionally) email it.

    Idempotent: re-renders the HTML and updates `out/<date>.html` every
    call. The Gmail send is skipped automatically if a `newsletters` row
    already exists for this session (so calling from both `rewrite` and
    `send` does not double-deliver). Pass `force_email=True` to override.

    Returns the path to the written HTML file. Raises ClickException on
    missing state. Email failures are reported but do not raise.
    """
    load_dotenv()
    state = NewsletterAgentState(session_id=session_id, db_path=db_path).load_latest_from_db()
    if state is None:
        raise click.ClickException(f"No state found for session {session_id}")

    body = state.final_newsletter or (
        "<p><em>Dummy newsletter — pipeline ran through gather/download/send "
        "without the LLM steps.</em></p>"
    )
    title = state.newsletter_title or "AI Newsletter"

    _OUT_DIR.mkdir(exist_ok=True)
    today = date.today().isoformat()
    out_file = _OUT_DIR / f"{today}.html"
    html = _render_html(title, body, local_path=str(out_file.resolve()))
    out_file.write_text(html)

    latest = _OUT_DIR / "latest.html"
    if latest.exists() or latest.is_symlink():
        latest.unlink()
    try:
        latest.symlink_to(out_file.name)
    except OSError:
        latest.write_text(html)

    previously_sent = _already_sent(db_path, session_id)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO newsletters (session_id, title, html, sent_at, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (session_id, title, html, None, datetime.now().isoformat()),
        )
        conn.commit()

    # Record survivors (articles that made it into this newsletter) into the
    # cross-day dedup store, once per session. Never let this break a send.
    if not previously_sent:
        try:
            n = _record_published_articles(state, session_id, db_path)
            if echo and n:
                click.echo(f"Recorded {n} article(s) to the cross-day dedup store.")
        except Exception as exc:  # pragma: no cover - defensive
            click.echo(f"Warning: could not record published articles: {exc}", err=True)

    if echo:
        click.echo(f"Newsletter written to {out_file}")

    if no_email:
        return out_file
    if previously_sent and not force_email:
        if echo:
            click.echo(
                "Email skipped: newsletter already delivered for this session "
                "(pass --force-email to re-send)."
            )
        return out_file

    subject = f"{title} — {today}"
    try:
        send_gmail(subject, html, to=to)
        if echo:
            click.echo(f"Emailed newsletter to {to or '<GMAIL_USER>'}.")
    except RuntimeError as e:
        if echo:
            click.echo(
                f"Skipped email ({e}). Pass --to or set GMAIL_USER/GMAIL_PASSWORD.",
                err=True,
            )
    return out_file


@click.command()
@click.option("--db", "db_path", default="newsletter_agent.db")
@click.option("--session", "session_id", required=True)
@click.option("--to", "email_to", default=None,
              help="Email recipient for the newsletter (defaults to GMAIL_USER).")
@click.option("--no-email", "no_email", is_flag=True,
              help="Write out/<date>.html but skip sending email.")
@click.option("--force-email", "force_email", is_flag=True,
              help="Send email even if a newsletter has already been delivered for this session.")
def cli(
    db_path: str,
    session_id: str,
    email_to: Optional[str],
    no_email: bool,
    force_email: bool,
) -> None:
    state = NewsletterAgentState(session_id=session_id, db_path=db_path).load_latest_from_db()
    if state is None:
        raise click.ClickException(f"No state found for session {session_id}")

    state.start_step("send")
    state.save_checkpoint("send")

    out_file = deliver_newsletter(
        session_id=session_id,
        db_path=db_path,
        to=email_to,
        no_email=no_email,
        force_email=force_email,
    )

    state.complete_step("send", message=f"wrote {out_file}")
    state.save_checkpoint("send")


if __name__ == "__main__":  # pragma: no cover
    sys.exit(cli())
