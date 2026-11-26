"""Tests for lib/gui.py — read-only data layer for the pipeline GUI."""
import json
import os

import pytest

from lib.db import init_db
from lib.gui import (GuiError, list_sessions, loop_detail, session_overview,
                     session_start, step_code, step_doc, step_log, step_state)
from lib.state import NewsletterAgentState

SID = "2026-11-21-07-00-30"


def _seed(db, through="rate", headlines=None):
    init_db(db)
    st = NewsletterAgentState(session_id=SID, db_path=db)
    st.headline_data = headlines or [
        {"title": "Low", "site_name": "B", "rating": 0.2, "embedding": [1, 2]},
        {"title": "High", "site_name": "A", "rating": 0.9, "coverage_count": 3},
    ]
    for step_id in [s.id for s in st.steps]:
        st.start_step(step_id)
        st.complete_step(step_id, message=f"{step_id} done")
        st.save_checkpoint(step_id)
        if step_id == through:
            break
    return st


def test_session_start_parses_sid():
    assert session_start(SID) == "2026-11-21T07:00:30"
    assert session_start("2026-06-29") == "2026-06-29"
    assert session_start("weird") is None


def test_list_sessions(tmp_db):
    _seed(tmp_db, through="gather")
    rows = list_sessions(tmp_db)
    assert rows[0]["session_id"] == SID and rows[0]["checkpoints"] == 2


def test_overview_statuses_and_header(tmp_db, tmp_path):
    _seed(tmp_db, through="rate")
    ov = session_overview(tmp_db, SID, runs_root=tmp_path)
    assert [s["id"] for s in ov["steps"]][:3] == ["start", "gather", "filter"]
    assert ov["n_complete"] == 9 and ov["n_steps"] == 14
    assert ov["through"] == "rate" and ov["next"] == "cluster"
    assert ov["status"] == "in_progress" and ov["running"] is None
    rate = next(s for s in ov["steps"] if s["id"] == "rate")
    assert rate["status_message"] == "rate done"
    assert rate["duration_s"] is not None and rate["duration_s"] >= 0


def test_overview_running_marker(tmp_db, tmp_path):
    _seed(tmp_db, through="rate")
    (tmp_path / SID).mkdir()
    (tmp_path / SID / "cluster.running").write_text(f"{os.getpid()}\n")
    ov = session_overview(tmp_db, SID, runs_root=tmp_path)
    assert ov["running"] == "cluster"
    # a stale marker (dead pid) is ignored
    (tmp_path / SID / "cluster.running").write_text("999999\n")
    assert session_overview(tmp_db, SID, runs_root=tmp_path)["running"] is None


def test_overview_complete_and_unknown(tmp_db, tmp_path):
    _seed(tmp_db, through="send")
    assert session_overview(tmp_db, SID, runs_root=tmp_path)["status"] == "complete"
    with pytest.raises(GuiError):
        session_overview(tmp_db, "nope", runs_root=tmp_path)
    with pytest.raises(GuiError):
        session_overview(tmp_db, "../etc", runs_root=tmp_path)


def test_loop_detail(tmp_path):
    d = tmp_path / SID
    d.mkdir()
    (d / "draft.json").write_text(json.dumps({"sections": [
        {"cat": "Chips", "iterations": 2, "scores": [7.2, 8.3], "accepted": True}]}))
    (d / "rewrite.json").write_text(json.dumps({"title": "T", "transcript": {
        "iterations": 2, "scores": [7.3, 8.2]}}))
    lp = loop_detail(SID, tmp_path)
    assert lp["draft"][0]["scores"] == [7.2, 8.3]
    assert lp["rewrite"]["scores"] == [7.3, 8.2] and lp["rewrite"]["title"] == "T"
    assert loop_detail("other", tmp_path) == {"draft": None, "rewrite": None}


def test_step_doc_strips_frontmatter(tmp_path):
    p = tmp_path / "skills" / "rate"
    p.mkdir(parents=True)
    (p / "SKILL.md").write_text("---\nname: rate\ndescription: Rates things\n---\n# Rate\n\nBody")
    doc = step_doc("rate", tmp_path)
    assert doc["description"] == "Rates things"
    assert "<h1>Rate</h1>" in doc["html"] and "name: rate" not in doc["html"]
    with pytest.raises(GuiError):
        step_doc("bogus", tmp_path)


def test_step_code_finds_prompts(tmp_path):
    (tmp_path / "lib" / "steps").mkdir(parents=True)
    (tmp_path / "lib" / "prompts").mkdir(parents=True)
    (tmp_path / "lib" / "prompts" / "filter_urls.py").write_text("P = 1\n")
    (tmp_path / "lib" / "prompts" / "unused.py").write_text("U = 1\n")
    (tmp_path / "lib" / "steps" / "filter.py").write_text(
        'from lib.llm import call_prompt\ncall_prompt("filter_urls", {})\n')
    paths = [f["path"] for f in step_code("filter", tmp_path)["files"]]
    assert paths == ["lib/steps/filter.py", "lib/prompts/filter_urls.py"]


def test_step_code_real_repo():
    paths = [f["path"] for f in step_code("rate")["files"]]
    assert paths[0] == "lib/steps/rate.py"


def test_step_log_offsets(tmp_path):
    assert step_log(SID, "rate", 0, tmp_path)["exists"] is False
    (tmp_path / SID).mkdir()
    log = tmp_path / SID / "rate.log"
    log.write_text("one\n")
    r1 = step_log(SID, "rate", 0, tmp_path)
    assert r1["text"] == "one\n" and r1["offset"] == 4
    with open(log, "a") as fh:
        fh.write("two\n")
    r2 = step_log(SID, "rate", r1["offset"], tmp_path)
    assert r2["text"] == "two\n" and r2["offset"] == 8
    # offset beyond size (file replaced) restarts from 0
    assert step_log(SID, "rate", 999, tmp_path)["text"] == "one\ntwo\n"


def test_step_state_table_sorted_and_stats(tmp_db, tmp_path):
    _seed(tmp_db, through="rate")
    (tmp_path / SID).mkdir()
    (tmp_path / SID / "rate.json").write_text(json.dumps({"total_rated": 2}))
    s = step_state(tmp_db, SID, "rate", tmp_path)
    assert s["available"] and s["status_message"] == "rate done"
    assert s["stats"]["headlines"] == 2 and s["stats"]["top rating"] == 0.9
    assert s["stats"]["coverage boosts"] == 1
    cols = s["table"]["columns"]
    assert "embedding" not in cols and cols[:2] == ["title", "site_name"]
    assert s["table"]["rows"][0][0] == "High"
    assert s["summary"] == {"total_rated": 2} and "rate.json" in s["artifacts"]


def test_step_state_not_run(tmp_db, tmp_path):
    _seed(tmp_db, through="gather")
    s = step_state(tmp_db, SID, "cluster", tmp_path)
    assert s["available"] is False


def test_step_state_select_uses_section_rows(tmp_db, tmp_path):
    st = _seed(tmp_db, through="cluster")
    st.newsletter_section_data = [
        {"cat": "Chips", "headline": "H1", "link": "https://x/1", "site_name": "S", "rating": 5.0}]
    st.start_step("select")
    st.complete_step("select", message="1 headline")
    st.save_checkpoint("select")
    t = step_state(tmp_db, SID, "select", tmp_path)["table"]
    assert t["columns"] == ["headline", "site_name", "cat", "rating", "link"]
    assert t["rows"][0][0] == "H1"


def test_step_span_start_uses_first_invocation_of_last_attempt():
    from datetime import datetime as dt
    from lib.gui import step_span_start
    t = lambda s: dt.fromisoformat("2026-11-22T" + s)
    runs = [t("09:01:22"), t("09:09:39"), t("09:10:27")]  # prepare, re-prepare, apply
    assert step_span_start(runs, t("09:01:21"), t("09:10:27")) == (t("09:01:22"), 3)
    # an earlier attempt more than an hour before is a separate try
    runs2 = [t("06:00:00"), t("09:01:22"), t("09:10:27")]
    assert step_span_start(runs2, None, t("09:10:27")) == (t("09:01:22"), 2)
    # invocations before the previous step finished, or after this one, don't count
    assert step_span_start([t("08:00:00"), t("10:00:00")], t("09:00:00"), t("09:30:00")) == (None, 0)


def test_overview_duration_spans_prepare_to_apply(tmp_db, tmp_path):
    _seed(tmp_db, through="crossdedupe")
    from lib.gui import _latest_steps
    steps = _latest_steps(tmp_db, SID)
    summ = next(s for s in steps if s["id"] == "summarize")
    cross = next(s for s in steps if s["id"] == "crossdedupe")
    (tmp_path / SID).mkdir()
    # first invocation logged when summarize finished (seconds precision), i.e. earlier
    # than the checkpoint's started_at
    from datetime import datetime as dt
    first = dt.fromisoformat(summ["completed_at"]).isoformat(timespec="seconds")
    (tmp_path / SID / "crossdedupe.log").write_text(
        f"=== crossdedupe {first} pid=1 argv=--prepare-batches\n12:00:00 x\n")
    ov = session_overview(tmp_db, SID, runs_root=tmp_path)
    c = next(s for s in ov["steps"] if s["id"] == "crossdedupe")
    assert c["started_at"] == first and c["invocations"] == 1
    assert c["duration_s"] == pytest.approx(
        (dt.fromisoformat(cross["completed_at"]) - dt.fromisoformat(first)).total_seconds())


def test_step_output(tmp_path):
    from lib.gui import step_output
    assert step_output(SID, "download", tmp_path)["exists"] is False
    (tmp_path / SID).mkdir()
    (tmp_path / SID / "download.json").write_text(json.dumps({"downloaded": 3, "failures": []}))
    out = step_output(SID, "download", tmp_path)
    assert out["exists"] and out["data"]["downloaded"] == 3
    assert out["path"] == f"runs/{SID}/download.json"


def test_step_artifacts_and_file_guard(tmp_db, tmp_path):
    from lib.gui import artifact_file, step_artifacts
    root = tmp_path
    _seed(tmp_db, through="download", headlines=[
        {"title": "A", "text_path": "download/txt/a.txt", "html_path": "download/html/a.html"}])
    runs = root / "runs" / SID
    (runs / "download-batches").mkdir(parents=True)
    (runs / "download.json").write_text("{}")
    (runs / "download.log").write_text("x\n")
    (runs / "download-batches" / "batch-000.json").write_text("[]")
    (runs / "downloadish.txt").write_text("not mine")      # different step-name prefix
    (runs / "sitename.json").write_text("{}")
    (root / "download" / "txt").mkdir(parents=True)
    (root / "download" / "txt" / "a.txt").write_text("body")
    (root / "secret.txt").write_text("nope")
    groups = step_artifacts(tmp_db, SID, "download", "runs", root)["groups"]
    paths = {g["label"].split(" ")[0]: [f["path"] for f in g["files"]] for g in groups}
    own = paths[f"runs/{SID}/"]
    assert f"runs/{SID}/download.json" in own and f"runs/{SID}/download-batches/batch-000.json" in own
    assert not any("downloadish" in p for p in own)
    assert paths["Site-name"] == [f"runs/{SID}/sitename.json"]
    assert paths["Article"] == ["download/txt/a.txt"]  # html cache file absent → skipped
    p, size = artifact_file(tmp_db, SID, "download", "download/txt/a.txt", "runs", root)
    assert p.read_text() == "body" and size == 4
    for bad in ("secret.txt", "../secret.txt", f"runs/{SID}/downloadish.txt"):
        with pytest.raises(GuiError):
            artifact_file(tmp_db, SID, "download", bad, "runs", root)


def test_artifacts_out_files(tmp_db, tmp_path):
    from lib.gui import step_artifacts
    _seed(tmp_db, through="send")
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "2026-11-21-070030.html").write_text("<p>hi</p>")
    (tmp_path / "out" / "2026-11-21-070030_short.html").write_text("<p>s</p>")
    send = step_artifacts(tmp_db, SID, "send", "runs", tmp_path)["groups"]
    assert send[-1]["files"][0]["path"] == "out/2026-11-21-070030.html"
    rate = step_artifacts(tmp_db, SID, "rate", "runs", tmp_path)["groups"]
    assert rate[-1]["files"][0]["path"] == "out/2026-11-21-070030_short.html"
