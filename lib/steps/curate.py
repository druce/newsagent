"""Curation web server: pick stories for the newsletter by toggling
`published_articles.selected`.

Lists every row from sessions since noon of the previous calendar day, ordered
by category then rating, 50 per page. Localhost only.

    .venv/bin/python -m lib.steps.curate            # http://127.0.0.1:8766/
    .venv/bin/python -m lib.steps.curate --since 2026-11-14-12-00-00
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from urllib.parse import parse_qs, urlparse

import click

from lib.curate import fetch_page, render_page, toggle_selected, window_start
from lib.db import init_db

_DEFAULT_PORT = 8766


def _int_param(qs: dict, name: str) -> int | None:
    try:
        return int((qs.get(name) or [""])[0])
    except ValueError:
        return None


class _CurateHandler(BaseHTTPRequestHandler):
    db_path = ""  # bound per server via make_handler
    fixed_since: str | None = None  # --since; None = noon yesterday, per request

    def _since(self) -> str:
        # Recomputed per request so a server left running keeps a rolling window.
        return self.fixed_since or window_start(datetime.now())

    def _send(self, status: int, body: str, content_type: str) -> None:
        payload = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def _json(self, status: int, obj: dict) -> None:
        self._send(status, json.dumps(obj), "application/json")

    def do_GET(self) -> None:  # noqa: N802 — http.server API
        parsed = urlparse(self.path)
        if parsed.path != "/":
            self._send(404, "<html><body>Not found</body></html>",
                       "text/html; charset=utf-8")
            return
        page = _int_param(parse_qs(parsed.query), "page") or 1
        since = self._since()
        result = fetch_page(self.db_path, since, page)
        self._send(200, render_page(result, since), "text/html; charset=utf-8")

    def do_POST(self) -> None:  # noqa: N802 — http.server API
        parsed = urlparse(self.path)
        if parsed.path != "/toggle":
            self._json(404, {"error": "not found"})
            return
        aid = _int_param(parse_qs(parsed.query), "id")
        if aid is None:
            self._json(400, {"error": "id must be an integer"})
            return
        new = toggle_selected(self.db_path, aid)
        if new is None:
            self._json(404, {"error": f"no article {aid}"})
            return
        click.echo(f'{"Selected" if new else "Unselected"}: "{self._headline(aid)}"')
        self._json(200, {"id": aid, "selected": new})

    def _headline(self, aid: int) -> str:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT short_summary, title, url FROM published_articles WHERE id = ?",
                (aid,),
            ).fetchone()
        return next((v for v in row or () if v), str(aid))

    def log_message(self, format, *args) -> None:  # silence default stderr logging
        pass


def make_handler(db_path: str, since: str | None):
    """Return a _CurateHandler subclass bound to db_path and an optional fixed window start."""
    return type("BoundCurateHandler", (_CurateHandler,),
                {"db_path": db_path, "fixed_since": since})


def start_server(db_path: str, port: int, since: str | None) -> ThreadingHTTPServer:
    """Start the curate server on a daemon thread; return the server."""
    httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(db_path, since))
    Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


@click.command()
@click.option("--port", default=None, type=int,
              help=f"Port (default $CURATE_PORT or {_DEFAULT_PORT}).")
@click.option("--db", "db_path", default="newsletter_agent.db", show_default=True)
@click.option("--since", default=None,
              help="Earliest session id to show (default: noon yesterday, "
                   "e.g. 2026-11-14-12-00-00).")
def cli(port: int | None, db_path: str, since: str | None) -> None:
    """Serve the curation page for toggling published_articles.selected."""
    init_db(db_path)
    resolved_port = port or int(os.environ.get("CURATE_PORT", str(_DEFAULT_PORT)))
    httpd = ThreadingHTTPServer(("127.0.0.1", resolved_port),
                                make_handler(db_path, since))
    window = since or "noon yesterday, rolling"
    click.echo(f"Curate on http://127.0.0.1:{resolved_port}/ (since {window}). "
               "Ctrl-C to stop.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        click.echo("\nStopped.")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    cli()
