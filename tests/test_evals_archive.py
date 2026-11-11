"""Tests for evals.extract.archive — Phase A raw extraction of prompt I/O."""
import json
import sqlite3

from evals.extract import archive as A


def _w(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj) if not isinstance(obj, str) else obj)


def _batch(sid, bid, key, items, user_prompt="UP", **extra):
    d = {"batch_id": bid, "session_id": sid,
         "ids": [str(i.get("id", i.get("cluster_id", ""))) for i in items],
         key: items, "system_prompt": "SYS", "user_prompt": user_prompt,
         "output_schema": {}}
    d.update(extra)
    return d


def test_parse_items_from_user_prompt():
    up = 'For each article below, produce...:\n\n[{"id": "0", "title": "T", "text": "body [x]"}]'
    assert A.parse_items_from_user_prompt(up) == [{"id": "0", "title": "T", "text": "body [x]"}]


def test_batch_records_pairs_input_and_output(tmp_path):
    sid = "2026-11-09-18-00-11"
    run = tmp_path / sid
    _w(run / "filter-batches/batch-000.json",
       _batch(sid, 0, "items", [{"id": "0", "title": "A"}, {"id": "1", "title": "B"}]))
    _w(run / "filter-results/batch-000.json",
       {"classifications": [{"id": "0", "is_ai": True}, {"id": "1", "is_ai": False}]})
    _w(run / "filter-batches/batch-001.json",
       _batch(sid, 1, "items", [{"id": "2", "title": "C"}]))  # orphan, no result
    recs = list(A.batch_records(run))
    assert [r["prompt"] for r in recs] == ["filter_urls", "filter_urls"]
    r0 = recs[0]
    assert r0["sid"] == sid and r0["batch"] == "batch-000"
    assert r0["input"]["items"][1]["title"] == "B"
    assert r0["output"]["classifications"][1]["is_ai"] is False
    assert r0["system_prompt_hash"] == A.sha("SYS")
    assert recs[1]["output"] is None


def test_summarize_items_recovered_from_user_prompt(tmp_path):
    sid = "2026-11-09-18-00-11"
    run = tmp_path / sid
    up = 'hdr:\n\n' + json.dumps([{"id": "0", "title": "T", "text": "full text"}])
    _w(run / "summarize-batches/batch-000.json",
       _batch(sid, 0, "items", [{"id": "0", "title": "T"}], user_prompt=up))
    _w(run / "summarize-results/batch-000.json",
       {"summaries": [{"id": "0", "short_summary": "s", "summary": "- a"}]})
    (rec,) = A.batch_records(run)
    assert rec["prompt"] == "extract_summaries"
    assert rec["input"]["items"][0]["text"] == "full text"


def test_draft_record_carries_write_and_critique_outputs(tmp_path):
    sid = "2026-11-09-18-00-11"
    run = tmp_path / sid
    stories = [{"title": "t", "url": "u", "summary": "s", "source": "x", "rating": 5.0}]
    _w(run / "draft-batches/batch-000.json",
       {"batch_id": 0, "session_id": sid, "cat": "Chips", "stories": stories,
        "write_system_prompt": "W", "write_user_prompt": "WU",
        "critique_system_prompt": "C", "critique_user_prompt": "CU",
        "improve_system_prompt": "I", "improve_user_prompt": "IU",
        "max_edits": 2, "accept_threshold": 8.0, "output_schema": {}})
    _w(run / "draft-results/batch-000.json",
       {"cat": "Chips", "final_section_markdown": "## Chips", "iterations": 1,
        "scores": [8.2], "feedbacks": ["ok"], "accepted": True})
    (rec,) = A.batch_records(run)
    assert rec["prompt"] == "write_section"
    assert rec["input"] == {"section_title": "Chips", "stories": stories}
    assert rec["output"]["final_section_markdown"] == "## Chips"
    assert rec["system_prompt_hash"] == A.sha("W")


def test_unknown_step_dirs_are_reported_not_archived(tmp_path):
    sid = "2026-11-09-18-00-11"
    run = tmp_path / sid
    _w(run / "select-assign-batches/batch-000.json", _batch(sid, 0, "headlines", []))
    assert list(A.batch_records(run)) == []
    assert "select-assign-batches" in A.unknown_batch_dirs(run)


def test_rewrite_records_chain(tmp_path):
    sid = "2026-11-09-18-00-11"
    w = tmp_path / sid / "rewrite-work"
    for name, body in [("draft-init.md", "D0"), ("critique-0.md", "C0"),
                       ("draft-0.md", "D1"), ("critique-1.md", "C1"),
                       ("title.txt", "Title\n")]:
        _w(w / name, body)
    recs = A.rewrite_records(tmp_path / sid)
    by = {}
    for r in recs:
        by.setdefault(r["prompt"], []).append(r)
    crit = by["critique_newsletter"]
    assert [(c["input"]["newsletter_markdown"], c["output"]) for c in crit] == [("D0", "C0"), ("D1", "C1")]
    (imp,) = by["improve_newsletter"]
    assert imp["input"] == {"newsletter_markdown": "D0", "critique": "C0"}
    assert imp["output"] == "D1"
    (t,) = by["generate_newsletter_title"]
    assert t["input"] == {"newsletter_markdown": "D1"} and t["output"] == "Title"


def test_rate_records_exclude_embeddings(tmp_path):
    db = tmp_path / "x.db"
    con = sqlite3.connect(db)
    con.execute("create table agent_state (id integer primary key, session_id text, step_name text, state_data text)")
    state = {"headline_data": [{"title": "T", "summary": "- s", "source": "Src",
                                "url": "u", "embedding": [0.1] * 5,
                                "quality_low": 0.0, "on_topic": 0.9, "importance": 0.4,
                                "bt_z": 1.2, "rating": 7.0}]}
    con.execute("insert into agent_state (session_id, step_name, state_data) values (?,?,?)",
                ("2026-11-09-18-00-11", "rate", json.dumps(state)))
    con.commit()
    con.close()
    (rec,) = A.checkpoint_items(db, "rate", A.RATE_FIELDS)
    assert rec["sid"] == "2026-11-09-18-00-11"
    item = rec["items"][0]
    assert item["id"] == "0" and item["on_topic"] == 0.9 and item["bt_z"] == 1.2
    assert "embedding" not in item
