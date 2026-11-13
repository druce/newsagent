"""Tests for evals.run.challenge — multi-model challenger runs (Phase C)."""
import csv
import json

import pytest
from collections import Counter

from evals.run import challenge as R


def _cases(n):
    return [{"case_id": f"filter_urls:s:b:{i}", "prompt": "filter_urls", "key": f"k{i}",
             "input": {"items": [{"id": str(i), "title": f"Headline {i}"}]},
             "prod_output": {"is_ai": i % 2 == 0},
             "strata": {"month": "2026-09", "source": "Src", "label": i % 2 == 0},
             "tags": ["borderline_topic"] if i % 3 == 0 else [], "sample": "hard"}
            for i in range(n)]


def test_pack_is_deterministic_renumbers_and_limit_is_a_prefix():
    full = R.pack(_cases(60), "filter_urls", batch_size=25, seed=0)
    again = R.pack(_cases(60), "filter_urls", batch_size=25, seed=0)
    assert [b["case_ids"] for b in full] == [b["case_ids"] for b in again]
    assert [len(b["case_ids"]) for b in full] == [25, 25, 10]
    b0 = full[0]
    assert b0["ids"] == [str(i) for i in range(25)]
    assert [it["id"] for it in b0["items"]] == b0["ids"]
    assert '"id": "0"' in b0["user_prompt"] and "Headline" in b0["user_prompt"]
    assert b0["system_prompt"]
    small = R.pack(_cases(60), "filter_urls", batch_size=25, seed=0, limit=50)
    assert [b["case_ids"] for b in small] == [b["case_ids"] for b in full[:2]]


def test_cost_uses_per_million_prices():
    assert R.cost("terra", in_tok=1_000_000, out_tok=200_000) == pytest.approx(1.0 + 0.2 * 5.0)
    assert R.cost("luna", in_tok=400, out_tok=0) == pytest.approx(400 * 0.10 / 1e6)
    assert R.cost("haiku", in_tok=10, out_tok=10) is None  # subscription: no $ figure


def test_parse_filter_output_maps_local_ids():
    out = {"classifications": [{"id": "0", "is_ai": True}, {"id": "1", "is_ai": False}]}
    assert R.parse_labels("filter_urls", out) == {"0": {"is_ai": True}, "1": {"is_ai": False}}


def test_decisions_instructions_keep_rules_drop_io_boilerplate():
    instr = R.decisions_instructions("filter_urls")
    assert "AI-related if the article is about" in instr
    assert "Input:" not in instr and "Classify every item below" not in instr


def test_write_csvs(tmp_path):
    batches = R.pack(_cases(4), "filter_urls", batch_size=2, seed=0)
    run = tmp_path / "run"
    run.mkdir()
    (run / "batches.json").write_text(json.dumps(batches))
    (run / "cases.jsonl").write_text("".join(json.dumps(c) + "\n" for c in _cases(4)))
    # every model says True except gemini on one case; luna is per item with probabilities
    for m in ("haiku", "opus", "terra", "gemini"):
        rows = []
        for b in batches:
            labels = {lid: {"is_ai": True} for lid in b["ids"]}
            if m == "gemini" and b["batch"] == "batch-000":
                labels[b["ids"][0]] = {"is_ai": False}
            rows.append({"model": m, "batch": b["batch"], "unit": "batch", "n_items": len(b["ids"]),
                         "labels": labels, "secs": 2.0, "in_tok": 100, "out_tok": 10,
                         "reasoning_tok": 5, "cost": R.cost(m, 100, 10), "error": None})
        R.append_jsonl(run / f"{m}.jsonl", rows)
    luna = [{"model": "luna", "batch": b["batch"], "unit": "item", "item": lid, "n_items": 1,
             "labels": {lid: {"is_ai": True, "prob": 0.9}}, "secs": 0.4, "in_tok": 380,
             "out_tok": 0, "reasoning_tok": 0, "cost": R.cost("luna", 380, 0), "error": None}
            for b in batches for lid in b["ids"]]
    R.append_jsonl(run / "luna.jsonl", luna)

    R.write_csvs(run, "filter_urls", models=["haiku", "opus", "terra", "gemini", "luna"])
    items = list(csv.DictReader(open(run / "items.csv")))
    assert len(items) == 4
    split = [r for r in items if r["gemini_is_ai"] == "False"]
    assert len(split) == 1
    s = split[0]
    assert s["n_ai_votes"] == "4" and s["unanimous"] == "False"
    assert s["dissent"] == "gemini"
    assert s["luna_prob"] == "0.9"
    assert items[0]["unanimous"] == "False"  # disagreements sort first

    models = {r["model"]: r for r in csv.DictReader(open(run / "models.csv"))}
    assert models["luna"]["call_unit"] == "item"
    assert models["haiku"]["call_unit"] == "batch"
    assert models["haiku"]["total_cost_usd"] == ""
    assert float(models["terra"]["total_cost_usd"]) > 0
    assert models["gemini"]["agree_with_majority"] == "0.75"
    calls = list(csv.DictReader(open(run / "calls.csv")))
    assert len(calls) == 4 * 2 + 4


def test_call_luna_sends_name_and_cache_key_and_reads_cached_tokens(monkeypatch):
    import httpx
    sent = {}

    class Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"answers": [{"type": "predicate", "name": "ai_related", "probability": 0.8}],
                    "usage": {"input_tokens": 380,
                              "input_tokens_details": {"cached_tokens": 256},
                              "output_tokens": 0}}

    def fake_post(url, **kw):
        sent.update(kw["json"])
        return Resp()

    monkeypatch.setenv("OPENAI_API_KEY", "x")
    monkeypatch.setattr(httpx, "post", fake_post)
    res = R.call_luna("Some headline", "filter_urls")
    assert sent["prompt_cache_key"] == "ai-news-classifier-v1"
    assert sent["questions"][0]["name"] == "ai_related"
    assert sent["questions"][0]["type"] == "predicate"
    assert res["prob"] == 0.8 and res["cached_tok"] == 256 and res["in_tok"] == 380


def test_call_luna_records_refusal_without_probability(monkeypatch):
    import httpx

    class Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"answers": [{"type": "refusal", "name": "ai_related"}],
                    "usage": {"input_tokens": 8, "output_tokens": 0}}

    monkeypatch.setenv("OPENAI_API_KEY", "x")
    monkeypatch.setattr(httpx, "post", lambda url, **kw: Resp())
    res = R.call_luna("440 comments", "filter_urls")
    assert res["prob"] is None and res["refusal"] is True


def test_rerun_drops_previous_error_rows(tmp_path, monkeypatch):
    monkeypatch.setattr(R, "ROOT", tmp_path)
    rd = R.run_dir("filter_urls", "r")
    rd.mkdir(parents=True)
    batches = R.pack(_cases(2), "filter_urls", batch_size=2, seed=0)
    (rd / "batches.json").write_text(json.dumps(batches))
    b = batches[0]
    R.append_jsonl(rd / "luna.jsonl", [
        {"model": "luna", "batch": b["batch"], "item": "0", "unit": "item", "labels": {}, "error": "boom"},
        {"model": "luna", "batch": b["batch"], "item": "1", "unit": "item",
         "labels": {"1": {"is_ai": True, "prob": 0.9}}, "error": None}])
    monkeypatch.setattr(R, "call_luna", lambda title, prompt: {"prob": 0.2, "refusal": False, "in_tok": 5,
                                                                "out_tok": 0, "cached_tok": 0, "reasoning_tok": 0})
    R.run_api("filter_urls", "r", "luna")
    rows = R.read_jsonl(rd / "luna.jsonl")
    assert len(rows) == 2 and not any(r["error"] for r in rows)
    assert {r["item"]: r["labels"][r["item"]]["is_ai"] for r in rows} == {"0": False, "1": True}


def _item(i, unanimous, prod_agrees, majority, month="2026-09"):
    return {"case_id": f"c{i}", "title": f"T{i}", "source": "S", "month": month,
            "unanimous": str(unanimous), "prod_agrees_majority": str(prod_agrees),
            "majority": str(majority)}


def test_select_gold_priorities_and_balanced_controls():
    rows = ([_item(i, False, True, True) for i in range(10)]                # splits
            + [_item(100 + i, True, False, i % 2 == 0) for i in range(5)]   # prod vs unanimous panel
            + [_item(200 + i, True, True, True) for i in range(50)]         # controls, AI
            + [_item(300 + i, True, True, False) for i in range(50)])       # controls, not AI
    picked = R.select_gold(rows, n=25, seed=0)
    reasons = Counter(r["gold_reason"] for r in picked)
    assert reasons == {"split": 10, "prod_vs_consensus": 5, "control": 10}
    ctrl = [r for r in picked if r["gold_reason"] == "control"]
    assert Counter(r["majority"] for r in ctrl) == {"True": 5, "False": 5}
    assert len({r["case_id"] for r in picked}) == 25


def test_select_gold_truncates_splits_when_n_is_small():
    rows = [_item(i, False, True, True) for i in range(10)]
    assert len(R.select_gold(rows, n=4, seed=0)) == 4


def test_v2_cases_attach_excerpt_and_pack_renders_it_only_when_present():
    cases = _cases(2)
    v2 = R.make_v2_cases(cases, {cases[0]["case_id"]: "An AI lab raised money."}, with_excerpt=True)
    assert v2[0]["prompt"] == "filter_urls_v2" and v2[0]["case_id"] == cases[0]["case_id"]
    (b,) = R.pack(v2, "filter_urls_v2", batch_size=2, seed=0)
    assert "An AI lab raised money." in b["user_prompt"]
    assert b["user_prompt"].count('"excerpt"') == 1
    bare = R.make_v2_cases(cases, {cases[0]["case_id"]: "x"}, with_excerpt=False)
    assert all("excerpt" not in c["input"]["items"][0] for c in bare)
    assert R.parse_labels("filter_urls_v2", {"classifications": [{"id": "0", "is_ai": True}]}) == {"0": {"is_ai": True}}


def test_v2_cases_use_corrected_titles_when_given():
    cases = _cases(2)
    v2 = R.make_v2_cases(cases, {}, with_excerpt=True, titles={cases[1]["case_id"]: "Real Headline"})
    assert v2[1]["input"]["items"][0]["title"] == "Real Headline"
    assert v2[0]["input"]["items"][0]["title"] == cases[0]["input"]["items"][0]["title"]


def test_luna_input_appends_excerpt():
    assert R.luna_input({"title": "T"}) == "T"
    assert R.luna_input({"title": "T", "excerpt": "E"}) == "T\n\nE"
    assert "AI is the subject or a main thread" in R.decisions_instructions("filter_urls_v2")


# ------------------------------------------------------------ pair prompts
def _pair_case(cid, summary="- s", same=True, cos=0.8):
    return {"case_id": cid, "prompt": "coverage_boost", "key": cid,
            "input": {"pairs": [{"id": "1-2", "a_title": "A title", "a_summary": summary,
                                 "b_title": "B title", "b_summary": summary}]},
            "prod_output": {"same": same},
            "strata": {"month": "2026-11", "label": same, "cosine": cos}, "tags": [], "sample": "recent"}


def test_parse_coverage_boost_output_maps_local_ids():
    out = {"results": [{"id": "0", "same": True}, {"id": "1", "same": False}]}
    assert R.parse_labels("coverage_boost", out) == {"0": {"same": True}, "1": {"same": False}}


def test_pack_pairs_renumbers_ids_and_keeps_case_ids():
    (b,) = R.pack([_pair_case(f"c{i}") for i in range(3)], "coverage_boost", batch_size=25)
    assert b["ids"] == ["0", "1", "2"] and sorted(b["case_ids"]) == ["c0", "c1", "c2"]
    assert [p["id"] for p in b["items"]] == b["ids"]
    assert '"a_summary"' in b["user_prompt"]


def test_pack_splits_batches_over_byte_cap():
    cases = [_pair_case(f"c{i}", summary="x" * 4000) for i in range(25)]
    bs = R.pack(cases, "coverage_boost", batch_size=25, max_bytes=55_000)
    assert len(bs) > 1 and all(len(b["user_prompt"].encode()) <= 55_000 for b in bs)
    assert sum(len(b["ids"]) for b in bs) == 25
    assert [b["batch"] for b in bs] == [f"batch-{i:03d}" for i in range(len(bs))]


def test_case_title_for_pairs_and_items():
    assert R.case_title(_pair_case("c0")) == "A title || B title"
    assert R.case_title({"input": {"items": [{"id": "0", "title": "T"}]}}) == "T"


def test_write_csvs_for_pairs(tmp_path):
    cases = [_pair_case("c0", same=True), _pair_case("c1", same=False)]
    rd = tmp_path
    (rd / "cases.jsonl").write_text("".join(json.dumps(c) + "\n" for c in cases))
    (rd / "batches.json").write_text(json.dumps([{"batch": "batch-000", "ids": ["0", "1"], "case_ids": ["c0", "c1"]}]))
    for m, labs in {"opus": [True, False], "haiku": [True, True]}.items():
        (rd / f"{m}.jsonl").write_text(json.dumps({"model": m, "batch": "batch-000", "unit": "batch", "n_items": 2,
            "labels": {"0": {"same": labs[0]}, "1": {"same": labs[1]}}, "secs": 1.0, "in_tok": None,
            "out_tok": None, "reasoning_tok": None, "cost": None, "error": None}) + "\n")
    R.write_csvs(rd, "coverage_boost", ["opus", "haiku"])
    rows = {r["case_id"]: r for r in csv.DictReader((rd / "items.csv").open())}
    assert rows["c0"]["title"] == "A title || B title" and rows["c0"]["cosine"] == "0.8"
    assert rows["c1"]["n_same_votes"] == "1" and rows["c1"]["unanimous"] == "False"
