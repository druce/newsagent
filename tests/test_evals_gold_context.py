"""Tests for evals.run.gold_context — attaching article context to gold cases."""
from evals.run import gold_context as G


def test_lookup_joins_on_title_never_on_batch_local_id():
    recs = [{"sid": "S1", "input": {"items": [{"id": "3", "title": "Nvidia’s New Chip!"}]},
             "output": {"summaries": [{"id": "3", "summary": "- a\n- b", "short_summary": "x"}]}}]
    idx = G.index_summaries(recs)
    # same sid and id but a different title: summarize ids are batch-local, so no match
    assert G.lookup({"case_id": "filter_urls:S1:batch-000:3", "title": "other"}, idx) is None
    assert G.lookup({"case_id": "filter_urls:S9:batch-000:0", "title": "nvidia s new chip"}, idx)["summary"] == "- a\n- b"


def test_index_skips_empty_summaries():
    recs = [{"sid": "S", "input": {"items": [{"id": "0", "title": "T"}]},
             "output": {"summaries": [{"id": "0", "summary": "  "}]}}]
    assert G.index_summaries(recs) == {}


def test_looks_wrong_flags_front_pages_and_unrelated_text():
    assert G.looks_wrong("Data center uproar", "Top Items: Politico: Trump snub ...")
    assert G.looks_wrong("Asia shares edge up as chip selloff eases", "Dollar gains after hot inflation data")
    assert not G.looks_wrong("TSMC reports Q2 revenue up 36%", "Taiwan Semiconductor reported revenue ...")


def test_labeler_context_reads_exactly_what_the_batch_held(tmp_path):
    import json
    (tmp_path / "relabel-batches").mkdir()
    (tmp_path / "relabel-batches" / "batch-000.json").write_text(json.dumps({
        "items": [{"id": "0", "title": "A", "bullets": "- x"}, {"id": "1", "title": "B", "text_excerpt": "body"},
                  {"id": "2", "title": "C"}],
        "case_ids": ["c0", "c1", "c2"]}))
    assert G.labeler_context(tmp_path) == {"c0": "- x", "c1": "body", "c2": ""}


def test_item_for_excerpt_mode_uses_only_the_filter_excerpt():
    case = {"title": "T", "source": "S"}
    ctx = {"bullets": "- b", "article_text": "body"}
    assert G.item_for(case, ctx, excerpt="feed dek", mode="excerpt") == {"title": "T", "source": "S", "excerpt": "feed dek"}
    assert G.item_for(case, ctx, excerpt="", mode="excerpt") == {"title": "T", "source": "S"}
    assert G.item_for(case, ctx)["bullets"] == "- b"


def test_labeler_context_reads_excerpt_key(tmp_path):
    import json
    (tmp_path / "relabel-excerpt-batches").mkdir()
    (tmp_path / "relabel-excerpt-batches" / "batch-000.json").write_text(json.dumps({
        "items": [{"id": "0", "title": "A", "excerpt": "dek"}], "case_ids": ["c0"]}))
    assert G.labeler_context(tmp_path, "relabel-excerpt") == {"c0": "dek"}


def test_item_for_uses_title_override():
    from evals.run import gold_context as G
    case = {"title": "a dek posing as title", "source": "WSJ"}
    assert G.item_for(case, {}, "dek", mode="excerpt", title="Real Headline") == \
        {"title": "Real Headline", "source": "WSJ", "excerpt": "dek"}


def test_write_review_v4_carries_human_labels_and_new_inputs(tmp_path):
    import csv, json
    from evals.run import gold_context as G
    gd = tmp_path
    (gd / "relabel-v4-batches").mkdir()
    (gd / "relabel-v4-results").mkdir()
    (gd / "relabel-v4-batches" / "batch-000.json").write_text(json.dumps({
        "ids": ["0", "1"], "case_ids": ["c1", "c2"],
        "items": [{"id": "0", "title": "Real Headline", "source": "WSJ", "excerpt": "The dek."},
                  {"id": "1", "title": "Same", "source": "NYT"}]}))
    (gd / "relabel-v4-results" / "batch-000.json").write_text(json.dumps({"labels": [
        {"id": "0", "is_ai": True, "confidence": "high", "ai_signal": "title", "rationale": "r0"},
        {"id": "1", "is_ai": False, "confidence": "low", "ai_signal": "none", "rationale": "r1"}]}))
    src = gd / "v3.csv"
    with src.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["case_id", "gold_reason", "month", "input_title", "input_source",
                                          "input_excerpt", "rec_gold", "info_new_terra", "human_is_ai", "human_notes"])
        w.writeheader()
        w.writerow({"case_id": "c2", "gold_reason": "split", "month": "2026-05", "input_title": "Same",
                    "input_source": "NYT", "input_excerpt": "", "rec_gold": "True", "info_new_terra": "False",
                    "human_is_ai": "True", "human_notes": ""})
        w.writerow({"case_id": "c1", "gold_reason": "split", "month": "2026-05", "input_title": "A dek posing",
                    "input_source": "WSJ", "input_excerpt": "", "rec_gold": "False", "info_new_terra": "True",
                    "human_is_ai": "False", "human_notes": "n"})
    out = G.write_review_v4(gd, src, gd / "v4.csv", flags={"c1": "title changed"})
    rows = list(csv.DictReader(out.open()))
    assert [r["case_id"] for r in rows] == ["c2", "c1"]                     # v3 order kept
    r = rows[1]
    assert (r["input_title"], r["input_excerpt"], r["v3_title"]) == ("Real Headline", "The dek.", "A dek posing")
    assert (r["rec_gold"], r["rec_confidence"], r["rec_rationale"], r["v3_rec_gold"]) == ("True", "high", "r0", "False")
    assert (r["human_is_ai"], r["human_notes"], r["info_flag"]) == ("False", "n", "title changed")
    assert rows[0]["info_new_terra"] == "False" and rows[0]["info_flag"] == ""
    assert src.read_text().count("A dek posing") == 1                     # source untouched


def test_write_eval_set_from_human_labels(tmp_path):
    import csv, json
    from evals.run import gold_context as G
    review = tmp_path / "v4.csv"
    with review.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["case_id", "input_title", "input_source", "input_excerpt",
                                          "rec_gold", "human_is_ai", "human_notes"])
        w.writeheader()
        w.writerow({"case_id": "filter_urls:S1:batch-000:7", "input_title": "AI thing", "input_source": "WSJ",
                    "input_excerpt": "The dek.", "rec_gold": "False", "human_is_ai": "TRUE", "human_notes": "n"})
        w.writerow({"case_id": "filter_urls:S2:batch-001:3", "input_title": "Chip fab", "input_source": "NYT",
                    "input_excerpt": "", "rec_gold": "True", "human_is_ai": "false", "human_notes": ""})
    cases = tmp_path / "cases.jsonl"
    cases.write_text("\n".join(json.dumps({"case_id": c, "provenance": {"sid": s}, "strata": {"month": "2026-05"}})
                               for c, s in [("filter_urls:S1:batch-000:7", "S1"), ("filter_urls:S2:batch-001:3", "S2")]))
    out = G.write_eval_set(review, cases, tmp_path / "sets" / "filter_urls.jsonl", policy="P")
    recs = [json.loads(l) for l in out.read_text().splitlines()]
    assert len(recs) == 2
    a, b = recs
    assert a["input"] == {"items": [{"id": "0", "title": "AI thing", "excerpt": "The dek."}]}
    assert a["expected"] == {"classifications": [{"id": "0", "is_ai": True}]}
    assert (a["source"], a["notes"], a["opus_is_ai"], a["provenance"]["sid"]) == ("WSJ", "n", False, "S1")
    assert b["input"]["items"][0] == {"id": "0", "title": "Chip fab"}       # empty excerpt omitted
    assert b["expected"]["classifications"][0]["is_ai"] is False
    assert (out.parent / "filter_urls.policy.txt").read_text() == "P"


def test_write_eval_set_rejects_unlabeled_rows(tmp_path):
    import csv, pytest
    from evals.run import gold_context as G
    review = tmp_path / "v4.csv"
    with review.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["case_id", "input_title", "input_source", "input_excerpt",
                                          "rec_gold", "human_is_ai", "human_notes"])
        w.writeheader()
        w.writerow({"case_id": "c", "input_title": "t", "input_source": "s", "input_excerpt": "",
                    "rec_gold": "", "human_is_ai": "", "human_notes": ""})
    (tmp_path / "cases.jsonl").write_text("")
    with pytest.raises(ValueError, match="unlabeled"):
        G.write_eval_set(review, tmp_path / "cases.jsonl", tmp_path / "out.jsonl")
