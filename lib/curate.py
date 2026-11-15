"""Curation over `published_articles`: window query, `selected` toggle, page HTML.

Pure functions over a db path, wrapped by the `lib.steps.curate` web server.
Each call opens its own connection (the server is threaded).
"""
from __future__ import annotations

import html as _html
import sqlite3
from datetime import datetime, timedelta

from lib.utilities import _strip_no_content

PER_PAGE = 50

_COLS = ("id", "session_id", "url", "title", "short_summary", "summary",
         "site_name", "cat", "rating", "selected")

# Session ids are start timestamps (YYYY-MM-DD-HH-MM-SS), so a string compare
# gives the time window. The GLOB keeps non-date ids ("test-…" sorts after
# "2026-…") out of it.
_WHERE = "session_id >= ? AND session_id GLOB '[0-9][0-9][0-9][0-9]-*'"

_ORDER = (
    "CASE WHEN cat IS NULL OR cat = '' THEN 2 WHEN cat = 'Other' THEN 1 ELSE 0 END, "
    "cat, rating DESC NULLS LAST, id"
)


def window_start(now: datetime) -> str:
    """Session-id lower bound: noon of the calendar day before `now`."""
    return f"{(now.date() - timedelta(days=1)):%Y-%m-%d}-12-00-00"


def fetch_page(db_path: str, since: str, page: int, per_page: int = PER_PAGE) -> dict:
    """One page of in-window rows, plus paging and selected counts."""
    with sqlite3.connect(db_path) as conn:
        total, selected = conn.execute(
            f"SELECT COUNT(*), COALESCE(SUM(selected), 0) "
            f"FROM published_articles WHERE {_WHERE}", (since,)
        ).fetchone()
        pages = max(1, -(-total // per_page))
        page = min(max(1, page), pages)
        rows = conn.execute(
            f"SELECT {', '.join(_COLS)} FROM published_articles WHERE {_WHERE} "
            f"ORDER BY {_ORDER} LIMIT ? OFFSET ?",
            (since, per_page, (page - 1) * per_page),
        ).fetchall()
    return {
        "rows": [dict(zip(_COLS, r)) for r in rows],
        "page": page, "pages": pages, "total": total, "selected": selected,
    }


def toggle_selected(db_path: str, article_id: int) -> int | None:
    """Flip `selected` for one row; return the new value, or None if no such id."""
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(
            "UPDATE published_articles SET selected = 1 - selected WHERE id = ?",
            (article_id,),
        )
        if cur.rowcount == 0:
            return None
        return conn.execute(
            "SELECT selected FROM published_articles WHERE id = ?", (article_id,)
        ).fetchone()[0]


# ── Page rendering ───────────────────────────────────────────────────────────

_ON, _OFF = "\u2713 Selected", "Select"

_CSS = (
    "body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;"
    "max-width:900px;margin:20px auto;padding:0 16px;color:#222;}"
    "h1{font-size:18px;margin-bottom:6px;}"
    "h2{font-size:16px;margin:24px 0 8px;border-bottom:1px solid #ddd;padding-bottom:4px;}"
    ".bar{margin:8px 0 16px;color:#444;font-size:14px;}"
    ".bar a{margin-right:12px;}"
    ".row{margin-bottom:16px;padding:10px;border-left:3px solid #ccc;}"
    ".row.sel{border-left-color:#4CAF50;background:#f1f8f1;}"
    ".row h3{margin:0 0 5px 0;font-size:15px;}"
    ".row h3 a{color:#0366d6;text-decoration:none;}"
    ".src{color:#666;}"
    ".row p{margin:5px 0 0 0;color:#444;font-size:13px;line-height:1.5;}"
    ".tog{float:right;margin-left:10px;padding:4px 10px;border:1px solid #999;"
    "border-radius:4px;background:#fff;cursor:pointer;font-size:13px;}"
    ".row.sel .tog{background:#4CAF50;border-color:#4CAF50;color:#fff;}"
)

_JS = """<script>
document.addEventListener('click', async (e) => {
  const b = e.target.closest('.tog');
  if (!b) return;
  b.disabled = true;
  try {
    const r = await fetch('/toggle?id=' + b.dataset.id, {method: 'POST'});
    if (!r.ok) throw new Error(r.status);
    const j = await r.json();
    const row = b.closest('.row');
    const was = row.classList.contains('sel');
    const now = j.selected === 1;
    row.classList.toggle('sel', now);
    b.textContent = now ? '__ON__' : '__OFF__';
    if (was !== now) {
      const c = document.getElementById('nsel');
      c.textContent = String(Number(c.textContent) + (now ? 1 : -1));
    }
  } catch (err) {
    b.textContent = 'error';
  } finally {
    b.disabled = false;
  }
});
</script>""".replace("__ON__", _ON).replace("__OFF__", _OFF)


def _render_row(r: dict) -> str:
    esc = _html.escape
    headline = (_strip_no_content(r.get("short_summary")) or r.get("title")
                or "(no title)")
    rating = r.get("rating")
    rating_txt = f"{rating:.2f} &mdash; " if rating is not None else ""
    summary = esc(_strip_no_content(r.get("summary")).strip()).replace("\n", "<br>")
    sel = bool(r.get("selected"))
    return (
        f'<div class="row{" sel" if sel else ""}">'
        f'<button class="tog" data-id="{int(r["id"])}">{_ON if sel else _OFF}</button>'
        f'<h3>{rating_txt}<a href="{esc(r.get("url") or "#", quote=True)}" '
        f'target="_blank">{esc(headline)}</a>'
        f' &mdash; <span class="src">{esc(r.get("site_name") or "")}</span></h3>'
        f"<p>{summary}</p></div>"
    )


def render_page(result: dict, since: str) -> str:
    """Full HTML document for one page of `fetch_page` output."""
    esc = _html.escape
    page, pages = result["page"], result["pages"]
    nav = []
    if page > 1:
        nav.append(f'<a href="/?page={page - 1}">&larr; Prev</a>')
    if page < pages:
        nav.append(f'<a href="/?page={page + 1}">Next &rarr;</a>')
    bar = (
        f'<div class="bar"><span id="nsel">{result["selected"]}</span> selected of '
        f'{result["total"]} &mdash; since {esc(since)} &mdash; page {page}/{pages}'
        f'<br>{" ".join(nav)}</div>'
    )
    bottom_nav = f'<div class="bar">{" ".join(nav)}</div>' if nav else ""

    body = []
    prev_cat = object()
    for r in result["rows"]:
        cat = r.get("cat") or None
        if cat != prev_cat:
            body.append(f"<h2>{esc(cat or 'Uncategorized')}</h2>")
            prev_cat = cat
        body.append(_render_row(r))
    if not result["rows"]:
        body.append(f"<p>No articles since {esc(since)}.</p>")

    return (
        '<!DOCTYPE html>\n<html><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>Curate</title><style>{_CSS}</style></head><body>"
        "<h1>Curate</h1>" + bar + "\n".join(body) + bottom_nav + _JS + "</body></html>"
    )
