"""Tests for lib/rating.py — Swiss pairing + Bradley-Terry math.

No LLM calls — uses synthetic battle outcomes.
"""
import pytest
from lib.engines.base import EngineError
from lib.rating import (
    _call_battle_with_retry,
    bradley_terry_from_battles,
    swiss_pairing,
)


def test_swiss_pairing_pairs_adjacent_by_score():
    items = [{"id": "a"}, {"id": "b"}, {"id": "c"}, {"id": "d"}]
    scores = {"a": 4.0, "b": 3.0, "c": 2.0, "d": 1.0}
    pairs = swiss_pairing(items, battle_history=set(), current_scores=scores)
    assert pairs == [("a", "b"), ("c", "d")]


def test_swiss_pairing_skips_battled_pairs():
    items = [{"id": "a"}, {"id": "b"}, {"id": "c"}]
    scores = {"a": 3, "b": 2, "c": 1}
    history = {("a", "b"), ("b", "a")}
    pairs = swiss_pairing(items, battle_history=history, current_scores=scores)
    assert ("a", "b") not in pairs and ("b", "a") not in pairs


def test_swiss_pairing_empty_items():
    pairs = swiss_pairing([], battle_history=set(), current_scores={})
    assert pairs == []


def test_swiss_pairing_single_item():
    items = [{"id": "a"}]
    pairs = swiss_pairing(items, battle_history=set(), current_scores={"a": 1.0})
    assert pairs == []


def test_swiss_pairing_all_battled_returns_empty():
    items = [{"id": "a"}, {"id": "b"}]
    scores = {"a": 1.0, "b": 0.5}
    history = {("a", "b"), ("b", "a")}
    pairs = swiss_pairing(items, battle_history=history, current_scores=scores)
    assert pairs == []


def test_bradley_terry_from_battles_ranks_winners_higher():
    items = [{"id": "a"}, {"id": "b"}, {"id": "c"}]
    # a beats b 5 times, a beats c 3 times, b beats c 2 times
    battles = [("a", "b")] * 5 + [("a", "c")] * 3 + [("b", "c")] * 2
    scores = bradley_terry_from_battles([i["id"] for i in items], battles)
    assert scores["a"] > scores["b"] > scores["c"]


def test_bradley_terry_from_battles_returns_dict_keyed_by_id():
    ids = ["x", "y", "z"]
    battles = [("x", "y"), ("x", "z"), ("y", "z")]
    scores = bradley_terry_from_battles(ids, battles)
    assert set(scores.keys()) == {"x", "y", "z"}
    for v in scores.values():
        assert isinstance(v, float)


def test_bradley_terry_from_battles_two_items():
    ids = ["winner", "loser"]
    battles = [("winner", "loser")] * 3
    scores = bradley_terry_from_battles(ids, battles)
    assert scores["winner"] > scores["loser"]


# ---------------------------------------------------------------------------
# _call_battle_with_retry
# ---------------------------------------------------------------------------


def test_battle_retry_returns_on_first_success(monkeypatch):
    calls = []

    def fake_call_prompt(name, item):
        calls.append(item)
        return "ok"

    monkeypatch.setattr("lib.rating.call_prompt", fake_call_prompt)
    monkeypatch.setattr("lib.rating.time.sleep", lambda s: None)

    assert _call_battle_with_retry({"items": [1]}) == "ok"
    assert len(calls) == 1


def test_battle_retry_retries_on_5xx_then_succeeds(monkeypatch):
    attempts = {"n": 0}

    def fake_call_prompt(name, item):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise EngineError("Google API error: 503 UNAVAILABLE")
        return "ok"

    sleeps = []
    monkeypatch.setattr("lib.rating.call_prompt", fake_call_prompt)
    monkeypatch.setattr("lib.rating.time.sleep", lambda s: sleeps.append(s))

    assert _call_battle_with_retry({"items": [1]}) == "ok"
    assert attempts["n"] == 3
    assert sleeps == [60, 60]


def test_battle_retry_gives_up_after_max_retries(monkeypatch):
    attempts = {"n": 0}

    def fake_call_prompt(name, item):
        attempts["n"] += 1
        raise EngineError("Google API error: 503 UNAVAILABLE")

    monkeypatch.setattr("lib.rating.call_prompt", fake_call_prompt)
    monkeypatch.setattr("lib.rating.time.sleep", lambda s: None)

    with pytest.raises(EngineError, match="503"):
        _call_battle_with_retry({"items": [1]})
    assert attempts["n"] == 3  # 1 initial + 2 retries


def test_battle_retry_does_not_retry_on_4xx(monkeypatch):
    attempts = {"n": 0}

    def fake_call_prompt(name, item):
        attempts["n"] += 1
        raise EngineError("Google API error: 400 BAD REQUEST")

    sleeps = []
    monkeypatch.setattr("lib.rating.call_prompt", fake_call_prompt)
    monkeypatch.setattr("lib.rating.time.sleep", lambda s: sleeps.append(s))

    with pytest.raises(EngineError, match="400"):
        _call_battle_with_retry({"items": [1]})
    assert attempts["n"] == 1
    assert sleeps == []
