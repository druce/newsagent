from unittest.mock import patch
from click.testing import CliRunner
from lib.db import init_db
from lib.state import NewsletterAgentState
from lib.steps.dedupe import cli as dedupe_cli


def _seed(tmp_db, tmp_path):
    init_db(tmp_db)
    from pathlib import Path
    dl = Path(tmp_path) / "download"
    dl.mkdir(exist_ok=True)
    body_a = dl / "a.txt"; body_a.write_text("Story A about chip supply chain " * 50)
    body_b = dl / "b.txt"; body_b.write_text("Story A about chip supply chain " * 50)
    body_c = dl / "c.txt"; body_c.write_text("Different story about coral reefs " * 50)
    state = NewsletterAgentState(session_id="d1", db_path=tmp_db)
    state.complete_step("start")
    state.complete_step("gather")
    state.complete_step("filter")
    state.complete_step("download")
    state.headline_data = [
        {"id": 0, "title": "T1", "url": "https://e.com/a",
         "summary": "alpha beta gamma", "text_path": str(body_a)},
        {"id": 1, "title": "T1b", "url": "https://e.com/b",
         "summary": "alpha beta gamma", "text_path": str(body_b)},  # near-dupe body
        {"id": 2, "title": "T2", "url": "https://e.com/c",
         "summary": "different topic", "text_path": str(body_c)},
    ]
    state.save_checkpoint("download")


def test_dedupe_drops_near_duplicates(tmp_db, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    _seed(tmp_db, tmp_path)
    # First two are identical vectors; third is orthogonal
    vectors = [[1.0, 0.0], [1.0, 0.0], [0.0, 1.0]]
    with patch("lib.steps.dedupe.embed_texts", return_value=vectors):
        runner = CliRunner()
        result = runner.invoke(dedupe_cli, ["--db", tmp_db, "--session", "d1"])
    assert result.exit_code == 0, result.output
    state = NewsletterAgentState(session_id="d1", db_path=tmp_db).load_latest_from_db()
    urls = [h["url"] for h in state.headline_data]
    assert len(urls) == 2
    assert "https://e.com/c" in urls


def test_dedupe_preserves_embeddings(tmp_db, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    _seed(tmp_db, tmp_path)
    vectors = [[1.0, 0.0], [0.5, 0.5], [0.0, 1.0]]  # none near-duplicate
    with patch("lib.steps.dedupe.embed_texts", return_value=vectors):
        runner = CliRunner()
        runner.invoke(dedupe_cli, ["--db", tmp_db, "--session", "d1"])
    state = NewsletterAgentState(session_id="d1", db_path=tmp_db).load_latest_from_db()
    assert all("embedding" in h for h in state.headline_data)
    assert len(state.headline_data) == 3


def test_dedupe_no_summaries_noop(tmp_db, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    init_db(tmp_db)
    state = NewsletterAgentState(session_id="empty", db_path=tmp_db)
    state.complete_step("start")
    state.save_checkpoint("start")
    runner = CliRunner()
    result = runner.invoke(dedupe_cli, ["--db", tmp_db, "--session", "empty"])
    assert result.exit_code == 0
    assert "nothing" in result.output.lower() or "0" in result.output


# ── same-target collapse (several original urls → one final_url) ──────────

from lib.steps.dedupe import _collapse_same_target


def test_collapse_same_target_prefers_direct_link():
    hs = [
        {"id": 0, "url": "https://news.google.com/x", "final_url": "https://e.com/a"},
        {"id": 1, "url": "https://e.com/a", "final_url": "https://e.com/a"},
        {"id": 2, "url": "https://e.com/c", "final_url": "https://e.com/c"},
    ]
    kept, dropped = _collapse_same_target(hs)
    assert [h["id"] for h in kept] == [1, 2]
    assert [h["id"] for h in dropped] == [0]


def test_collapse_same_target_keeps_earliest_when_no_direct_link():
    hs = [
        {"id": 0, "url": "https://t.co/1", "final_url": "https://e.com/a"},
        {"id": 1, "url": "https://lnkd.in/2", "final_url": "https://e.com/a"},
    ]
    kept, dropped = _collapse_same_target(hs)
    assert [h["id"] for h in kept] == [0]
    assert [h["id"] for h in dropped] == [1]


def test_collapse_same_target_keys_on_text_path_without_final_url():
    hs = [
        {"id": 0, "url": "https://a.com/1", "text_path": "download/abc.txt"},
        {"id": 1, "url": "https://b.com/2", "text_path": "download/abc.txt"},
        {"id": 2, "url": "https://c.com/3"},  # no body, no final_url: keyed on url
        {"id": 3, "url": "https://c.com/3"},
    ]
    kept, dropped = _collapse_same_target(hs)
    assert [h["id"] for h in kept] == [0, 2]
    assert sorted(h["id"] for h in dropped) == [1, 3]


def test_dedupe_collapses_shared_final_url_before_embedding(tmp_db, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    init_db(tmp_db)
    from pathlib import Path
    import json
    dl = Path(tmp_path) / "download"; dl.mkdir(exist_ok=True)
    body = dl / "same.txt"; body.write_text("Shared body " * 50)
    other = dl / "other.txt"; other.write_text("Other body " * 50)
    state = NewsletterAgentState(session_id="d2", db_path=tmp_db)
    state.complete_step("start")
    state.headline_data = [
        {"id": 0, "title": "via aggregator", "url": "https://news.google.com/r/1",
         "final_url": "https://e.com/a", "text_path": str(body)},
        {"id": 1, "title": "direct", "url": "https://e.com/a",
         "final_url": "https://e.com/a", "text_path": str(body)},
        {"id": 2, "title": "no body", "url": "https://t.co/z",
         "final_url": "https://e.com/a"},
        {"id": 3, "title": "other", "url": "https://e.com/o",
         "final_url": "https://e.com/o", "text_path": str(other)},
    ]
    state.save_checkpoint("download")
    captured = {}

    def fake_embed(texts):
        captured["n"] = len(texts)
        return [[1.0, 0.0], [0.0, 1.0]][: len(texts)]

    with patch("lib.steps.dedupe.embed_texts", side_effect=fake_embed):
        result = CliRunner().invoke(dedupe_cli, ["--db", tmp_db, "--session", "d2"])
    assert result.exit_code == 0, result.output
    assert captured["n"] == 2  # only the kept direct link + "other" embedded
    state = NewsletterAgentState(session_id="d2", db_path=tmp_db).load_latest_from_db()
    assert [h["title"] for h in state.headline_data] == ["direct", "other"]
    report = json.loads((Path(tmp_path) / "runs" / "d2" / "dedupe.json").read_text())
    assert report["collapsed_same_url"] == 2


def test_dedupe_drops_by_identity_not_original_url(tmp_db, monkeypatch, tmp_path):
    """Two headlines sharing an ORIGINAL url but resolving to different
    final_urls: dropping one near-duplicate must not also drop the other."""
    monkeypatch.chdir(tmp_path)
    init_db(tmp_db)
    from pathlib import Path
    dl = Path(tmp_path) / "download"; dl.mkdir(exist_ok=True)
    a = dl / "a.txt"; a.write_text("Body A " * 50)
    b = dl / "b.txt"; b.write_text("Body A " * 50)
    c = dl / "c.txt"; c.write_text("Body C " * 50)
    state = NewsletterAgentState(session_id="d3", db_path=tmp_db)
    state.complete_step("start")
    state.headline_data = [
        {"id": 0, "title": "A", "url": "https://e.com/a", "final_url": "https://e.com/a",
         "text_path": str(a)},
        {"id": 1, "title": "B", "url": "https://agg.com/same", "final_url": "https://f.com/b",
         "text_path": str(b)},
        {"id": 2, "title": "C", "url": "https://agg.com/same", "final_url": "https://g.com/c",
         "text_path": str(c)},
    ]
    state.save_checkpoint("download")
    vectors = [[1.0, 0.0], [1.0, 0.0], [0.0, 1.0]]  # A≈B; C distinct
    with patch("lib.steps.dedupe.embed_texts", return_value=vectors):
        result = CliRunner().invoke(dedupe_cli, ["--db", tmp_db, "--session", "d3"])
    assert result.exit_code == 0, result.output
    state = NewsletterAgentState(session_id="d3", db_path=tmp_db).load_latest_from_db()
    assert [h["title"] for h in state.headline_data] == ["A", "C"]
