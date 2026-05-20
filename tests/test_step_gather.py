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
    state.complete_step("init")
    state.save_checkpoint("init")
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


def test_gather_updates_scrape_method_for_html_sources(tmp_path, tmp_db):
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
        runner.invoke(gather_cli, ["--db", tmp_db, "--session", "g1"])

    with sqlite3.connect(tmp_db) as conn:
        s = Site.get_by_domain(conn, "news.example.com")
    assert s is not None
    assert s.scrape_method == "playwright"


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
