"""Pipeline GUI: live workflow diagram + chat/controls into a Claude Code session.

Three panes: the 14-step workflow diagram (with the draft/rewrite critic loop),
a chat pane, and an output/log pane. Each node opens a pop-up with Doc / Code /
Log / State tabs. The page polls JSON every 2 s.

Phase 2: chat and the ▶ / ▶▶ buttons reach the user's interactive Claude Code
session through a channel (lib/gui_channel.py, started by Claude Code via
tools/claude-gui.sh). The channel long-polls this server's /bridge/* routes,
authenticated with the token in .gui-bridge.json (written at startup). ■ Stop
SIGTERMs the running step and tells the agent; ⟲ Reset runs lib.steps.start
directly and switches the page to the new session. Localhost only.

Page routes:
  GET  /                                  the page (lib/gui_page.html)
  GET  /api/sessions | /api/session?sid= | /api/step?sid=&step=&tab= | /api/log?...
  GET  /api/chat?after=N                  chat log, pending permissions, connected
  POST /api/chat {sid, text, step?}       send a chat message to the session
  POST /api/action {sid, action}          run_next | run_all | stop | reset
  POST /api/permission {request_id, behavior}
Channel routes (X-Bridge-Token):
  GET  /bridge/poll?client=&started=&timeout=
  POST /bridge/reply {chat_id, text}
  POST /bridge/permission {request_id, tool_name, description, input_preview}

    .venv/bin/python -m lib.steps.gui                 # http://127.0.0.1:8767/
"""
from __future__ import annotations

import json
import os
import re
import secrets
import signal
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.parse import parse_qs, urlparse

import click

from lib.db import init_db
from lib.gui import (REPO_ROOT, GuiError, artifact_file, list_sessions, session_overview,
                     step_artifacts, step_code, step_doc, step_log, step_output,
                     step_state)
from lib.gui_bridge import Hub
from lib.steplog import running_marker

_DEFAULT_PORT = 8767
_PAGE = Path(__file__).resolve().parent.parent / "gui_page.html"
BRIDGE_FILE = REPO_ROOT / ".gui-bridge.json"
_MAX_BODY = 200_000


def command_content(action: str, sid: str, step: str | None) -> tuple[str, str]:
    """(chat-pane label, text sent to the model) for a button action."""
    if action == "run_next":
        return (f"▶ Run next · {step}",
                f"The user pressed ▶ Run next in the pipeline GUI. Run the `{step}` step "
                f"for session {sid} by invoking the newsagent:{step} skill with "
                f"`--session {sid}`, following that skill exactly (dispatch agents where "
                "it says to). Run only this one step. When it finishes, call the reply "
                "tool with a one- or two-line result.")
    if action == "run_all":
        return ("▶▶ Run all",
                f"The user pressed ▶▶ Run all in the pipeline GUI. Resume session {sid} "
                f"to the end by invoking the newsagent:pipeline skill with `--resume {sid}`. "
                "Call the reply tool with a short status after each step completes and "
                "when the run finishes or stops on an error.")
    if action == "stop":
        what = f"The running `{step}` step process was sent SIGTERM. " if step else ""
        return ("■ Stop",
                f"The user pressed ■ Stop in the pipeline GUI for session {sid}. {what}"
                "Do not start any further steps or dispatch more agents. Wrap up, then "
                "call the reply tool saying what state the session is in.")
    raise GuiError(f"unknown action {action!r}")


class _GuiHandler(BaseHTTPRequestHandler):
    db_path = ""
    runs_root = "runs"
    default_session: str | None = None
    hub: Hub
    token = ""

    # ---------------------------------------------------------------- io
    def _send(self, status: int, body: str, content_type: str) -> None:
        payload = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def _json(self, status: int, obj) -> None:
        self._send(status, json.dumps(obj, default=str), "application/json")

    def _same_origin(self) -> bool:
        """Refuse browser requests from other sites (no auth on page routes)."""
        origin = self.headers.get("Origin")
        if not origin:
            return True  # non-browser client (curl, tests)
        return urlparse(origin).netloc == (self.headers.get("Host") or "")

    def _read_json(self) -> dict:
        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if ctype != "application/json":
            # Forces a CORS preflight for cross-site fetches, which we never answer.
            raise GuiError("Content-Type must be application/json")
        length = int(self.headers.get("Content-Length") or 0)
        if length > _MAX_BODY:
            raise GuiError("body too large")
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, UnicodeDecodeError):
            raise GuiError("body must be JSON") from None
        if not isinstance(body, dict):
            raise GuiError("body must be a JSON object")
        return body

    def _bridge_ok(self) -> bool:
        return bool(self.token) and secrets.compare_digest(
            self.headers.get("X-Bridge-Token") or "", self.token)

    # ---------------------------------------------------------------- GET
    def do_GET(self) -> None:  # noqa: N802 — http.server API
        url = urlparse(self.path)
        qs = {k: v[0] for k, v in parse_qs(url.query).items()}
        try:
            if url.path.startswith("/bridge/"):
                if not self._bridge_ok():
                    self._json(403, {"error": "bad bridge token"})
                elif url.path == "/bridge/poll":
                    events = self.hub.take_events(
                        qs.get("client", ""), min(float(qs.get("timeout", "25")), 60.0),
                        float(qs.get("started", "0") or 0))
                    self._json(200, {"events": events})
                else:
                    self._json(404, {"error": "not found"})
                return
            if url.path == "/":
                # null → the page picks the newest session from /api/sessions
                html = _PAGE.read_text().replace(
                    "__DEFAULT_SESSION__", json.dumps(self.default_session))
                self._send(200, html, "text/html; charset=utf-8")
            elif url.path == "/api/sessions":
                self._json(200, list_sessions(self.db_path))
            elif url.path == "/api/session":
                self._json(200, session_overview(self.db_path, qs.get("sid", ""),
                                                 self.runs_root))
            elif url.path == "/api/step":
                self._step(qs)
            elif url.path == "/api/log":
                self._json(200, step_log(qs.get("sid", ""), qs.get("step", ""),
                                         int(qs.get("offset", "0") or 0),
                                         self.runs_root))
            elif url.path == "/api/chat":
                self._json(200, self.hub.snapshot(int(qs.get("after", "0") or 0)))
            elif url.path == "/api/file":
                self._file(qs)
            else:
                self._json(404, {"error": "not found"})
        except (GuiError, ValueError) as exc:
            self._json(400, {"error": str(exc)})

    def _file(self, qs: dict) -> None:
        """One artifact file. HTML is served under a CSP sandbox (opaque origin, no
        scripts) so scraped pages can't act as this origin."""
        path, size = artifact_file(self.db_path, qs.get("sid", ""), qs.get("step", ""),
                                   qs.get("path", ""), self.runs_root)
        if size > 20_000_000:
            raise GuiError("file too large to view")
        ext = path.suffix.lower()
        ctype = {".html": "text/html", ".htm": "text/html", ".json": "application/json",
                 ".md": "text/plain", ".txt": "text/plain", ".log": "text/plain",
                 ".csv": "text/plain"}.get(ext, "text/plain")
        payload = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", f"{ctype}; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        # no scripts, no fetch/XHR/frames; cached pages may still pull styles/images/fonts
        self.send_header("Content-Security-Policy",
                         "sandbox; default-src 'none'; style-src * 'unsafe-inline'; "
                         "img-src * data:; font-src * data:")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(payload)

    def _step(self, qs: dict) -> None:
        tab, step = qs.get("tab", ""), qs.get("step", "")
        if tab == "doc":
            self._json(200, step_doc(step))
        elif tab == "code":
            self._json(200, step_code(step))
        elif tab == "state":
            self._json(200, step_state(self.db_path, qs.get("sid", ""), step,
                                       self.runs_root))
        elif tab == "output":
            self._json(200, step_output(qs.get("sid", ""), step, self.runs_root))
        elif tab == "artifacts":
            self._json(200, step_artifacts(self.db_path, qs.get("sid", ""), step,
                                           self.runs_root))
        else:
            raise GuiError(f"unknown tab {tab!r}")

    # ---------------------------------------------------------------- POST
    def do_POST(self) -> None:  # noqa: N802 — http.server API
        url = urlparse(self.path)
        try:
            if url.path.startswith("/bridge/"):
                if not self._bridge_ok():
                    self._json(403, {"error": "bad bridge token"})
                    return
                body = self._read_json()
                if url.path == "/bridge/reply":
                    self._json(200, self.hub.add_reply(body.get("chat_id"),
                                                       str(body.get("text", ""))))
                elif url.path == "/bridge/permission":
                    self.hub.add_permission(str(body["request_id"]),
                                            str(body.get("tool_name", "")),
                                            str(body.get("description", "")),
                                            str(body.get("input_preview", "")))
                    self._json(200, {"ok": True})
                else:
                    self._json(404, {"error": "not found"})
                return
            if not self._same_origin():
                self._json(403, {"error": "cross-origin request refused"})
                return
            body = self._read_json()
            if url.path == "/api/chat":
                text = str(body.get("text", "")).strip()
                if not text:
                    raise GuiError("empty message")
                self._json(200, self.hub.add_user_message(
                    text, body.get("sid") or None, body.get("step") or None))
            elif url.path == "/api/action":
                self._action(body)
            elif url.path == "/api/permission":
                try:
                    self.hub.answer_permission(str(body.get("request_id", "")),
                                               str(body.get("behavior", "")))
                except KeyError:
                    raise GuiError("unknown permission request") from None
                self._json(200, {"ok": True})
            else:
                self._json(404, {"error": "not found"})
        except KeyError as exc:
            self._json(400, {"error": f"missing field {exc}"})
        except (GuiError, ValueError) as exc:
            self._json(400, {"error": str(exc)})

    def _action(self, body: dict) -> None:
        action, sid = str(body.get("action", "")), str(body.get("sid", ""))
        if action == "reset":
            new_sid = self._new_session()
            self.hub.add_system(f"⟲ Started new session {new_sid}")
            self._json(200, {"sid": new_sid})
            return
        ov = session_overview(self.db_path, sid, self.runs_root)
        if action in ("run_next", "run_all"):
            if not self.hub.connected():
                raise GuiError("no Claude Code session connected (start it with "
                               "tools/claude-gui.sh)")
            if ov["running"]:
                raise GuiError(f"{ov['running']} is already running")
            if ov["next"] is None:
                raise GuiError("session is complete")
            label, content = command_content(action, sid, ov["next"])
            self._json(200, self.hub.add_user_message(label, sid, ov["next"], "command",
                                                      content))
        elif action == "stop":
            step = ov["running"]
            killed = self._kill_step(sid, step) if step else False
            label, content = command_content("stop", sid, step if killed else None)
            msg = self.hub.add_user_message(label, sid, step, "command", content)
            self._json(200, {**msg, "killed": killed, "step": step})
        else:
            raise GuiError(f"unknown action {action!r}")

    def _kill_step(self, sid: str, step: str) -> bool:
        try:
            pid = int(running_marker(self.runs_root, sid, step).read_text().strip())
            os.kill(pid, signal.SIGTERM)
            return True
        except (FileNotFoundError, ValueError, ProcessLookupError, PermissionError):
            return False

    def _new_session(self) -> str:
        res = subprocess.run(
            [sys.executable, "-m", "lib.steps.start", "--db", self.db_path],
            capture_output=True, text=True, timeout=120, cwd=REPO_ROOT)
        m = re.search(r"Session created: (\S+)", res.stdout)
        if res.returncode != 0 or not m:
            raise GuiError("start failed: " + (res.stderr or res.stdout)[-500:])
        return m.group(1)

    def log_message(self, format, *args) -> None:  # silence default stderr logging
        pass


def make_handler(db_path: str, runs_root: str = "runs", session: str | None = None,
                 hub: Hub | None = None, token: str = ""):
    return type("BoundGuiHandler", (_GuiHandler,),
                {"db_path": db_path, "runs_root": runs_root,
                 "default_session": session, "hub": hub or Hub(), "token": token})


def bridge_owner(path: Path = BRIDGE_FILE) -> int | None:
    """PID of another live GUI server that owns the channel bridge file, if any."""
    try:
        pid = int(json.loads(path.read_text()).get("pid"))
    except (FileNotFoundError, ValueError, TypeError):
        return None
    if pid == os.getpid():
        return None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return None
    except PermissionError:
        pass
    return pid


def write_bridge_file(port: int, token: str, path: Path = BRIDGE_FILE) -> None:
    path.write_text(json.dumps({"url": f"http://127.0.0.1:{port}", "token": token,
                                "pid": os.getpid()}))
    path.chmod(0o600)


def start_server(db_path: str, port: int, runs_root: str = "runs",
                 session: str | None = None, token: str = "") -> ThreadingHTTPServer:
    """Start the GUI server on a daemon thread; return the server (tests)."""
    httpd = ThreadingHTTPServer(("127.0.0.1", port),
                                make_handler(db_path, runs_root, session, token=token))
    httpd.daemon_threads = True
    Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


@click.command()
@click.option("--port", default=None, type=int,
              help=f"Port (default $GUI_PORT or {_DEFAULT_PORT}).")
@click.option("--db", "db_path", default="newsletter_agent.db", show_default=True)
@click.option("--session", default=None,
              help="Session to open (default: most recently updated).")
def cli(port: int | None, db_path: str, session: str | None) -> None:
    """Serve the pipeline GUI."""
    init_db(db_path)
    resolved = port or int(os.environ.get("GUI_PORT", str(_DEFAULT_PORT)))
    token = secrets.token_urlsafe(24)
    httpd = ThreadingHTTPServer(("127.0.0.1", resolved),
                                make_handler(db_path, "runs", session, token=token))
    httpd.daemon_threads = True
    def _term(*_):  # SIGTERM → same clean shutdown as Ctrl-C (removes our bridge file)
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, _term)
    owner = bridge_owner()
    if owner is None:
        write_bridge_file(resolved, token)
    click.echo(f"Pipeline GUI on http://127.0.0.1:{resolved}/  Ctrl-C to stop.")
    if owner is None:
        click.echo("Connect a Claude Code session with: tools/claude-gui.sh")
    else:
        click.echo(f"Note: another GUI server (pid {owner}) owns the Claude Code channel "
                   "bridge; chat and Run buttons stay with that server.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        click.echo("\nStopped.")
    finally:
        httpd.server_close()
        try:
            if json.loads(BRIDGE_FILE.read_text()).get("pid") == os.getpid():
                BRIDGE_FILE.unlink()
        except (FileNotFoundError, ValueError):
            pass


if __name__ == "__main__":
    cli()
