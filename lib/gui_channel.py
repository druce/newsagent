"""Claude Code channel: bridges the pipeline GUI into an interactive session.

Claude Code spawns this as a stdio MCP server (see tools/gui-channel.mcp.json
and tools/claude-gui.sh, which starts `claude` with
`--dangerously-load-development-channels server:newsagent-gui`).

  GUI page ──POST──▶ GUI server (lib/steps/gui.py, Hub) ◀──long-poll── this process
                                                         ──stdio──▶ Claude Code

- Events from the GUI become `notifications/claude/channel` messages; the
  model sees <channel source="newsagent-gui" chat_id=.. kind=.. sid=.. step=..>.
- The model answers with the `reply` tool, which this process POSTs back.
- Permission prompts (`notifications/claude/channel/permission_request`) are
  forwarded to the GUI; the user's Allow/Deny comes back as an event and is
  sent as `notifications/claude/channel/permission`.

Minimal hand-rolled MCP (newline-delimited JSON-RPC over stdio, stdlib only).
It answers `initialize` with a pre-2026-07-28 protocol revision because
servers on that revision can't deliver channel messages. stdout is the
protocol; diagnostics go to stderr.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Callable

SERVER_NAME = "newsagent-gui"
_SUPPORTED_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
BRIDGE_FILE = Path(__file__).resolve().parent.parent / ".gui-bridge.json"

INSTRUCTIONS = """\
Messages from the newsagent pipeline GUI (a local web page the user is looking at) \
arrive as <channel source="newsagent-gui" chat_id="…" kind="chat|command" sid="…" step="…">. \
The user cannot see your terminal output — everything they should read must go through \
the `reply` tool (pass the chat_id from the tag; markdown is fine; keep it concise).
- kind="chat": the user typed in the GUI chat. `sid` is the session they are viewing and \
`step` (if present) is the workflow step they attached. Answer by calling reply. If they \
ask you to do something, do it as if they had typed it here, then reply with the result.
- kind="command": the user pressed a GUI button (Run next / Run all / Stop). Carry it out \
exactly as the message says, using the newsagent skills and their rules, then reply.
Several messages may arrive together if you were busy; handle them in order."""

REPLY_TOOL = {
    "name": "reply",
    "description": "Send a message to the user's newsagent pipeline GUI chat pane. "
                   "Use this to answer every <channel source=\"newsagent-gui\"> message.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "chat_id": {"type": "string",
                        "description": "chat_id attribute of the message you are answering"},
            "text": {"type": "string", "description": "Message text (markdown ok)"},
        },
        "required": ["text"],
    },
}


def log(msg: str) -> None:
    print(f"[{SERVER_NAME}] {msg}", file=sys.stderr, flush=True)


class BridgeClient:
    """HTTP client for the GUI server's /bridge routes (address from .gui-bridge.json)."""

    def __init__(self, bridge_file: Path = BRIDGE_FILE) -> None:
        self.bridge_file = bridge_file
        self.client_id = uuid.uuid4().hex[:12]
        self.started = time.time()

    def _cfg(self) -> dict[str, str]:
        return json.loads(self.bridge_file.read_text())

    def _req(self, method: str, path: str, body: dict | None = None,
             timeout: float = 10.0) -> Any:
        cfg = self._cfg()  # re-read each call: the GUI may restart with a new token
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(cfg["url"] + path, data=data, method=method,
                                     headers={"X-Bridge-Token": cfg["token"],
                                              "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read() or b"{}")

    def poll(self, timeout: float = 25.0) -> list[dict]:
        path = (f"/bridge/poll?client={self.client_id}&started={self.started}"
                f"&timeout={timeout}")
        return self._req("GET", path, timeout=timeout + 10).get("events", [])

    def reply(self, chat_id: str | None, text: str) -> None:
        self._req("POST", "/bridge/reply", {"chat_id": chat_id, "text": text})

    def permission(self, params: dict) -> None:
        self._req("POST", "/bridge/permission", params)


class ChannelServer:
    def __init__(self, bridge: BridgeClient, out=None) -> None:
        self.bridge = bridge
        self.out = out or sys.stdout
        self._lock = threading.Lock()
        self.ready = threading.Event()  # set once the client sent `initialized`

    # ---------------------------------------------------------------- stdio
    def send(self, obj: dict) -> None:
        line = json.dumps(obj, ensure_ascii=False)
        with self._lock:
            self.out.write(line + "\n")
            self.out.flush()

    def notify(self, method: str, params: dict) -> None:
        self.send({"jsonrpc": "2.0", "method": method, "params": params})

    def _result(self, mid: Any, result: dict) -> None:
        self.send({"jsonrpc": "2.0", "id": mid, "result": result})

    def _error(self, mid: Any, code: int, message: str) -> None:
        self.send({"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}})

    # ---------------------------------------------------------------- dispatch
    def handle(self, msg: dict) -> None:
        method, mid = msg.get("method"), msg.get("id")
        params = msg.get("params") or {}
        if method is None:
            return  # a response to something we sent; we never send requests
        handler: Callable | None = {
            "initialize": self._initialize,
            "ping": lambda p: {},
            "tools/list": lambda p: {"tools": [REPLY_TOOL]},
            "tools/call": self._call_tool,
        }.get(method)
        if mid is None:  # notification
            if method == "notifications/initialized":
                self.ready.set()
            elif method == "notifications/claude/channel/permission_request":
                self._forward_permission(params)
            return
        if handler is None:
            self._error(mid, -32601, f"method not found: {method}")
            return
        try:
            self._result(mid, handler(params))
        except Exception as exc:  # report, never crash the server
            self._error(mid, -32603, str(exc))

    def _initialize(self, params: dict) -> dict:
        requested = params.get("protocolVersion", "")
        version = requested if requested in _SUPPORTED_VERSIONS else _SUPPORTED_VERSIONS[0]
        return {
            "protocolVersion": version,
            "capabilities": {
                "experimental": {"claude/channel": {}, "claude/channel/permission": {}},
                "tools": {},
            },
            "serverInfo": {"name": SERVER_NAME, "version": "0.1.0"},
            "instructions": INSTRUCTIONS,
        }

    def _call_tool(self, params: dict) -> dict:
        if params.get("name") != "reply":
            raise ValueError(f"unknown tool {params.get('name')!r}")
        args = params.get("arguments") or {}
        text = str(args.get("text", ""))
        try:
            self.bridge.reply(args.get("chat_id"), text)
        except (OSError, ValueError, KeyError) as exc:
            return {"content": [{"type": "text",
                                 "text": f"GUI not reachable, reply not delivered: {exc}"}],
                    "isError": True}
        return {"content": [{"type": "text", "text": "sent"}]}

    def _forward_permission(self, params: dict) -> None:
        try:
            self.bridge.permission({k: params.get(k, "") for k in
                                    ("request_id", "tool_name", "description",
                                     "input_preview")})
        except (OSError, ValueError, KeyError) as exc:
            log(f"could not forward permission request: {exc}")

    def deliver(self, event: dict) -> None:
        if event.get("type") == "permission":
            self.notify("notifications/claude/channel/permission",
                        {"request_id": event["request_id"], "behavior": event["behavior"]})
        elif event.get("type") == "channel":
            meta = {k: str(v) for k, v in (event.get("meta") or {}).items()
                    if k.replace("_", "").isalnum()}
            self.notify("notifications/claude/channel",
                        {"content": event.get("content", ""), "meta": meta})

    # ---------------------------------------------------------------- loops
    def poll_forever(self, stop: threading.Event) -> None:
        self.ready.wait()
        backoff = 1.0
        while not stop.is_set():
            try:
                for event in self.bridge.poll():
                    self.deliver(event)
                backoff = 1.0
            except (OSError, ValueError, KeyError) as exc:
                # GUI not running / restarted: retry quietly
                if backoff == 1.0:
                    log(f"GUI bridge unavailable ({exc}); retrying")
                stop.wait(backoff)
                backoff = min(backoff * 2, 10.0)

    def serve(self, inp=None) -> None:
        inp = inp or sys.stdin
        stop = threading.Event()
        threading.Thread(target=self.poll_forever, args=(stop,), daemon=True).start()
        for line in inp:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                log(f"bad json: {line[:200]}")
                continue
            if isinstance(msg, dict):
                self.handle(msg)
        stop.set()


def main() -> None:
    if os.environ.get("NEWSAGENT_GUI_BRIDGE"):
        bridge = BridgeClient(Path(os.environ["NEWSAGENT_GUI_BRIDGE"]))
    else:
        bridge = BridgeClient()
    ChannelServer(bridge).serve()


if __name__ == "__main__":
    main()
