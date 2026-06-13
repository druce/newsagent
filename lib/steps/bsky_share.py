"""Share-to-Bluesky daemon: local enqueue server + queue-draining worker.

Run alongside an open `out/latest.html`: clicking the butterfly link on a row
hits this process's HTTP server, which queues the item; the worker loop posts
the next pending item to Bluesky as a link-preview card every `--interval`
seconds.

    .venv/bin/python -m lib.steps.bsky_share --interval 60
"""
from __future__ import annotations

import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.parse import parse_qs, urlparse

import click
import httpx

from lib.bluesky.api import bsky_login
from lib.bsky_queue.poster import post_item
from lib.bsky_queue.queue import (
    enqueue,
    mark_failed,
    mark_posted,
    next_pending,
    pending_count,
)
from lib.db import init_db

_IMAGE_DIR = Path("download/bsky-images")


def _is_auth_error(exc: Exception) -> bool:
    """True for the HTTP errors that an expired/invalid accessJwt produces."""
    return (
        isinstance(exc, httpx.HTTPStatusError)
        and exc.response.status_code in (400, 401)
    )


def process_one(
    db_path: str,
    session: dict,
    image_dir: str | Path = _IMAGE_DIR,
    relogin=None,
) -> bool:
    """Post the next pending item. Returns False if the queue was empty.

    On an auth-type failure, calls `relogin()` (if given) to refresh the session
    in place and retries the item once before giving up.
    """
    item = next_pending(db_path)
    if not item:
        return False
    try:
        uri = post_item(session, item, image_dir)
    except Exception as exc:  # noqa: BLE001 — record any failure on the row
        if relogin is not None and _is_auth_error(exc):
            try:
                session.update(relogin())
                uri = post_item(session, item, image_dir)
            except Exception as exc2:  # noqa: BLE001
                mark_failed(db_path, item["id"], str(exc2))
                return True
        else:
            mark_failed(db_path, item["id"], str(exc))
            return True
    mark_posted(db_path, item["id"], uri)
    return True


class _EnqueueHandler(BaseHTTPRequestHandler):
    db_path = ""  # overridden per-instance via the handler factory

    def do_GET(self) -> None:  # noqa: N802 — http.server API
        parsed = urlparse(self.path)
        if parsed.path != "/enqueue":
            self.send_response(404)
            self.end_headers()
            return
        qs = parse_qs(parsed.query)
        url = (qs.get("u") or [""])[0]
        title = (qs.get("t") or [""])[0]
        inserted = enqueue(self.db_path, url, title or url) if url else False

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        msg = "Queued ✓" if inserted else "Already queued"
        self.wfile.write(
            f"<html><body style='font-family:sans-serif'>{msg}</body></html>".encode()
        )

    def log_message(self, format, *args) -> None:  # silence default stderr logging
        pass


def make_handler(db_path: str):
    """Return an _EnqueueHandler subclass bound to db_path."""
    return type("BoundEnqueueHandler", (_EnqueueHandler,), {"db_path": db_path})


def start_server(db_path: str, port: int) -> ThreadingHTTPServer:
    """Start the enqueue HTTP server on a daemon thread; return the server."""
    httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(db_path))
    Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


@click.command()
@click.option("--interval", default=60, show_default=True,
              help="Seconds between posting the next queued item.")
@click.option("--port", default=None, type=int,
              help="Enqueue server port (default $BSKY_QUEUE_PORT or 8765).")
@click.option("--db", "db_path", default="newsletter_agent.db", show_default=True)
@click.option("--once", is_flag=True,
              help="Drain a single pending item and exit (no server).")
def cli(interval: int, port: int | None, db_path: str, once: bool) -> None:
    """Run the share-to-Bluesky enqueue server + worker loop."""
    init_db(db_path)
    username = os.environ.get("BSKY_USERNAME")
    secret = os.environ.get("BSKY_SECRET")
    if not username or not secret:
        raise click.ClickException(
            "BSKY_USERNAME and BSKY_SECRET env vars are required."
        )

    def login() -> dict:
        return bsky_login(username, secret)

    session = login()

    if once:
        did = process_one(db_path, session, _IMAGE_DIR, relogin=login)
        click.echo("Posted 1 item." if did else "Queue empty.")
        return

    resolved_port = port or int(os.environ.get("BSKY_QUEUE_PORT", "8765"))
    start_server(db_path, resolved_port)
    click.echo(
        f"Enqueue server on http://localhost:{resolved_port}/enqueue ; "
        f"posting next item every {interval}s. Ctrl-C to stop."
    )
    try:
        while True:
            # Peek the title before posting; process_one consumes the row.
            pending = next_pending(db_path)
            if process_one(db_path, session, _IMAGE_DIR, relogin=login):
                title = (pending or {}).get("title", "?")
                remaining = pending_count(db_path)
                click.echo(f'Posted: "{title}" — {remaining} left in queue.')
            time.sleep(interval)
    except KeyboardInterrupt:
        click.echo("\nStopped.")


if __name__ == "__main__":
    cli()
