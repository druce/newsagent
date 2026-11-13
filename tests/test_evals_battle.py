"""Tests for evals.run.battle — battle_prompt model comparison against an Opus gold ranking."""
import json
import subprocess
import sys
from collections import Counter

import pytest

from evals.run import battle as B

TYPES = ("q1_top", "q2", "q3", "q4_bottom", "mixed")


def _case(t, g, ids=("a", "b", "c", "d", "e", "f")):
    return {"case_id": f"battle_prompt:s:rate:{t}-{g}", "prompt": "battle_prompt",
            "input": {"items": [{"id": i, "title": f"T{i}", "summary": f"S{i}"} for i in ids]},
            "prod_output": {"ranking": list(ids)}, "strata": {"group_type": t}}


def _cases(per_type=100):
    return [_case(t, g) for t in TYPES for g in range(per_type)]


def test_select_gold_groups_is_stratified_and_deterministic():
    sel = B.select_gold_groups(_cases(), per_type=80, seed=0)
    assert len(sel) == 400
    assert Counter(c["strata"]["group_type"] for c in sel) == {t: 80 for t in TYPES}
    assert [c["case_id"] for c in sel] == [c["case_id"] for c in B.select_gold_groups(_cases(), 80, 0)]
    assert len({c["case_id"] for c in sel}) == 400


def test_concordance_metrics():
    gold = ["a", "b", "c", "d"]
    assert B.pairwise_agreement(gold, gold) == 1.0
    assert B.kendall_tau(gold, gold) == 1.0
    assert B.kendall_tau(gold[::-1], gold) == -1.0
    # one adjacent swap: 5 of 6 pairs concordant
    assert B.pairwise_agreement(["b", "a", "c", "d"], gold) == pytest.approx(5 / 6)
    assert B.kendall_tau(["b", "a", "c", "d"], gold) == pytest.approx(4 / 6)


def test_ranking_problem_flags_missing_extra_and_duplicate_ids():
    ids = ["a", "b", "c"]
    assert B.ranking_problem(["c", "a", "b"], ids) is None
    assert "missing" in B.ranking_problem(["c", "a"], ids)
    assert "extra" in B.ranking_problem(["c", "a", "b", "z"], ids)
    assert "duplicate" in B.ranking_problem(["c", "a", "a", "b"], ids)


def test_cost_per_million():
    assert B.cost("hydrogen-low", 1_000_000, 100_000) == pytest.approx(1.50 + 0.75)
    assert B.cost("flashlite31-low", 1_000_000, 1_000_000) == pytest.approx(0.25 + 1.50)
    assert B.cost("luna-medium", 1_000_000, 1_000_000) == pytest.approx(0.10 + 0.50)
    assert B.cost("terra-none", 1_000_000, 1_000_000) == pytest.approx(1.00 + 5.00)


def test_model_specs_cover_the_three_configs():
    assert B.MODELS["flashlite31-low"] == ("gemini-3.1-flash-lite", "low")
    assert B.MODELS["flashlite35-low"] == ("gemini-3.5-flash-lite", "low")
    assert B.MODELS["hydrogen-low"] == ("gemini-4-hydrogen", "low")
    assert B.MODELS["hydrogen-medium"] == ("gemini-4-hydrogen", "medium")
    assert B.MODELS["luna-medium"] == ("gpt-6-luna", "medium")
    assert B.MODELS["terra-none"] == ("gpt-6.1-terra", "none")


def test_provider_routing(monkeypatch):
    seen = []
    monkeypatch.setattr(B, "call_gemini", lambda *a: seen.append(("gemini", a[0])) or {"ranking": ["a", "b"]})
    monkeypatch.setattr(B, "call_openai", lambda *a: seen.append(("openai", a[0])) or {"ranking": ["a", "b"]})
    items = [{"id": "a", "title": "A", "summary": "x"}, {"id": "b", "title": "B", "summary": "y"}]
    cfg = B._cfg()
    assert B.battle_call(cfg, "luna-medium", items)["invalid"] is None
    assert B.battle_call(cfg, "flashlite35-low", items)["invalid"] is None
    assert seen == [("openai", "gpt-6-luna"), ("gemini", "gemini-3.5-flash-lite")]


def test_gold_batches_fit_cap_and_pass_check_batch(tmp_path):
    cases = B.select_gold_groups(_cases(), per_type=80, seed=0)
    paths = B.write_gold_batches(cases, tmp_path, max_groups=12, max_bytes=55_000)
    assert len(paths) == 34  # ceil(400 / 12)
    seen = []
    for p in paths:
        assert p.stat().st_size < 55_000
        b = json.loads(p.read_text())
        assert b["ids"] == [g["group"] for g in b["groups"]]
        seen += b["case_ids"]
    assert sorted(seen) == sorted(c["case_id"] for c in cases)

    # a well-formed agent result for batch-000 passes the shared validator
    b0 = json.loads(paths[0].read_text())
    res_dir = tmp_path / "opus-results"
    res_dir.mkdir()
    rp = res_dir / "batch-000.json"
    rp.write_text(json.dumps({"rankings": [
        {"group": g["group"], "ranking": [it["id"] for it in g["items"]]} for g in b0["groups"]]}))
    r = subprocess.run([sys.executable, "tools/check_batch.py", str(rp)], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


def test_read_gold_maps_groups_back_to_cases(tmp_path):
    cases = B.select_gold_groups(_cases(), per_type=80, seed=0)
    paths = B.write_gold_batches(cases, tmp_path, max_groups=12)
    (tmp_path / "opus-results").mkdir()
    for p in paths:
        b = json.loads(p.read_text())
        (tmp_path / "opus-results" / p.name).write_text(json.dumps({"rankings": [
            {"group": g["group"], "ranking": [it["id"] for it in g["items"]][::-1]} for g in b["groups"]]}))
    gold, problems = B.read_gold(tmp_path)
    assert len(gold) == 400 and not problems
    c0 = cases[0]
    assert gold[c0["case_id"]] == ["f", "e", "d", "c", "b", "a"]


# ---------------------------------------------------------------- batch-size sweep
def _ckpts(n_sessions=10, n_items=60):
    return [{"sid": f"s{s}", "items": [
        {"id": str(i), "title": f"T{s}-{i}", "summary": f"S{s}-{i}", "rating": float(i)}
        for i in range(n_items)]} for s in range(n_sessions)]


def test_big_groups_are_single_session_and_split_random_band():
    gs = B.big_groups(_ckpts(), n=40, size=20, seed=0)
    assert len(gs) == 40
    assert Counter(g["strata"]["group_type"] for g in gs) == {"random": 20, "band": 20}
    for g in gs:
        its = g["input"]["items"]
        assert len(its) == 20 and len({it["id"] for it in its}) == 20
        assert g["case_id"].split(":")[1] == g["provenance"]["sid"]
    # a band group is 20 consecutive items by rating
    band = next(g for g in gs if g["strata"]["group_type"] == "band")
    r = sorted(int(it["id"]) for it in band["input"]["items"])
    assert r == list(range(r[0], r[0] + 20))
    assert [g["case_id"] for g in gs] == [g["case_id"] for g in B.big_groups(_ckpts(), n=40, size=20, seed=0)]


@pytest.mark.parametrize("k,n_chunks", [(6, 3), (8, 2), (10, 2), (12, 1), (15, 1), (20, 1)])
def test_k_chunks_are_disjoint_deterministic_and_full_size(k, n_chunks):
    g = B.big_groups(_ckpts(), n=2, size=20, seed=0)[0]
    ch = B.k_chunks(g, k)
    assert len(ch) == n_chunks and all(len(c) == k for c in ch)
    flat = [it["id"] for c in ch for it in c]
    assert len(flat) == len(set(flat))
    assert ch == B.k_chunks(g, k)


def test_pair_counts_pool_concordant_pairs_against_gold_subset():
    gold = ["a", "b", "c", "d", "e"]
    assert B.pair_counts(["c", "a"], gold) == (0, 1)       # gold has a > c
    assert B.pair_counts(["a", "b", "c"], gold) == (3, 0)


def test_ordered_items_follows_reference_or_reverse():
    c = _case("q2", 0, ids=("a", "b", "c"))
    c["input"]["items"] = list(reversed(c["input"]["items"]))  # stored shuffled: c, b, a
    ids = lambda xs: [x["id"] for x in xs]
    assert ids(B.ordered_items(c, "as-is")) == ["c", "b", "a"]
    assert ids(B.ordered_items(c, "reference")) == ["a", "b", "c"]
    assert ids(B.ordered_items(c, "reverse")) == ["c", "b", "a"]


def test_run_tag_names_variants():
    assert B.run_tag("flashlite35-low", "battle_prompt", "as-is") == "flashlite35-low"
    assert B.run_tag("flashlite35-low", "battle_prompt_v2", "as-is") == "flashlite35-low@v2"
    assert B.run_tag("flashlite35-low", "battle_prompt", "reference") == "flashlite35-low+reference"
    assert B.run_tag("flashlite35-low", "battle_prompt_v2", "reverse") == "flashlite35-low@v2+reverse"


def test_battle_call_uses_the_given_prompt(monkeypatch):
    seen = []
    monkeypatch.setattr(B, "call_gemini", lambda m, l, system, user, schema: seen.append(system) or {"ranking": ["a", "b"]})
    items = [{"id": "a", "title": "A", "summary": "x"}, {"id": "b", "title": "B", "summary": "y"}]
    B.battle_call(B._cfg("battle_prompt_v2"), "flashlite35-low", items)
    assert "order of the array is random" in seen[0]
