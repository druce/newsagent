"""Tests for evals.run.excerpts — the feed excerpt a title+excerpt filter would see."""
from evals.run import excerpts as E

FEEDLY = """
<div class="entry"><div class="y1"><p><a class="EntryTitleLink x" href="https://www.businessinsider.com/google-spirit-2026-11?utm_source=feedly">Google's $10 million plan</a></p></div>
<div class="meta"><div class="EntryMetadataWrapper"><a href="/i/subscription/x">Business Insider</a> / <span>3min</span></div></div>
<div class="snip">The Association of Flight Attendants said Tuesday it reached a deal.</div></div>
<div class="entry"><div class="y1"><p><a class="EntryTitleLink" href="https://example.com/no-snippet">No snippet here</a></p></div>
<div class="meta"><div class="EntryMetadataWrapper"><a href="/i/s">Src</a> / <span>1h</span></div></div></div>
"""


def test_feedly_excerpts_maps_cleaned_url_to_snippet():
    ex = E.feedly_excerpts(FEEDLY)
    assert ex == {"https://www.businessinsider.com/google-spirit-2026-11":
                  "The Association of Flight Attendants said Tuesday it reached a deal."}


def test_clean_rss_summary_strips_html_and_drops_hn_comment_links():
    assert E.clean_summary('<a href="https://news.ycombinator.com/item?id=1">Comments</a>') == ""
    assert E.clean_summary("<p>An <b>AI</b> lab&nbsp;raised money.</p>") == "An AI lab raised money."
    assert len(E.clean_summary("x " * 1000)) <= E.MAX_CHARS


def test_excerpt_prefers_rss_summary_then_page_dek_then_feedly():
    assert E.pick("rss", "dek", "snip") == ("rss", "rss")
    assert E.pick("", "dek", "snip") == ("dek", "page")
    assert E.pick("", "", "snip") == ("snip", "feedly")
    assert E.pick("", "", "") == ("", "none")


WSJ_PAGE = """<h3><a data-testid="flexcard-headline" href="https://www.wsj.com/tech/ai/nvidia-x?mod=a">Even at $5 Trillion, Nvidia Is Underappreciated</a></h3>
<div><a href="https://www.wsj.com/tech/ai/nvidia-x?mod=a"><p>Competition is growing, but the AI chip maker's sluggish stock doesn't give enough credit.</p></a></div>"""


def test_build_excerpts_reextracts_html_headline_and_dek(tmp_path, monkeypatch):
    import gzip, json
    sid = "2026-05-21-183344"
    (tmp_path / "runs" / sid / "pages").mkdir(parents=True)
    (tmp_path / "runs" / sid / "pages" / "WSJ.html").write_text(WSJ_PAGE)
    (tmp_path / "archive").mkdir()
    with gzip.open(tmp_path / "archive" / "ckpt_gather.jsonl.gz", "wt") as f:
        f.write(json.dumps({"sid": sid, "items": [
            {"id": 7, "source": "WSJ", "url": "https://www.wsj.com/tech/ai/nvidia-x",
             "title": "Competition is growing, but the AI chip maker's sluggish stock doesn't give enough credit."},
            {"id": 8, "source": "NYT", "url": "https://nyt.com/a", "title": "NYT head", "rss_summary": "<p>A dek.</p>"},
        ]}) + "\n")
    monkeypatch.setattr(E, "ARCHIVE", tmp_path / "archive")
    monkeypatch.setattr(E, "RUNS", tmp_path / "runs")
    monkeypatch.setattr(E, "SOURCES", {"WSJ": {"type": "html", "url": "https://www.wsj.com/tech/ai", "filename": "WSJ"}})
    run = tmp_path / "run"
    run.mkdir()
    (run / "cases.jsonl").write_text("".join(json.dumps({"case_id": f"filter_urls:{sid}:batch-000:{i}", "strata": {"source": s}}) + "\n"
                                             for i, s in ((7, "WSJ"), (8, "NYT"))))
    rows = {r["case_id"].rsplit(":", 1)[1]: r for r in E.build_excerpts(run)}
    assert rows["7"]["title"] == "Even at $5 Trillion, Nvidia Is Underappreciated"
    assert rows["7"]["excerpt"].startswith("Competition is growing") and rows["7"]["excerpt_source"] == "page"
    assert rows["8"]["title"] == "NYT head" and rows["8"]["excerpt"] == "A dek." and rows["8"]["excerpt_source"] == "rss"


def test_build_excerpts_rejoins_by_title_when_id_points_at_another_story(tmp_path, monkeypatch):
    import gzip, json
    sid = "2026-07-23-07-00-10"
    (tmp_path / "archive").mkdir()
    with gzip.open(tmp_path / "archive" / "ckpt_gather.jsonl.gz", "wt") as f:
        f.write(json.dumps({"sid": sid, "items": [
            {"id": 307, "source": "NewsAPI", "url": "u1", "title": "Other story", "rss_summary": "wrong dek"},
            {"id": 900, "source": "NewsAPI", "url": "u2", "title": "Astera Labs expands", "rss_summary": "right dek"},
        ]}) + "\n")
    monkeypatch.setattr(E, "ARCHIVE", tmp_path / "archive")
    monkeypatch.setattr(E, "RUNS", tmp_path / "runs")
    monkeypatch.setattr(E, "SOURCES", {})
    run = tmp_path / "run"
    run.mkdir()
    (run / "cases.jsonl").write_text(json.dumps({
        "case_id": f"filter_urls:{sid}:batch-012:307", "strata": {"source": "NewsAPI"},
        "input": {"items": [{"id": "307", "title": "Astera Labs expands"}]}}) + "\n")
    (row,) = E.build_excerpts(run)
    assert row["title"] == "Astera Labs expands" and row["excerpt"] == "right dek"
