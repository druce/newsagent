"""In-memory hub between the GUI page and the Claude Code channel (phase 2).

The GUI server owns one `Hub`. The page posts chat messages and button
actions into it; the channel process (lib/gui_channel.py, spawned by Claude
Code) long-polls `take_events` and pushes each event into the session as a
`notifications/claude/channel` message, posts the model's `reply` tool calls
back via `add_reply`, and forwards permission prompts via `add_permission`.

Only the most recently connected channel client receives events, so a stale
second session can't swallow the user's messages. Nothing is persisted: the
chat log lives as long as the GUI server.
"""
from __future__ import annotations

import itertools
import threading
import time
from typing import Any

CONNECTED_WINDOW_S = 40.0  # a channel counts as connected if it polled this recently
PERMISSION_STALE_S = 600.0


class Hub:
    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._ids = itertools.count(1)
        self.messages: list[dict[str, Any]] = []
        self._events: list[dict[str, Any]] = []
        self.permissions: dict[str, dict[str, Any]] = {}
        self.client_id: str | None = None
        self.client_started: float = 0.0
        self.last_poll: float = 0.0

    # ---------------------------------------------------------------- status
    def connected(self) -> bool:
        return self.client_id is not None and time.time() - self.last_poll < CONNECTED_WINDOW_S

    # ---------------------------------------------------------------- page side
    def add_user_message(self, text: str, sid: str | None, step: str | None = None,
                         kind: str = "chat", content: str | None = None) -> dict[str, Any]:
        """Record a user chat line or button action and queue it for the channel.

        `text` is what the chat pane shows; `content` (default: text) is what
        the model receives.
        """
        with self._cond:
            mid = next(self._ids)
            msg = {"id": mid, "role": "user", "kind": kind, "text": text, "sid": sid,
                   "step": step, "ts": time.time(), "status": "queued"}
            self.messages.append(msg)
            meta = {"chat_id": str(mid), "kind": kind}
            if sid:
                meta["sid"] = sid
            if step:
                meta["step"] = step
            self._events.append({"type": "channel", "msg_id": mid,
                                 "content": content if content is not None else text,
                                 "meta": meta})
            self._cond.notify_all()
            return dict(msg)

    def add_system(self, text: str) -> dict[str, Any]:
        with self._cond:
            msg = {"id": next(self._ids), "role": "system", "text": text,
                   "ts": time.time(), "status": "done"}
            self.messages.append(msg)
            return dict(msg)

    def answer_permission(self, request_id: str, behavior: str) -> None:
        if behavior not in ("allow", "deny"):
            raise ValueError("behavior must be 'allow' or 'deny'")
        with self._cond:
            p = self.permissions.get(request_id)
            if p is None:
                raise KeyError(request_id)
            if p["status"] != "pending":
                raise ValueError(f"permission {request_id} already {p['status']}")
            p["status"] = behavior
            self._events.append({"type": "permission", "request_id": request_id,
                                 "behavior": behavior})
            self._cond.notify_all()

    def snapshot(self, after: int = 0) -> dict[str, Any]:
        now = time.time()
        with self._cond:
            msgs = [dict(m) for m in self.messages if m["id"] > after]
            perms = []
            for rid, p in self.permissions.items():
                q = dict(p, request_id=rid)
                if q["status"] == "pending" and now - q["ts"] > PERMISSION_STALE_S:
                    q["status"] = "stale"
                perms.append(q)
        return {"messages": msgs, "permissions": perms, "connected": self.connected(),
                "last_id": msgs[-1]["id"] if msgs else after}

    # ---------------------------------------------------------------- channel side
    def take_events(self, client_id: str, timeout: float = 25.0,
                    started_at: float = 0.0) -> list[dict[str, Any]]:
        """Long-poll: block up to `timeout` s for events.

        The channel process that started most recently (`started_at`) owns the
        queue; an older one (a stale second session) just idles out its polls.
        """
        deadline = time.time() + timeout
        with self._cond:
            if client_id != self.client_id and started_at >= self.client_started:
                self.client_id, self.client_started = client_id, started_at
                self._cond.notify_all()  # wake the previous owner's poll
            while True:
                if client_id != self.client_id:
                    remaining = deadline - time.time()
                    if remaining > 0:
                        self._cond.wait(remaining)
                    return []
                self.last_poll = time.time()
                if self._events:
                    events, self._events = self._events, []
                    for e in events:
                        if e["type"] == "channel":
                            for m in self.messages:
                                if m["id"] == e["msg_id"]:
                                    m["status"] = "delivered"
                    return events
                remaining = deadline - time.time()
                if remaining <= 0:
                    return []
                self._cond.wait(remaining)

    def add_reply(self, chat_id: str | None, text: str) -> dict[str, Any]:
        with self._cond:
            reply_to = int(chat_id) if chat_id and str(chat_id).isdigit() else None
            msg = {"id": next(self._ids), "role": "agent", "text": text,
                   "reply_to": reply_to, "ts": time.time(), "status": "done"}
            self.messages.append(msg)
            for m in self.messages:
                if reply_to is not None and m["id"] == reply_to:
                    m["status"] = "answered"
            return dict(msg)

    def add_permission(self, request_id: str, tool_name: str, description: str,
                       input_preview: str) -> None:
        with self._cond:
            self.permissions[request_id] = {
                "tool_name": tool_name, "description": description,
                "input_preview": input_preview, "ts": time.time(), "status": "pending"}
