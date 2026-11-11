"""Tests for evals.run.challenge — multi-model challenger runs (Phase C)."""
import csv
import json

import pytest

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
    assert "AI-related if the title mentions" in instr
    assert "Input:" not in instr and "Classify every headline below" not in instr


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
