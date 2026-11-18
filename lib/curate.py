"""Curation over `published_articles`: window query, `selected` toggle, page HTML.

Pure functions over a db path, wrapped by the `lib.steps.curate` web server.
Each call opens its own connection (the server is threaded).
"""
from __future__ import annotations

import html as _html
import sqlite3
from datetime import datetime, timedelta
from urllib.parse import urlparse

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


def latest_session(db_path: str) -> str | None:
    """Most recent date-shaped session id in published_articles (None if empty)."""
    with sqlite3.connect(db_path) as conn:
        return conn.execute(
            "SELECT MAX(session_id) FROM published_articles "
            "WHERE session_id GLOB '[0-9][0-9][0-9][0-9]-*'"
        ).fetchone()[0]


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


def selected_rows(db_path: str, since: str) -> list[dict]:
    """Every selected in-window row, in curate-page order (for the edition draft)."""
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            f"SELECT {', '.join(_COLS)} FROM published_articles "
            f"WHERE {_WHERE} AND selected = 1 ORDER BY {_ORDER}", (since,)
        ).fetchall()
    return [dict(zip(_COLS, r)) for r in rows]


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


# ── Row edit / delete / create ───────────────────────────────────────────────

class CurateError(ValueError):
    """A rejected edit; `status` is the HTTP status the server should return."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def domain_source(url: str) -> str:
    """Default source label for a URL: its host, lowercased, minus `www.`."""
    host = (urlparse((url or "").strip()).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def get_article(db_path: str, article_id: int) -> dict | None:
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            f"SELECT {', '.join(_COLS)} FROM published_articles WHERE id = ?",
            (article_id,),
        ).fetchone()
    return dict(zip(_COLS, row)) if row else None


def _must_get(db_path: str, article_id: int | None) -> dict:
    row = get_article(db_path, article_id) if article_id is not None else None
    if row is None:
        raise CurateError(404, f"no article {article_id}")
    return row


def _validate(url: str, short_summary: str) -> tuple[str, str]:
    url, short_summary = (url or "").strip(), " ".join((short_summary or "").split())
    if not url.lower().startswith(("http://", "https://")):
        raise CurateError(400, "URL must start with http:// or https://")
    if not short_summary:
        raise CurateError(400, "Headline must not be empty")
    return url, short_summary


def _duplicate(conn: sqlite3.Connection, url: str) -> CurateError:
    row = conn.execute("SELECT id FROM published_articles WHERE url = ?",
                       (url,)).fetchone()
    return CurateError(409, f"URL already exists (id {row[0] if row else '?'})")


def update_article(db_path: str, article_id: int, url: str, short_summary: str,
                   site_name: str) -> dict:
    """Set url / short_summary / site_name on one row; no other column changes."""
    url, short_summary = _validate(url, short_summary)
    with sqlite3.connect(db_path) as conn:
        try:
            cur = conn.execute(
                "UPDATE published_articles SET url = ?, short_summary = ?, "
                "site_name = ? WHERE id = ?",
                (url, short_summary, (site_name or "").strip(), article_id),
            )
        except sqlite3.IntegrityError:
            raise _duplicate(conn, url) from None
        if cur.rowcount == 0:
            raise CurateError(404, f"no article {article_id}")
    return _must_get(db_path, article_id)


def delete_article(db_path: str, article_id: int) -> dict:
    """Hard-delete one row; return it as it was."""
    row = _must_get(db_path, article_id)
    with sqlite3.connect(db_path) as conn:
        conn.execute("DELETE FROM published_articles WHERE id = ?", (article_id,))
    return row


def create_article(db_path: str, url: str, short_summary: str, site_name: str = "",
                   now: datetime | None = None) -> dict:
    """Insert a hand-added story into the latest session, selected, cat 'Other'."""
    url, short_summary = _validate(url, short_summary)
    session = latest_session(db_path) or f"{now or datetime.now():%Y-%m-%d-%H-%M-%S}"
    with sqlite3.connect(db_path) as conn:
        try:
            cur = conn.execute(
                "INSERT INTO published_articles (session_id, url, title, "
                "short_summary, summary, site_name, cat, rating, selected) "
                "VALUES (?, ?, ?, ?, '', ?, 'Other', NULL, 1)",
                (session, url, short_summary, short_summary,
                 (site_name or "").strip() or domain_source(url)),
            )
        except sqlite3.IntegrityError:
            raise _duplicate(conn, url) from None
        new_id = cur.lastrowid
    return _must_get(db_path, new_id)


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
    ".edit{float:right;margin-left:6px;padding:4px 10px;border:1px solid #999;"
    "border-radius:4px;background:#fff;cursor:pointer;font-size:13px;}"
    "#add{margin-right:10px;padding:4px 10px;border:1px solid #0366d6;color:#0366d6;"
    "border-radius:4px;background:#fff;cursor:pointer;font-size:13px;}"
    ".ef{margin:8px 0 16px;padding:10px;border:1px solid #ccc;border-radius:4px;"
    "background:#fafafa;font-size:13px;}"
    ".ef label{display:block;margin-bottom:6px;color:#555;}"
    ".ef input,.ef textarea{display:block;font-family:inherit;width:100%;box-sizing:border-box;padding:5px;"
    "font-size:14px;margin-top:2px;}"
    ".ef .btns{margin-top:8px;}"
    ".ef button{padding:4px 12px;margin-right:6px;cursor:pointer;font-size:13px;}"
    ".ef .delbox{float:right;}"
    ".ef .del{color:#c00;border:1px solid #c00;background:#fff;"
    "border-radius:4px;}"
    ".ef .err{color:#c00;margin-left:6px;}"
)

_JS = """<script>
function esc(s) {
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}
function bump(id, d) {
  const c = document.getElementById(id);
  c.textContent = String(Number(c.textContent) + d);
}
function hostOf(u) {
  try { return new URL(u).hostname.toLowerCase().replace(/^www\\./, ''); }
  catch (e) { return ''; }
}
function formHtml(url, headline, source, withDelete) {
  return '<label>URL<input name="url" value="' + esc(url) + '"></label>'
    + '<label>Headline<textarea name="headline" rows="2">' + esc(headline) + '</textarea></label>'
    + '<label>Source<input name="source" value="' + esc(source) + '"></label>'
    + '<div class="btns"><button type="submit">Save</button>'
    + '<button type="button" class="cancel">Cancel</button>'
    + '<span class="err"></span>'
    + (withDelete ? '<span class="delbox"><button type="button" class="del">Delete</button></span>' : '')
    + '</div>';
}
async function post(path, obj) {
  const r = await fetch(path, {method: 'POST',
    headers: {'Content-Type': 'application/json'}, body: JSON.stringify(obj)});
  let j = {};
  try { j = await r.json(); } catch (e) {}
  if (!r.ok) throw new Error(j.error || ('HTTP ' + r.status));
  return j;
}
function fields(f) {
  return {url: f.elements.url.value, short_summary: f.elements.headline.value,
          site_name: f.elements.source.value};
}
function openEdit(row) {
  if (row.querySelector('.ef')) return;
  const f = document.createElement('form');
  f.className = 'ef';
  f.innerHTML = formHtml(row.dataset.url, row.dataset.headline, row.dataset.source, true);
  row.querySelector('.view').hidden = true;
  row.appendChild(f);
  f.elements.headline.focus();
  f.addEventListener('submit', async (e) => {
    e.preventDefault();
    const err = f.querySelector('.err');
    err.textContent = '';
    try {
      const j = await post('/update', Object.assign({id: Number(row.dataset.id)}, fields(f)));
      row.outerHTML = j.html;
    } catch (x) { err.textContent = x.message; }
  });
}
function openAdd() {
  if (document.getElementById('addform')) return;
  const f = document.createElement('form');
  f.className = 'ef';
  f.id = 'addform';
  f.innerHTML = formHtml('', '', '', false);
  document.querySelector('.bar').after(f);
  f.elements.url.focus();
  f.elements.url.addEventListener('change', () => {
    if (!f.elements.source.value) f.elements.source.value = hostOf(f.elements.url.value);
  });
  f.addEventListener('submit', async (e) => {
    e.preventDefault();
    const err = f.querySelector('.err');
    err.textContent = '';
    try { await post('/create', fields(f)); location.reload(); }
    catch (x) { err.textContent = x.message; }
  });
}
document.addEventListener('click', async (e) => {
  if (e.target.closest('#add')) { openAdd(); return; }
  const ed = e.target.closest('.edit');
  if (ed) { openEdit(ed.closest('.row')); return; }
  const f = e.target.closest('.ef');
  if (f && e.target.closest('.cancel')) {
    const row = f.closest('.row');
    if (row) row.querySelector('.view').hidden = false;
    f.remove();
    return;
  }
  if (f && e.target.closest('.del')) {
    f.querySelector('.delbox').innerHTML = 'Delete? <button type="button" class="yes">yes</button>'
      + '<button type="button" class="no">no</button>';
    return;
  }
  if (f && e.target.closest('.no')) {
    f.querySelector('.delbox').innerHTML = '<button type="button" class="del">Delete</button>';
    return;
  }
  if (f && e.target.closest('.yes')) {
    const row = f.closest('.row');
    try {
      const j = await post('/delete', {id: Number(row.dataset.id)});
      row.remove();
      bump('ntotal', -1);
      if (j.selected === 1) bump('nsel', -1);
    } catch (x) { f.querySelector('.err').textContent = x.message; }
    return;
  }
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
    if (was !== now) bump('nsel', now ? 1 : -1);
  } catch (err) {
    b.textContent = 'error';
  } finally {
    b.disabled = false;
  }
});
</script>""".replace("__ON__", _ON).replace("__OFF__", _OFF)


def render_row(r: dict) -> str:
    """One row's HTML; data-* attributes seed the inline edit form."""
    esc = _html.escape
    headline = (_strip_no_content(r.get("short_summary")) or r.get("title")
                or "(no title)")
    edit_headline = (_strip_no_content(r.get("short_summary")) or r.get("title")
                     or "")
    rating = r.get("rating")
    rating_txt = f"{rating:.2f} &mdash; " if rating is not None else ""
    summary = esc(_strip_no_content(r.get("summary")).strip()).replace("\n", "<br>")
    sel = bool(r.get("selected"))
    aid = int(r["id"])
    return (
        f'<div class="row{" sel" if sel else ""}" id="row-{aid}" data-id="{aid}" '
        f'data-url="{esc(r.get("url") or "", quote=True)}" '
        f'data-headline="{esc(edit_headline, quote=True)}" '
        f'data-source="{esc(r.get("site_name") or "", quote=True)}">'
        f'<div class="view">'
        f'<button class="tog" data-id="{aid}">{_ON if sel else _OFF}</button>'
        f'<button class="edit">\u270e Edit</button>'
        f'<h3>{rating_txt}<a href="{esc(r.get("url") or "#", quote=True)}" '
        f'target="_blank">{esc(headline)}</a>'
        f' &mdash; <span class="src">{esc(r.get("site_name") or "")}</span></h3>'
        f"<p>{summary}</p></div></div>"
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
        f'<div class="bar"><button id="add">+ Add story</button> '
        f'<span id="nsel">{result["selected"]}</span> selected of '
        f'<span id="ntotal">{result["total"]}</span> &mdash; since {esc(since)} &mdash; page {page}/{pages}'
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
        body.append(render_row(r))
    if not result["rows"]:
        body.append(f"<p>No articles since {esc(since)}.</p>")

    return (
        '<!DOCTYPE html>\n<html><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>Curate</title><style>{_CSS}</style></head><body>"
        "<h1>Curate</h1>" + bar + "\n".join(body) + bottom_nav + _JS + "</body></html>"
    )
