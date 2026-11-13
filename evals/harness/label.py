"""Web harness for hand-labeling a CSV in place (True/False per row).

Columns by prefix:
  input_*  always shown — what the labeler judges
  rec_*    a recommended label + rationale (hidden with --no-rec)
  info_*   extra context, e.g. model predictions (hidden with --no-info)
Every other column is kept in the file untouched and not shown.

True/False writes the label column (default human_is_ai) and rewrites the
CSV atomically, then moves to the next row (stays put on the last row).
Previous/Next navigate without saving. Keys: t / f / ← / →.

  .venv/bin/python -m evals.harness.label FILE.csv [--no-rec] [--no-info] [--port 8765]
"""
from __future__ import annotations

import csv
import html
import os
import re
import sys
import tempfile
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import click

VALUES = ("True", "False")


class HarnessError(Exception):
    pass


class LabelSet:
    def __init__(self, path: Path, fieldnames: list[str], rows: list[dict], label_col: str):
        self.path, self.fieldnames, self.rows, self.label_col = path, fieldnames, rows, label_col
        self._lock = threading.Lock()

    @classmethod
    def load(cls, path: Path, label_col: str) -> "LabelSet":
        path = Path(path)
        try:
            with path.open(newline="", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                rows = list(reader)
                fieldnames = list(reader.fieldnames or [])
        except (OSError, UnicodeDecodeError, csv.Error) as e:
            raise HarnessError(f"cannot read {path}: {e}") from e
        if not any(c.startswith("input_") for c in fieldnames):
            raise HarnessError(f"{path} has no input_* columns (found: {', '.join(fieldnames) or 'none'})")
        if not rows:
            raise HarnessError(f"{path} has no data rows")
        if label_col not in fieldnames:
            fieldnames.append(label_col)
            for r in rows:
                r[label_col] = ""
        return cls(path, fieldnames, rows, label_col)

    def columns(self, prefix: str) -> list[str]:
        return [c for c in self.fieldnames if c.startswith(prefix)]

    def label(self, i: int) -> str:
        return (self.rows[i].get(self.label_col) or "").strip()

    def n_labeled(self) -> int:
        return sum(1 for i in range(len(self.rows)) if self.label(i))

    def first_unlabeled(self) -> int:
        return next((i for i in range(len(self.rows)) if not self.label(i)), 0)

    def set(self, i: int, value: str) -> None:
        if value not in VALUES:
            raise ValueError(f"label must be one of {VALUES}, got {value!r}")
        with self._lock:
            self.rows[i][self.label_col] = value
            self._save()

    def _save(self) -> None:
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=f".{self.path.name}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=self.fieldnames, extrasaction="ignore")
                w.writeheader()
                w.writerows(self.rows)
            os.replace(tmp, self.path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise


CSS = """
:root{--bg:#fafafa;--fg:#1d1d1f;--muted:#6e6e73;--card:#fff;--line:#e2e2e6;--true:#1a7f37;--false:#c62828;--rec:#f3f0ff;--info:#f2f6fa}
@media (prefers-color-scheme:dark){:root{--bg:#161618;--fg:#ececf0;--muted:#9a9aa2;--card:#202024;--line:#33333a;--true:#3fb950;--false:#f47067;--rec:#272236;--info:#1e252d}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.5 -apple-system,system-ui,sans-serif}
main{max-width:860px;margin:0 auto;padding:20px 16px 120px}
header{display:flex;justify-content:space-between;color:var(--muted);font-size:14px;margin-bottom:12px}
section{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 18px;margin-bottom:14px}
section.rec{background:var(--rec)}section.info{background:var(--info)}
h2{font-size:12px;letter-spacing:.06em;text-transform:uppercase;color:var(--muted);margin:0 0 8px}
dl{margin:0}dt{font-size:12px;color:var(--muted);margin-top:8px}dd{margin:2px 0 0;white-space:pre-wrap;word-wrap:break-word}
dd.input_title{font-size:20px;font-weight:600}
nav{position:fixed;bottom:0;left:0;right:0;background:var(--card);border-top:1px solid var(--line);padding:12px 16px;display:flex;justify-content:center;gap:12px}
nav form{display:contents}
button{font:inherit;font-weight:600;padding:10px 22px;border-radius:8px;border:2px solid var(--line);background:var(--card);color:var(--fg);cursor:pointer;min-width:110px}
button:disabled{opacity:.35;cursor:default}
button.true{border-color:var(--true);color:var(--true)}button.false{border-color:var(--false);color:var(--false)}
button.true.current{background:var(--true);color:#fff}button.false.current{background:var(--false);color:#fff}
.status{font-weight:600}
"""

JS = """
document.addEventListener('keydown', e => {
  if (e.metaKey || e.ctrlKey || e.altKey) return;
  const id = {t:'true', f:'false', ArrowLeft:'prev', ArrowRight:'next'}[e.key];
  const b = id && document.getElementById(id);
  if (b && !b.disabled) { e.preventDefault(); b.click(); }
});
"""


def _section(ls: LabelSet, i: int, prefix: str, title: str, cls: str) -> str:
    cols = ls.columns(prefix)
    if not cols:
        return ""
    items = "".join(
        f'<dt>{html.escape(c)}</dt><dd class="{html.escape(c)}">{html.escape(ls.rows[i].get(c) or "") or "&mdash;"}</dd>'
        for c in cols)
    return f'<section class="{cls}"><h2>{title}</h2><dl>{items}</dl></section>'


def render(ls: LabelSet, i: int, show_rec: bool, show_info: bool) -> str:
    n, cur = len(ls.rows), ls.label(i)
    body = _section(ls, i, "input_", "Input", "input")
    if show_rec:
        body += _section(ls, i, "rec_", "Recommendation", "rec")
    if show_info:
        body += _section(ls, i, "info_", "Info", "info")

    def btn(value: str) -> str:
        current = ' class="%s current"' % value.lower() if cur == value else ' class="%s"' % value.lower()
        return (f'<button type="submit" name="value" value="{value}" id="{value.lower()}"{current}>'
                f'{value}</button>')

    prev_dis = " disabled" if i == 0 else ""
    next_dis = " disabled" if i == n - 1 else ""
    status = html.escape(cur) if cur else "unlabeled"
    return f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Label {i + 1}/{n}</title><style>{CSS}</style></head>
<body><main>
<header><span>Row {i + 1} / {n} &middot; {ls.n_labeled()} labeled</span>
<span>{html.escape(ls.label_col)}: <span class="status">{status}</span></span></header>
{body}</main>
<nav>
<button type="button" id="prev"{prev_dis} onclick="location.href='/r/{i - 1}'">&larr; Previous</button>
<form method="post" action="/r/{i}">{btn("True")}{btn("False")}</form>
<button type="button" id="next"{next_dis} onclick="location.href='/r/{i + 1}'">Next &rarr;</button>
</nav><script>{JS}</script></body></html>"""


def make_handler(ls: LabelSet, show_rec: bool, show_info: bool):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def _index(self) -> int | None:
            m = re.fullmatch(r"/r/(\d+)", urllib.parse.urlparse(self.path).path)
            if m and int(m.group(1)) < len(ls.rows):
                return int(m.group(1))
            return None

        def _redirect(self, i: int) -> None:
            self.send_response(303)
            self.send_header("Location", f"/r/{i}")
            self.end_headers()

        def do_GET(self):
            if urllib.parse.urlparse(self.path).path == "/":
                return self._redirect(ls.first_unlabeled())
            i = self._index()
            if i is None:
                return self.send_error(404)
            page = render(ls, i, show_rec, show_info).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(page)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(page)

        def do_POST(self):
            i = self._index()
            if i is None:
                return self.send_error(404)
            length = int(self.headers.get("Content-Length") or 0)
            form = urllib.parse.parse_qs(self.rfile.read(length).decode())
            try:
                ls.set(i, (form.get("value") or [""])[0])
            except ValueError as e:
                return self.send_error(400, str(e))
            except OSError as e:
                return self.send_error(500, f"could not save: {e}")
            self._redirect(min(i + 1, len(ls.rows) - 1))

    return Handler


@click.command()
@click.argument("csv_file", type=click.Path(path_type=Path))
@click.option("--label-col", default="human_is_ai", show_default=True, help="Column the label is written to.")
@click.option("--no-rec", is_flag=True, help="Hide rec_* columns.")
@click.option("--no-info", is_flag=True, help="Hide info_* columns.")
@click.option("--host", default="127.0.0.1", show_default=True)
@click.option("--port", default=8765, show_default=True, type=int)
def cli(csv_file, label_col, no_rec, no_info, host, port):
    try:
        ls = LabelSet.load(csv_file, label_col)
    except HarnessError as e:
        click.echo(f"error: {e}", err=True)
        sys.exit(1)
    try:
        srv = ThreadingHTTPServer((host, port), make_handler(ls, not no_rec, not no_info))
    except OSError as e:
        click.echo(f"error: cannot listen on {host}:{port}: {e}", err=True)
        sys.exit(1)
    click.echo(f"{len(ls.rows)} rows, {ls.n_labeled()} labeled → {csv_file}")
    click.echo(f"Labeling at http://{host}:{srv.server_port}/   (Ctrl-C to stop)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


if __name__ == "__main__":
    cli()
