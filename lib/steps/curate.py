"""Curation web server: pick stories for the newsletter by toggling
`published_articles.selected`.

Lists every row from sessions since noon of the previous calendar day, ordered
by category then rating, 50 per page. Localhost only.

Routes: GET / (page), POST /toggle?id= (flip selected), and JSON POSTs
/update {id, url, short_summary, site_name}, /delete {id},
/create {url, short_summary, site_name} — see lib.curate for the rules.

    .venv/bin/python -m lib.steps.curate            # http://127.0.0.1:8766/
    .venv/bin/python -m lib.steps.curate --since 2026-11-14-12-00-00
    .venv/bin/python -m lib.steps.curate --latest   # most recent session only
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

from lib.curate import (CurateError, create_article, delete_article, fetch_page,
                        latest_session, render_page, render_row, toggle_selected,
                        update_article, window_start)
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
        if not self._same_origin():
            self._json(403, {"error": "cross-origin request refused"})
            return
        route = {"/update": self._update, "/delete": self._delete,
                 "/create": self._create}.get(parsed.path)
        if route is not None:
            try:
                route(self._read_json())
            except CurateError as e:
                self._json(e.status, {"error": str(e)})
            except sqlite3.Error as e:
                self._json(500, {"error": f"database error: {e}"})
            return
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

    def _same_origin(self) -> bool:
        """Refuse browser POSTs from other sites (no auth on this server)."""
        origin = self.headers.get("Origin")
        if not origin:
            return True  # non-browser client (curl, tests)
        return urlparse(origin).netloc == (self.headers.get("Host") or "")

    def _read_json(self) -> dict:
        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if ctype != "application/json":
            # Forces a CORS preflight for cross-site fetches, which we never answer.
            raise CurateError(415, "Content-Type must be application/json")
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, UnicodeDecodeError):
            raise CurateError(400, "body must be JSON") from None
        if not isinstance(body, dict):
            raise CurateError(400, "body must be a JSON object")
        return body

    @staticmethod
    def _id(body: dict) -> int:
        aid = body.get("id")
        if isinstance(aid, bool) or not isinstance(aid, int) or not 0 < aid < 2**63:
            raise CurateError(400, "id must be an integer")
        return aid

    @staticmethod
    def _fields(body: dict) -> tuple[str, str, str]:
        return tuple(str(body.get(k) or "") for k in ("url", "short_summary", "site_name"))

    def _update(self, body: dict) -> None:
        row = update_article(self.db_path, self._id(body), *self._fields(body))
        click.echo(f'Updated: "{row["short_summary"]}" -> {row["url"]}')
        self._json(200, {"row": row, "html": render_row(row)})

    def _delete(self, body: dict) -> None:
        row = delete_article(self.db_path, self._id(body))
        click.echo(f'Deleted: "{row["short_summary"] or row["title"] or row["url"]}"')
        self._json(200, {"id": row["id"], "selected": row["selected"]})

    def _create(self, body: dict) -> None:
        row = create_article(self.db_path, *self._fields(body))
        click.echo(f'Created: "{row["short_summary"]}" -> {row["url"]}')
        self._json(200, {"row": row})

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
@click.option("--host", default=None,
              help="Bind address (default $CURATE_HOST or 127.0.0.1). Use 0.0.0.0 "
                   "for all interfaces — no auth, so only on a trusted network.")
@click.option("--port", default=None, type=int,
              help=f"Port (default $CURATE_PORT or {_DEFAULT_PORT}).")
@click.option("--db", "db_path", default="newsletter_agent.db", show_default=True)
@click.option("--since", default=None,
              help="Earliest session id to show (default: noon yesterday, "
                   "e.g. 2026-11-14-12-00-00).")
@click.option("--latest", is_flag=True,
              help="Show only the most recent session (resolved at startup).")
def cli(host: str | None, port: int | None, db_path: str, since: str | None,
        latest: bool) -> None:
    """Serve the curation page for toggling published_articles.selected."""
    init_db(db_path)
    if latest:
        if since:
            raise click.UsageError("--latest and --since are mutually exclusive.")
        since = latest_session(db_path)
        if since is None:
            raise click.ClickException("No sessions in published_articles.")
    resolved_port = port or int(os.environ.get("CURATE_PORT", str(_DEFAULT_PORT)))
    host = host or os.environ.get("CURATE_HOST", "127.0.0.1")
    httpd = ThreadingHTTPServer((host, resolved_port),
                                make_handler(db_path, since))
    window = since or "noon yesterday, rolling"
    click.echo(f"Curate on http://{host}:{resolved_port}/ (since {window}). "
               "Ctrl-C to stop.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        click.echo("\nStopped.")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    cli()
