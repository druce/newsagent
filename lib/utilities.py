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


# Session ids minted by `newsagent:start` look like "2026-07-27-18-00-07".
_SESSION_ID_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})-(\d{2})-(\d{2})-(\d{2})$")


def run_stamp(session_id: Optional[str] = None) -> str:
    """Return the `YYYY-MM-DD-HHMMSS` suffix used for files in `out/`.

    Derived from `session_id` when it has the standard shape, so every write
    for a session lands on the same filename and re-running a step overwrites
    rather than accumulating. Falls back to wall-clock now() for ad-hoc
    session ids (`--session my-test`) and for callers with no session at all
    (the Bluesky digest) — those re-runs do produce a new file.
    """
    match = _SESSION_ID_RE.match(session_id or "")
    if match:
        y, mo, d, h, mi, s = (int(g) for g in match.groups())
        try:
            return datetime(y, mo, d, h, mi, s).strftime("%Y-%m-%d-%H%M%S")
        except ValueError:
            pass  # Right shape, impossible date/time — treat as ad-hoc.
    return datetime.now().strftime("%Y-%m-%d-%H%M%S")


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


def _queue_port(explicit: Optional[int] = None) -> int:
    """Resolve the bsky_share daemon port: explicit > $BSKY_QUEUE_PORT > 8765."""
    if explicit is not None:
        return int(explicit)
    return int(os.environ.get("BSKY_QUEUE_PORT", "8765"))


# Client for the local bsky_share daemon, shared by the full newsletter and the
# rated digest. Defines window.__bskyQ(anchor) — used by the 🦋 onclick — plus a
# load/focus health probe that pins a red banner when the daemon is unreachable.
#
# Kept as a plain constant (NOT an f-string): the JS is full of braces, and the
# `__PORT__` sentinel is substituted by render_bsky_share_script(). Non-ASCII is
# \u-escaped so the emitted file is encoding-agnostic, and no literal "</script>"
# appears inside any JS string.
#
# Email clients strip <script> AND onclick, so in the emailed copy __bskyQ is
# never defined, the handler never runs, and the anchor's real href navigates —
# exactly the pre-fix behaviour, no regression.
_BSKY_SHARE_JS = """<script>
(function () {
  if (window.__bskyQ) { return; }              /* tolerate a double include */
  var PORT = "__PORT__";
  var PROBE = "http://127.0.0.1:" + PORT + "/enqueue";   /* no ?u= -> no write */
  var WIN = "bskyq";                           /* ONE reused fallback window */
  var CMD = ".venv/bin/python -m lib.steps.bsky_share";
  var BANNER = "bsky-daemon-banner";
  var last = 0;

  function ok(a) {
    a.textContent = "\\u2713";
    a.style.color = "#aaaaaa";
    a.title = "Queued to Bluesky";
    a.target = "_blank";
    a.removeAttribute("data-bsky-failed");
  }

  function bad(a, why) {
    a.textContent = "\\u2717";
    a.style.color = "#cc0000";
    a.title = "Direct queue failed (" + why + "). Opened the fallback window \\u2014 "
            + "check it says Queued. Click again to retry in that same window. "
            + "Daemon: " + CMD;
    a.target = WIN;              /* later clicks navigate into the same window */
    a.setAttribute("data-bsky-failed", "1");
    try { window.open(a.href, WIN, "width=340,height=200"); } catch (e) {}
  }

  function rearm() {                        /* daemon came back: undo the red X */
    var xs = document.querySelectorAll('a[data-bsky-failed="1"]');
    for (var i = 0; i < xs.length; i++) {
      xs[i].textContent = "\\ud83e\\udd8b";
      xs[i].style.color = "#aaaaaa";
      xs[i].title = "Queue to Bluesky";
      xs[i].target = "_blank";
      xs[i].removeAttribute("data-bsky-failed");
    }
  }

  function banner(show) {
    var el = document.getElementById(BANNER);
    if (!show) { if (el && el.parentNode) { el.parentNode.removeChild(el); } return; }
    if (el || !document.body) { return; }
    el = document.createElement("div");
    el.id = BANNER;
    el.setAttribute("style", "position:sticky;top:0;z-index:2147483647;"
      + "background:#c62828;color:#ffffff;padding:10px 14px;font-size:13px;"
      + "line-height:1.45;font-family:-apple-system,BlinkMacSystemFont,sans-serif;");
    el.innerHTML = "\\u26a0\\ufe0f <b>Bluesky queue daemon not reachable</b> at "
      + "127.0.0.1:" + PORT + " \\u2014 \\ud83e\\udd8b clicks are NOT being saved "
      + "directly (each falls back to a popup window). Start or restart it with: "
      + "<code style=\\"background:rgba(255,255,255,.2);padding:1px 5px;"
      + "border-radius:3px;\\">" + CMD + "</code>";
    document.body.insertBefore(el, document.body.firstChild);
  }

  function probe() {
    var now = Date.now();
    if (now - last < 1500) { return; }        /* debounce focus storms */
    last = now;
    try {
      fetch(PROBE, { mode: "cors", cache: "no-store" }).then(function (r) {
        banner(!r.ok);
        if (r.ok) { rearm(); }
      }).catch(function () { banner(true); });
    } catch (e) { banner(true); }
  }

  window.__bskyQ = function (a) {
    if (!a || !a.href) { return true; }            /* nothing to do: let it be */
    if (a.getAttribute("data-bsky-failed") === "1") { return true; }
                       /* already degraded: plain navigation into WIN, which is
                          immune to CORS/PNA and always has user activation */
    try {
      fetch(a.href, { mode: "cors", cache: "no-store" }).then(function (r) {
        if (r.ok) { ok(a); banner(false); } else { bad(a, "HTTP " + r.status); }
      }).catch(function (e) {
        bad(a, (e && e.message) || "connection refused");
      });
    } catch (e) {
      bad(a, (e && e.message) || "fetch unavailable");
    }
    return false;
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", probe);
  } else { probe(); }
  window.addEventListener("focus", probe);
  document.addEventListener("visibilitychange", function () {
    if (!document.hidden) { probe(); }
  });
})();
</script>
"""


def render_bsky_share_script(queue_port: Optional[int] = None) -> str:
    """Return the <script> block backing the 🦋 links (see `_BSKY_SHARE_JS`).

    Emit this once per page, after the rows. Callers should skip it entirely
    when a page has no 🦋 anchors, so a stopped daemon can't pin a warning
    banner on a page with nothing to queue.
    """
    return _BSKY_SHARE_JS.replace("__PORT__", str(_queue_port(queue_port)))


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
    queue_port = _queue_port(queue_port)

    copy_js = _js_attr(headline)
    copy = (
        '<a href="#" onclick="navigator.clipboard.writeText(&quot;'
        f'{copy_js}&quot;); return false;" '
        'style="text-decoration:none; color:#aaaaaa; margin-left:6px; font-size:12px;" '
        'title="Copy headline">&#x2398;</a>'
    )

    # Behaviour lives in the shared script (`render_bsky_share_script`), which
    # must be emitted once on any page carrying these links: it reports real
    # success/failure per click and warns up front when the daemon is down.
    #
    # The href and target stay real on purpose — with the script stripped (email)
    # or JS off, the anchor still navigates to /enqueue, which is also the
    # CORS/PNA-immune recovery path the script falls back to.
    #
    # Host is the literal 127.0.0.1, never `localhost`: the latter resolves to
    # both 127.0.0.1 and ::1, and the daemon binds IPv4 only.
    share = ""
    if url:
        share_url = (
            f"http://127.0.0.1:{queue_port}/enqueue"
            f"?u={quote(url, safe='')}&t={quote(headline, safe='')}"
        )
        share = (
            f'<a href="{_html.escape(share_url, quote=True)}" target="_blank" '
            'onclick="return __bskyQ(this);" '
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
    # Only ship the client when there is at least one 🦋 anchor to drive —
    # otherwise a stopped daemon would pin a red banner on a page with nothing
    # to queue.
    share_script = render_bsky_share_script() if "/enqueue" in items_html else ""
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
        + share_script
        + "</body></html>"
    )


def write_short_digest(
    session_id: str,
    headlines: list[dict],
    *,
    out_dir: str | Path = "out",
) -> tuple[Path, str]:
    """Render the rated digest, write to out/<stamp>_short.html, update latest_short.html.

    `headlines` must be filtered + sorted by the caller. Returns
    (dated_path, html_content).
    """
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    html_content = render_rated_digest(session_id, headlines, timestamp)

    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    dated = out_path / f"{run_stamp(session_id)}_short.html"
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
