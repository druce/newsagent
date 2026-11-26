"""Tests for lib/gui_bridge.py — the GUI ↔ channel hub."""
import threading

import pytest

from lib.gui_bridge import Hub


def test_message_flows_to_channel_and_reply_back():
    hub = Hub()
    m = hub.add_user_message("why 14 drops?", sid="S1", step="crossdedupe")
    assert m["status"] == "queued" and not hub.connected()
    ev = hub.take_events("c1", timeout=0.1)
    assert ev == [{"type": "channel", "msg_id": m["id"], "content": "why 14 drops?",
                   "meta": {"chat_id": str(m["id"]), "kind": "chat", "sid": "S1",
                            "step": "crossdedupe"}}]
    assert hub.connected()
    hub.add_reply(str(m["id"]), "Because they matched.")
    snap = hub.snapshot()
    assert [x["role"] for x in snap["messages"]] == ["user", "agent"]
    assert snap["messages"][0]["status"] == "answered"
    assert snap["messages"][1]["reply_to"] == m["id"]
    assert hub.snapshot(after=snap["last_id"])["messages"] == []


def test_content_differs_from_display_text():
    hub = Hub()
    hub.add_user_message("▶ Run next · Cluster", sid="S1", kind="command",
                         content="Run the cluster step")
    ev = hub.take_events("c1", timeout=0.1)[0]
    assert ev["content"] == "Run the cluster step" and ev["meta"]["kind"] == "command"


def test_long_poll_wakes_on_new_message():
    hub = Hub()
    got = []
    t = threading.Thread(target=lambda: got.extend(hub.take_events("c1", timeout=5)))
    t.start()
    hub.add_user_message("hi", sid=None)
    t.join(2)
    assert not t.is_alive() and got[0]["content"] == "hi" and "sid" not in got[0]["meta"]


def test_newest_client_wins():
    hub = Hub()
    old = []
    t = threading.Thread(target=lambda: old.extend(hub.take_events("old", 1.0, started_at=100)))
    t.start()
    assert hub.take_events("new", 0.05, started_at=200) == []
    hub.add_user_message("x", sid=None)
    t.join(3)
    assert old == []  # old poller woke and returned empty, didn't take "x"
    assert hub.take_events("old", 0.05, started_at=100) == []  # stale client can't reclaim
    assert [e["content"] for e in hub.take_events("new", 0.05, started_at=200)] == ["x"]


def test_permission_round_trip():
    hub = Hub()
    hub.add_permission("abcde", "Bash", "run tests", "pytest -q")
    snap = hub.snapshot()
    assert snap["permissions"][0]["status"] == "pending"
    hub.answer_permission("abcde", "allow")
    assert hub.take_events("c", timeout=0.05) == [
        {"type": "permission", "request_id": "abcde", "behavior": "allow"}]
    with pytest.raises(ValueError):
        hub.answer_permission("abcde", "deny")  # already answered
    with pytest.raises(KeyError):
        hub.answer_permission("zzzzz", "allow")
    hub.add_permission("q", "Bash", "", "")
    with pytest.raises(ValueError):
        hub.answer_permission("q", "maybe")
