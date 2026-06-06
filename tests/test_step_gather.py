import json
import sqlite3
from pathlib import Path
from unittest.mock import patch
from click.testing import CliRunner

from lib.db import init_db, Site
from lib.fetch.types import Article, FetchResult
from lib.state import NewsletterAgentState
from lib.steps.gather import cli as gather_cli


def _make_session(tmp_db, sources_yaml_path):
    """Helper to set up a session ready for gather."""
    import yaml
    sources = yaml.safe_load(Path(sources_yaml_path).read_text())["sources"]
    init_db(tmp_db)
    state = NewsletterAgentState(
        session_id="g1", db_path=tmp_db,
        sources_file=sources_yaml_path, sources=sources,
    )
    state.complete_step("start")
    state.save_checkpoint("start")
    return state


def test_gather_calls_rss_fetcher_for_rss_sources(tmp_path, tmp_db):
    yaml_path = tmp_path / "sources.yaml"
    yaml_path.write_text(
        "sources:\n"
        "  Feed1:\n    type: rss\n    url: https://feed.example.com/rss\n    enabled: true\n"
    )
    _make_session(tmp_db, str(yaml_path))

    rss_result = FetchResult(source="Feed1", ok=True, articles=[
        Article(source="Feed1", title="A", url="https://feed.example.com/a"),
    ])
    with patch("lib.steps.gather.fetch_rss", return_value=rss_result) as mock_rss:
        runner = CliRunner()
        result = runner.invoke(gather_cli, ["--db", tmp_db, "--session", "g1"])
    assert result.exit_code == 0, result.output
    mock_rss.assert_called_once()


def test_gather_writes_urls_to_db_and_state(tmp_path, tmp_db):
    yaml_path = tmp_path / "sources.yaml"
    yaml_path.write_text(
        "sources:\n  Feed1:\n    type: rss\n    url: https://feed.example.com/rss\n    enabled: true\n"
    )
    _make_session(tmp_db, str(yaml_path))

    rss_result = FetchResult(source="Feed1", ok=True, articles=[
        Article(source="Feed1", title="A long enough headline", url="https://feed.example.com/a"),
        Article(source="Feed1", title="B long enough headline", url="https://feed.example.com/b"),
    ])
    with patch("lib.steps.gather.fetch_rss", return_value=rss_result):
        runner = CliRunner()
        runner.invoke(gather_cli, ["--db", tmp_db, "--session", "g1"])

    with sqlite3.connect(tmp_db) as conn:
        rows = conn.execute("SELECT initial_url FROM urls").fetchall()
    assert {r[0] for r in rows} == {"https://feed.example.com/a", "https://feed.example.com/b"}

    state = NewsletterAgentState(session_id="g1", db_path=tmp_db).load_latest_from_db()
    assert state is not None
    assert len(state.headline_data) == 2


def test_gather_dedups_against_existing_urls(tmp_path, tmp_db):
    yaml_path = tmp_path / "sources.yaml"
    yaml_path.write_text(
        "sources:\n  Feed1:\n    type: rss\n    url: https://feed.example.com/rss\n    enabled: true\n"
    )
    _make_session(tmp_db, str(yaml_path))

    with sqlite3.connect(tmp_db) as conn:
        conn.execute(
            "INSERT INTO urls (initial_url, final_url, title, source) VALUES (?,?,?,?)",
            ("https://feed.example.com/a", "https://feed.example.com/a", "A", "Feed1"),
        )
        conn.commit()

    rss_result = FetchResult(source="Feed1", ok=True, articles=[
        Article(source="Feed1", title="A long enough headline", url="https://feed.example.com/a"),  # dupe
        Article(source="Feed1", title="B long enough headline", url="https://feed.example.com/b"),  # new
    ])
    with patch("lib.steps.gather.fetch_rss", return_value=rss_result):
        runner = CliRunner()
        runner.invoke(gather_cli, ["--db", tmp_db, "--session", "g1"])

    state = NewsletterAgentState(session_id="g1", db_path=tmp_db).load_latest_from_db()
    assert len(state.headline_data) == 1
    assert state.headline_data[0]["url"] == "https://feed.example.com/b"


def test_gather_does_not_auto_pin_scrape_method(tmp_path, tmp_db):
    """sites.scrape_method is operator-curated config. gather must not
    auto-write it based on whichever method happened to work this run."""
    yaml_path = tmp_path / "sources.yaml"
    yaml_path.write_text(
        "sources:\n  Site1:\n    type: html\n    url: https://news.example.com/\n    enabled: true\n"
    )
    _make_session(tmp_db, str(yaml_path))

    html_result = FetchResult(source="Site1", ok=True, articles=[
        Article(source="Site1", title="Long enough headline X", url="https://news.example.com/a"),
    ])
    with patch("lib.steps.gather.fetch_html",
               return_value=(html_result, "playwright", "<html>raw</html>")):
        runner = CliRunner()
        result = runner.invoke(gather_cli, ["--db", tmp_db, "--session", "g1"])

    assert result.exit_code == 0, result.output
    with sqlite3.connect(tmp_db) as conn:
        s = Site.get_by_domain(conn, "news.example.com")
    if s is not None:
        assert s.scrape_method is None
    # And the operator-facing nudge should appear, suggesting they pin it.
    assert "scrape_method='playwright'" in result.output


def test_gather_persists_front_page_html(tmp_path, tmp_db, monkeypatch):
    monkeypatch.chdir(tmp_path)
    yaml_path = tmp_path / "sources.yaml"
    yaml_path.write_text(
        "sources:\n  Site 1:\n    type: html\n    url: https://news.example.com/\n    enabled: true\n"
    )
    _make_session(tmp_db, str(yaml_path))

    html_result = FetchResult(source="Site 1", ok=True, articles=[
        Article(source="Site 1", title="Long enough headline X", url="https://news.example.com/a"),
    ])
    with patch("lib.steps.gather.fetch_html",
               return_value=(html_result, "http", "<html>FRONT</html>")):
        runner = CliRunner()
        runner.invoke(gather_cli, ["--db", tmp_db, "--session", "g1"])

    page = Path("runs/g1/pages/Site_1.html")
    assert page.exists()
    assert page.read_text() == "<html>FRONT</html>"

    report = json.loads(Path("runs/g1/gather.json").read_text())
    assert any(s.get("page_path", "").endswith("Site_1.html") for s in report["sources"])


def test_gather_cached_pages_reads_html_from_disk(tmp_path, tmp_db, monkeypatch):
    monkeypatch.chdir(tmp_path)
    yaml_path = tmp_path / "sources.yaml"
    yaml_path.write_text(
        "sources:\n  Site1:\n    type: html\n    url: https://news.example.com/\n    enabled: true\n"
    )
    _make_session(tmp_db, str(yaml_path))

    page_dir = Path("runs/g1/pages")
    page_dir.mkdir(parents=True, exist_ok=True)
    (page_dir / "Site1.html").write_text(
        '<html><body>'
        '<a href="https://news.example.com/story-one">'
        'A sufficiently long story headline here</a>'
        '</body></html>'
    )

    def _boom(*a, **k):
        raise AssertionError("fetch_html must not be called with --cached-pages")

    with patch("lib.steps.gather.fetch_html", side_effect=_boom):
        runner = CliRunner()
        result = runner.invoke(
            gather_cli, ["--db", tmp_db, "--session", "g1", "--cached-pages"]
        )

    assert result.exit_code == 0, result.output
    with sqlite3.connect(tmp_db) as conn:
        rows = {r[0] for r in conn.execute("SELECT initial_url FROM urls").fetchall()}
    assert "https://news.example.com/story-one" in rows


def test_gather_cached_pages_still_fetches_rss_live(tmp_path, tmp_db, monkeypatch):
    monkeypatch.chdir(tmp_path)
    yaml_path = tmp_path / "sources.yaml"
    yaml_path.write_text(
        "sources:\n"
        "  Site1:\n    type: html\n    url: https://news.example.com/\n    enabled: true\n"
        "  Feed1:\n    type: rss\n    url: https://feed.example.com/rss\n    enabled: true\n"
    )
    _make_session(tmp_db, str(yaml_path))

    page_dir = Path("runs/g1/pages")
    page_dir.mkdir(parents=True, exist_ok=True)
    (page_dir / "Site1.html").write_text(
        '<html><body>'
        '<a href="https://news.example.com/story-one">'
        'A sufficiently long story headline here</a>'
        '</body></html>'
    )

    rss_result = FetchResult(source="Feed1", ok=True, articles=[
        Article(source="Feed1", title="A sufficiently long rss headline here",
                url="https://feed.example.com/a"),
    ])
    with patch("lib.steps.gather.fetch_html",
               side_effect=AssertionError("html must come from cache")), \
         patch("lib.steps.gather.fetch_rss", return_value=rss_result) as mock_rss:
        runner = CliRunner()
        result = runner.invoke(
            gather_cli, ["--db", tmp_db, "--session", "g1", "--cached-pages"]
        )

    assert result.exit_code == 0, result.output
    mock_rss.assert_called_once()
    with sqlite3.connect(tmp_db) as conn:
        rows = {r[0] for r in conn.execute("SELECT initial_url FROM urls").fetchall()}
    assert "https://feed.example.com/a" in rows
    assert "https://news.example.com/story-one" in rows


def test_gather_writes_report_json(tmp_path, tmp_db, monkeypatch):
    monkeypatch.chdir(tmp_path)
    yaml_path = tmp_path / "sources.yaml"
    yaml_path.write_text(
        "sources:\n  Feed1:\n    type: rss\n    url: https://feed.example.com/rss\n    enabled: true\n"
    )
    _make_session(tmp_db, str(yaml_path))

    with patch("lib.steps.gather.fetch_rss",
               return_value=FetchResult(source="Feed1", ok=True, articles=[])):
        runner = CliRunner()
        runner.invoke(gather_cli, ["--db", tmp_db, "--session", "g1"])

    report_path = Path("runs/g1/gather.json")
    assert report_path.exists()
    report = json.loads(report_path.read_text())
    assert "sources" in report
    assert any(s["source"] == "Feed1" for s in report["sources"])


def test_gather_halts_when_source_extracts_zero(tmp_path, tmp_db, monkeypatch):
    monkeypatch.chdir(tmp_path)
    yaml_path = tmp_path / "sources.yaml"
    yaml_path.write_text(
        "sources:\n"
        "  Good:\n    type: rss\n    url: https://feed.example.com/rss\n    enabled: true\n"
        "  Bad:\n    type: rss\n    url: https://bad.example.com/rss\n    enabled: true\n"
    )
    _make_session(tmp_db, str(yaml_path))

    def _rss(source_name, url):
        if source_name == "Good":
            return FetchResult(source="Good", ok=True, articles=[
                Article(source="Good", title="A sufficiently long good headline here",
                        url="https://feed.example.com/a"),
            ])
        return FetchResult(source="Bad", ok=False, error="boom", articles=[])

    with patch("lib.steps.gather.fetch_rss", side_effect=_rss):
        runner = CliRunner()
        result = runner.invoke(gather_cli, ["--db", tmp_db, "--session", "g1"])

    # Halts with a non-zero exit code.
    assert result.exit_code != 0, result.output
    # Working source's URL is still persisted.
    with sqlite3.connect(tmp_db) as conn:
        rows = {r[0] for r in conn.execute("SELECT initial_url FROM urls").fetchall()}
    assert "https://feed.example.com/a" in rows
    # Step is left in ERROR so resume picks it up.
    state = NewsletterAgentState(session_id="g1", db_path=tmp_db).load_latest_from_db()
    assert state.get_current_step() == "gather"
    # gather.json lists the empty source.
    report = json.loads(Path("runs/g1/gather.json").read_text())
    assert [e["source"] for e in report["empty_sources"]] == ["Bad"]


def test_gather_does_not_halt_on_zero_new_after_dedup(tmp_path, tmp_db, monkeypatch):
    monkeypatch.chdir(tmp_path)
    yaml_path = tmp_path / "sources.yaml"
    yaml_path.write_text(
        "sources:\n  Feed1:\n    type: rss\n    url: https://feed.example.com/rss\n    enabled: true\n"
    )
    _make_session(tmp_db, str(yaml_path))

    # Pre-seed the only article so it dedups to 0 new — but extracted > 0.
    with sqlite3.connect(tmp_db) as conn:
        conn.execute(
            "INSERT INTO urls (initial_url, final_url, title, source) VALUES (?,?,?,?)",
            ("https://feed.example.com/a", "https://feed.example.com/a", "A", "Feed1"),
        )
        conn.commit()

    rss_result = FetchResult(source="Feed1", ok=True, articles=[
        Article(source="Feed1", title="A sufficiently long headline here",
                url="https://feed.example.com/a"),
    ])
    with patch("lib.steps.gather.fetch_rss", return_value=rss_result):
        runner = CliRunner()
        result = runner.invoke(gather_cli, ["--db", tmp_db, "--session", "g1"])

    assert result.exit_code == 0, result.output
    state = NewsletterAgentState(session_id="g1", db_path=tmp_db).load_latest_from_db()
    # gather completed, so the first non-complete step is the next one (filter).
    assert state.get_current_step() == "filter"
