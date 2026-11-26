"""End-to-end: lib/gui_channel.py (as a stdio subprocess) ↔ GUI server ↔ page routes."""
import json
import queue
import subprocess
import sys
import threading
from contextlib import contextmanager

import httpx

from lib.db import init_db
from lib.state import NewsletterAgentState
from lib.steps.gui import start_server, write_bridge_file

SID = "2026-11-21-07-00-30"
TOKEN = "tok123"


@contextmanager
def _stack(tmp_db, tmp_path):
    init_db(tmp_db)
    st = NewsletterAgentState(session_id=SID, db_path=tmp_db)
    st.start_step("start"); st.complete_step("start", "ok"); st.save_checkpoint("start")
    server = start_server(tmp_db, 0, str(tmp_path), token=TOKEN)
    port = server.server_address[1]
    bridge = tmp_path / "bridge.json"
    write_bridge_file(port, TOKEN, bridge)
    proc = subprocess.Popen([sys.executable, "-m", "lib.gui_channel"],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, text=True, bufsize=1,
                            env={"NEWSAGENT_GUI_BRIDGE": str(bridge), "PATH": "/usr/bin"})
    lines: queue.Queue = queue.Queue()
    threading.Thread(target=lambda: [lines.put(json.loads(ln)) for ln in proc.stdout],
                     daemon=True).start()

    def send(obj):
        proc.stdin.write(json.dumps(obj) + "\n"); proc.stdin.flush()

    def recv(pred=lambda m: True, timeout=10):
        while True:
            m = lines.get(timeout=timeout)
            if pred(m):
                return m
    try:
        yield f"http://127.0.0.1:{port}", send, recv
    finally:
        proc.stdin.close(); proc.terminate(); proc.wait(5)
        server.shutdown()


def test_channel_round_trip(tmp_db, tmp_path):
    with _stack(tmp_db, tmp_path) as (base, send, recv):
        send({"jsonrpc": "2.0", "id": 1, "method": "initialize",
              "params": {"protocolVersion": "2026-07-28", "capabilities": {},
                         "clientInfo": {"name": "t", "version": "0"}}})
        init = recv(lambda m: m.get("id") == 1)["result"]
        assert init["protocolVersion"] != "2026-07-28"
        assert init["capabilities"]["experimental"] == {"claude/channel": {},
                                                        "claude/channel/permission": {}}
        assert "reply" in init["instructions"]
        send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        send({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        assert recv(lambda m: m.get("id") == 2)["result"]["tools"][0]["name"] == "reply"

        # page → session
        msg = httpx.post(base + "/api/chat", json={"sid": SID, "text": "hello", "step": "rate"}).json()
        note = recv(lambda m: m.get("method") == "notifications/claude/channel")
        assert note["params"] == {"content": "hello", "meta": {
            "chat_id": str(msg["id"]), "kind": "chat", "sid": SID, "step": "rate"}}
        snap = httpx.get(base + "/api/chat").json()
        assert snap["connected"] and snap["messages"][0]["status"] == "delivered"

        # session → page
        send({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {
            "name": "reply", "arguments": {"chat_id": str(msg["id"]), "text": "hi back"}}})
        assert recv(lambda m: m.get("id") == 3)["result"]["content"][0]["text"] == "sent"
        msgs = httpx.get(base + "/api/chat").json()["messages"]
        assert msgs[-1]["role"] == "agent" and msgs[-1]["text"] == "hi back"

        # permission relay
        send({"jsonrpc": "2.0", "method": "notifications/claude/channel/permission_request",
              "params": {"request_id": "abcde", "tool_name": "Bash",
                         "description": "run x", "input_preview": "ls"}})
        for _ in range(50):
            perms = httpx.get(base + "/api/chat").json()["permissions"]
            if perms:
                break
            threading.Event().wait(0.1)
        assert perms[0]["request_id"] == "abcde" and perms[0]["status"] == "pending"
        assert httpx.post(base + "/api/permission",
                          json={"request_id": "abcde", "behavior": "allow"}).status_code == 200
        verdict = recv(lambda m: m.get("method") == "notifications/claude/channel/permission")
        assert verdict["params"] == {"request_id": "abcde", "behavior": "allow"}

        # unknown method → error, server keeps going
        send({"jsonrpc": "2.0", "id": 4, "method": "nope"})
        assert recv(lambda m: m.get("id") == 4)["error"]["code"] == -32601


def test_bridge_requires_token(tmp_db, tmp_path):
    init_db(tmp_db)
    server = start_server(tmp_db, 0, str(tmp_path), token=TOKEN)
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        assert httpx.get(base + "/bridge/poll?timeout=0").status_code == 403
        assert httpx.post(base + "/bridge/reply", json={"text": "x"},
                          headers={"X-Bridge-Token": "wrong"}).status_code == 403
        assert httpx.post(base + "/api/chat", json={"text": "x"},
                          headers={"Origin": "http://evil.example"}).status_code == 403
    finally:
        server.shutdown()
