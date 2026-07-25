"""Tests for lib/rating.py — Swiss pairing + Bradley-Terry math.

No LLM calls — uses synthetic battle outcomes.
"""
from dataclasses import dataclass
from typing import List as _List

import pytest

from lib.engines.base import EngineBlockedError, EngineError
from lib.rating import (
    _call_battle_with_retry,
    bradley_terry_from_battles,
    bradley_terry_scores,
    swiss_pairing,
)


@dataclass
class _FakeRanking:
    """Stand-in for the BATTLE_PROMPT output object (only `.ranking` is read)."""
    ranking: _List[str]


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


def test_battle_retry_returns_none_on_blocked_prompt(monkeypatch):
    """A content-filtered battle is unwinnable on retry — return None so the
    caller can drop that one battle instead of failing the whole rate step."""
    attempts = {"n": 0}

    def fake_call_prompt(name, item):
        attempts["n"] += 1
        raise EngineBlockedError("Google prompt blocked (block_reason=OTHER)")

    sleeps = []
    monkeypatch.setattr("lib.rating.call_prompt", fake_call_prompt)
    monkeypatch.setattr("lib.rating.time.sleep", lambda s: sleeps.append(s))

    assert _call_battle_with_retry({"items": [1]}) is None
    assert attempts["n"] == 1  # no retry — the block is deterministic
    assert sleeps == []


def test_bradley_terry_scores_survives_blocked_battles(monkeypatch):
    """One item whose content trips the provider's prompt filter must not kill
    the whole run: its battles are skipped, every other item still gets scored."""
    n = 12
    items = [{"id": f"id{i}", "title": f"t{i}", "summary": f"s{i}"} for i in range(n)]

    def fake_call_prompt(name, item):
        if any(x["id"] == "id7" for x in item["items"]):
            raise EngineBlockedError("Google prompt blocked (block_reason=OTHER)")
        ordered = sorted(item["items"], key=lambda x: int(x["id"][2:]))
        return _FakeRanking(ranking=[x["id"] for x in ordered])

    monkeypatch.setattr("lib.rating.call_prompt", fake_call_prompt)
    monkeypatch.setattr("lib.rating.time.sleep", lambda s: None)

    scores = bradley_terry_scores(items)

    assert set(scores) == {f"id{i}" for i in range(n)}
    assert all(isinstance(v, float) for v in scores.values())
    # Unblocked items still got a real ordering out of their battles.
    assert scores["id0"] > scores["id6"]


# ---------------------------------------------------------------------------
# bradley_terry_scores — convergence early-stop
# ---------------------------------------------------------------------------


def test_bradley_terry_scores_stops_early_when_converged(monkeypatch):
    """If the LLM judge returns a perfectly consistent ranking every round,
    rank positions stop changing after the first couple of rounds and we
    should break out well before max_rounds."""
    # 20 items, a stable identity ranking
    n = 20
    items = [{"id": f"id{i}", "title": f"t{i}", "summary": f"s{i}"} for i in range(n)]

    rounds_seen = []

    def fake_call_prompt(name, item):
        # Judge always ranks the input items in their original numeric order;
        # this means after round 1 the BT ordering matches forever.
        ordered = sorted(item["items"], key=lambda x: int(x["id"][2:]))
        return _FakeRanking(ranking=[x["id"] for x in ordered])

    monkeypatch.setattr("lib.rating.call_prompt", fake_call_prompt)
    monkeypatch.setattr("lib.rating.time.sleep", lambda s: None)

    def _cb(rnd, _max_rnd, _n_b, _avg):
        rounds_seen.append(rnd)

    scores = bradley_terry_scores(items, progress_callback=_cb)

    # max_rounds for n=20 with items_per_battle=6 is ceil(19/5) = 4, so min_rounds = max(3, 4//3) = 3.
    # Convergence can only fire after >min_rounds samples (i.e. round 4+). For
    # this tiny n the loop will likely just exhaust the round budget OR break
    # because Swiss pairing runs out of fresh pairs. The contract we verify is
    # weaker: it must not exceed max_rounds (no regression), it must produce
    # scores for every id, and ordering must be monotone with the judge.
    assert max(rounds_seen) <= 4  # max_rounds for n=20 / batch 6
    assert set(scores.keys()) == {item["id"] for item in items}
    # Lower-numbered ids win every battle → highest BT scores.
    ordered_ids = sorted(scores.keys(), key=lambda k: scores[k], reverse=True)
    assert ordered_ids[0] == "id0"
    assert ordered_ids[-1] == f"id{n - 1}"


def test_bradley_terry_scores_break_short_circuits_below_max(monkeypatch):
    """Use a larger n so max_rounds is big enough to actually test early-stop.
    With a perfectly stable judge, convergence should fire after min_rounds+1."""
    n = 60  # max_rounds = ceil(59/5) = 12, min_rounds = max(3, 12//3) = 4.
    items = [{"id": f"id{i:03d}", "title": "", "summary": ""} for i in range(n)]

    rounds_seen = []

    def fake_call_prompt(name, item):
        ordered = sorted(item["items"], key=lambda x: int(x["id"][2:]))
        return _FakeRanking(ranking=[x["id"] for x in ordered])

    monkeypatch.setattr("lib.rating.call_prompt", fake_call_prompt)
    monkeypatch.setattr("lib.rating.time.sleep", lambda s: None)

    def _cb(rnd, max_rnd, _n_b, _avg):
        rounds_seen.append((rnd, max_rnd))

    bradley_terry_scores(items, progress_callback=_cb)

    actual_max = rounds_seen[-1][0]
    budget = rounds_seen[-1][1]
    # Must run at least min_rounds+1 = 5 rounds (so >min_rounds samples exist
    # before convergence check runs), but must stop strictly before budget.
    assert actual_max >= 5
    assert actual_max < budget, (
        f"Expected early stop before max_rounds={budget}, got {actual_max}"
    )
