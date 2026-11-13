"""Tests for evals.extract.candidates — Phase B candidate selection."""
from evals.extract import candidates as C


def _filter_rec(sid, items, labels):
    return {"prompt": "filter_urls", "sid": sid, "batch": "batch-000",
            "input": {"items": items},
            "output": {"classifications": [{"id": i, "is_ai": v} for i, v in labels.items()]}}


def test_norm_title():
    assert C.norm("  OpenAI’s  New Model!! ") == "openai s new model"


def test_filter_units_enrich_source_and_flag_inconsistent_titles():
    recs = [
        _filter_rec("2026-06-01-07-00-00", [{"id": "0", "title": "Nvidia earnings"},
                                            {"id": "1", "title": "Cat video"}],
                    {"0": True, "1": False}),
        _filter_rec("2026-07-01-07-00-00", [{"id": "0", "title": "Nvidia Earnings"}],
                    {"0": False}),
    ]
    gather = {"2026-06-01-07-00-00": {"0": {"source": "Reuters", "url": "u0"},
                                      "1": {"source": "Blog", "url": "u1"}},
              "2026-07-01-07-00-00": {"0": {"source": "CNBC", "url": "u2"}}}
    units = C.filter_units(recs, gather)
    assert len(units) == 3
    u = units[0]
    assert u["input"] == {"items": [{"id": "0", "title": "Nvidia earnings"}]}
    assert u["prod_output"] == {"is_ai": True}
    assert u["strata"] == {"month": "2026-06", "source": "Reuters", "label": True}
    nv = [x for x in units if C.norm(x["input"]["items"][0]["title"]) == "nvidia earnings"]
    assert all("inconsistent_prod" in x["tags"] for x in nv)
    assert all(x["hard_score"] > 0 for x in nv)


def test_pair_units_flag_label_vs_overlap_mismatch():
    rec = {"prompt": "dedupe_vs_archive", "sid": "2026-10-01-07-00-00", "batch": "batch-000",
           "input": {"pairs": [
               {"id": "0", "a_title": "Nvidia beats earnings estimates",
                "a_short_summary": "", "b_title": "Nvidia beats earnings estimates again",
                "b_short_summary": "", "b_url": "x"},
               {"id": "1", "a_title": "Apple ships glasses", "a_short_summary": "",
                "b_title": "Meta lays off staff", "b_short_summary": "", "b_url": "y"}]},
           "output": {"results": [{"id": "0", "same": False}, {"id": "1", "same": False}]}}
    units = C.pair_units([rec])
    assert [u["prod_output"] for u in units] == [{"same": False}, {"same": False}]
    assert "different_but_similar" in units[0]["tags"]
    assert units[0]["hard_score"] > units[1]["hard_score"]


def test_summary_rule_violations():
    out = {"short_summary": " ".join(["w"] * 30), "summary": "- a\n- b"}
    v = C.summary_violations(out, text="x" * 8000)
    assert {"short_summary_over_25", "bullets_not_3", "text_truncated"} <= set(v)
    assert C.summary_violations({"short_summary": "Fine one", "summary": "- a\n- b\n- c"},
                                text="y" * 3000) == []
    assert "no_content" in C.summary_violations(
        {"short_summary": "no content", "summary": "- no content"}, text="z")


def test_rate_units_skip_unrated_and_flag_midrange():
    ck = [{"sid": "2026-08-01-07-00-00", "items": [
        {"id": "0", "title": "T", "summary": "- s", "source": "S",
         "quality_low": 0.1, "on_topic": 0.5, "importance": 0.9, "bt_z": -1.5},
        {"id": "1", "title": "U", "summary": None, "on_topic": None}]}]
    units = C.rate_units(ck, "rate_on_topic")
    (u,) = units
    assert u["input"] == {"items": [{"id": "0", "input_text": "T\n- s"}]}
    assert u["prod_output"] == {"confidence": 0.5}
    assert "midrange" in u["tags"]
    (imp,) = C.rate_units(ck, "rate_importance")
    assert "importance_vs_battle_mismatch" in imp["tags"]


def test_critique_section_only_when_final_draft_was_judged():
    recs = [{"prompt": "write_section", "sid": "2026-09-01-07-00-00", "batch": "batch-000",
             "input": {"section_title": "X", "stories": []},
             "output": {"final_section_markdown": "## X", "scores": [7.0, 8.5],
                        "feedbacks": ["f1", "f2"], "accepted": True, "iterations": 2}},
            {"prompt": "write_section", "sid": "2026-09-01-07-00-00", "batch": "batch-001",
             "input": {"section_title": "Y", "stories": []},
             "output": {"final_section_markdown": "## Y", "scores": [6.0, 7.0],
                        "feedbacks": ["g1", "g2"], "accepted": False, "iterations": 2}}]
    (u,) = C.critique_section_units(recs)
    assert u["input"] == {"section_markdown": "## X"}
    assert u["prod_output"] == {"score": 8.5, "feedback": "f2", "accept": True}


def _battle_ck(sid, n):
    return {"sid": sid, "items": [{"id": str(i), "title": f"T{i}", "summary": "- s",
                                   "rating": float(i)} for i in range(n)]}


def test_battle_groups_quartiles_and_mixed():
    ckpts = [_battle_ck(f"2026-08-{d:02d}-07-00-00", 40) for d in range(1, 21)]
    ckpts.append(_battle_ck("2026-08-30-07-00-00", 20))  # too small for 6/quartile: skipped
    units = C.battle_units(ckpts, n=100, seed=1)
    assert len(units) == 100
    types = [u["strata"]["group_type"] for u in units]
    assert {t: types.count(t) for t in set(types)} == {
        "q1_top": 20, "q2": 20, "q3": 20, "q4_bottom": 20, "mixed": 20}
    assert all(u["provenance"]["sid"] != "2026-08-30-07-00-00" for u in units)
    for u in units:
        ids = [it["id"] for it in u["input"]["items"]]
        assert len(ids) == 6 == len(set(ids))
        assert set(u["input"]["items"][0]) == {"id", "title", "summary"}
        r = u["prod_output"]["rating"]
        assert u["prod_output"]["ranking"] == sorted(ids, key=lambda i: -r[i])
        q = [int(i) // 10 for i in ids]  # 40 items, rating == id → quartile = id // 10
        t = u["strata"]["group_type"]
        if t == "mixed":
            assert set(q) == {0, 1, 2, 3}
        else:
            want = {"q4_bottom": 0, "q3": 1, "q2": 2, "q1_top": 3}[t]
            assert set(q) == {want}


def test_select_dedupes_mixes_hard_and_spreads_months():
    units = []
    for m in ("2026-06", "2026-07", "2026-08"):
        for i in range(50):
            units.append({"case_id": f"{m}-{i}", "key": f"{m}-{i % 40}",
                          "strata": {"month": m}, "tags": [],
                          "hard_score": 1.0 if i < 10 else 0.0})
    picked = C.select(units, n=60, hard_frac=0.5, seed=0)
    assert len(picked) == 60
    assert len({u["key"] for u in picked}) == 60
    assert sum(u["sample"] == "hard" for u in picked) == 30
    months = [u["strata"]["month"] for u in picked]
    assert min(months.count(m) for m in set(months)) >= 15


def test_select_takes_all_when_fewer_than_n():
    units = [{"case_id": str(i), "key": str(i), "strata": {"month": "2026-06"},
              "tags": [], "hard_score": 0.0} for i in range(5)]
    assert len(C.select(units, n=1000, hard_frac=0.0, seed=0)) == 5


def _u(i, label, tags=(), hard=0.0, m="2026-06"):
    return {"case_id": str(i), "key": str(i), "strata": {"month": m, "label": label},
            "tags": list(tags), "hard_score": hard}


def test_select_balances_labels_when_minority_is_available():
    units = [_u(i, True) for i in range(900)] + [_u(1000 + i, False) for i in range(100)]
    picked = C.select(units, n=300, hard_frac=0.0, seed=0, balance_label=True)
    labels = [u["strata"]["label"] for u in picked]
    assert len(picked) == 300
    assert labels.count(False) == 100  # all of the scarce label, rest True
    picked = C.select(units, n=100, hard_frac=0.0, seed=0, balance_label=True)
    assert [u["strata"]["label"] for u in picked].count(False) == 50


def test_select_caps_any_single_tag_among_hard_picks():
    units = [_u(i, None, tags=["no_content"], hard=2.0) for i in range(500)]
    units += [_u(1000 + i, None, tags=["source_attribution"], hard=1.0) for i in range(500)]
    units += [_u(2000 + i, None, tags=["bullets_not_3"], hard=1.0) for i in range(500)]
    picked = C.select(units, n=100, hard_frac=1.0, seed=0, max_tag_share=0.4)
    tags = [u["tags"][0] for u in picked]
    assert len(picked) == 100
    assert tags.count("no_content") == 40
    assert tags.count("source_attribution") <= 40 and tags.count("bullets_not_3") <= 40


def test_recent_units_newest_first_and_dedupes_by_key():
    u = lambda sid, key: {"key": key, "provenance": {"sid": sid}, "sample": None}
    units = [u("2026-11-01-07", "a"), u("2026-11-09-18", "b"), u("2026-11-09-07", "b"), u("2026-11-05-07", "c")]
    out = C.recent_units(units, n=2)
    assert [x["key"] for x in out] == ["b", "c"]          # newest sid wins the duplicate "b"
    assert out[0]["provenance"]["sid"] == "2026-11-09-18"
    assert all(x["sample"] == "recent" for x in out)
    assert units[1]["sample"] is None                     # inputs not mutated


def test_recent_cli_writes_newest_pairs(tmp_path):
    import gzip, json
    from click.testing import CliRunner
    arch = tmp_path / "arch"
    arch.mkdir()
    def rec(sid, pid, a, b, same):
        return {"prompt": "coverage_boost", "sid": sid, "batch": "batch-000",
                "input": {"pairs": [{"id": pid, "a_title": a, "a_summary": "- x", "b_title": b, "b_summary": "- y"}]},
                "output": {"results": [{"id": pid, "same": same}]}}
    with gzip.open(arch / "coverage_boost.jsonl.gz", "wt") as f:
        for r in (rec("2026-11-01-07-00-00", "1-2", "Old A", "Old B", False),
                  rec("2026-11-10-07-00-00", "3-4", "New A", "New B", True)):
            f.write(json.dumps(r) + "\n")
    out = tmp_path / "recent.jsonl"
    res = CliRunner().invoke(C.cli, ["--archive", str(arch), "recent", "--prompt", "coverage_boost",
                                     "--n", "1", "--out", str(out)])
    assert res.exit_code == 0, res.output
    rows = [json.loads(l) for l in out.read_text().splitlines()]
    assert [r["input"]["pairs"][0]["a_title"] for r in rows] == ["New A"]
    assert rows[0]["prod_output"] == {"same": True}
