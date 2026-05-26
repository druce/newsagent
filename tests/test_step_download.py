import json
import sqlite3
from pathlib import Path
from unittest.mock import patch
from click.testing import CliRunner

from lib.db import init_db, Site
from lib.state import NewsletterAgentState
from lib.steps.download import cli as download_cli


def _body(label: str) -> str:
    """An extracted article body comfortably above the 500-char min."""
    return f"Article body {label}. " + ("Long-form text. " * 50)


def _setup(tmp_db):
    init_db(tmp_db)
    state = NewsletterAgentState(session_id="d1", db_path=tmp_db)
    state.complete_step("start")
    state.complete_step("gather")
    state.headline_data = [
        {"source": "S", "title": "T1", "url": "https://example.com/a"},
        {"source": "S", "title": "T2", "url": "https://example.com/b"},
    ]
    state.save_checkpoint("gather")
    with sqlite3.connect(tmp_db) as conn:
        for h in state.headline_data:
            conn.execute(
                "INSERT INTO urls (initial_url, final_url, title, source) "
                "VALUES (?, ?, ?, ?)",
                (h["url"], h["url"], h["title"], h["source"]),
            )
        conn.commit()
    return state


def test_download_phase1_http_success_writes_paired_files(tmp_path, tmp_db, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _setup(tmp_db)

    def fake_http(url):
        if url.endswith("/a"):
            return _body("A"), "<html>RAW-A</html>", "https://canonical.example.com/article-a", None
        return _body("B"), "<html>RAW-B</html>", "https://example.com/b", None

    with patch("lib.steps.download._http_fetch", side_effect=fake_http):
        with patch("lib.steps.download.fetch_urls_html_batch") as pw:
            runner = CliRunner()
            result = runner.invoke(download_cli, ["--db", tmp_db, "--session", "d1"])
    assert result.exit_code == 0, result.output
    pw.assert_not_called()

    state = NewsletterAgentState(session_id="d1", db_path=tmp_db).load_latest_from_db()
    for h in state.headline_data:
        txt = Path(h["text_path"])
        html = Path(h["html_path"])
        assert txt.exists() and html.exists()
        assert txt.parent.name == "txt"
        assert html.parent.name == "html"
        # Paired filenames: same stem, different extension
        assert txt.stem == html.stem
        # Domain-prefixed
        assert txt.name.startswith(h["final_url"].split("//")[1].split("/")[0] + ".")

    # urls.final_url should reflect redirect
    with sqlite3.connect(tmp_db) as conn:
        rows = dict(conn.execute("SELECT initial_url, final_url FROM urls").fetchall())
    assert rows["https://example.com/a"] == "https://canonical.example.com/article-a"


def test_download_clears_directories_at_start(tmp_path, tmp_db, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _setup(tmp_db)

    # Pre-populate with stale files that must be deleted before the run
    Path("download/txt").mkdir(parents=True)
    Path("download/html").mkdir(parents=True)
    Path("download/txt/stale.txt").write_text("OLD")
    Path("download/html/stale.html").write_text("OLD")

    def fake_http(url):
        return _body("ok"), "<html>ok</html>", url, None

    with patch("lib.steps.download._http_fetch", side_effect=fake_http):
        runner = CliRunner()
        runner.invoke(download_cli, ["--db", tmp_db, "--session", "d1"])

    assert not Path("download/txt/stale.txt").exists()
    assert not Path("download/html/stale.html").exists()
    # The two new articles should be there
    assert len(list(Path("download/txt").glob("*.txt"))) == 2
    assert len(list(Path("download/html").glob("*.html"))) == 2


def test_download_falls_back_to_playwright_on_http_failure(tmp_path, tmp_db, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _setup(tmp_db)

    def fake_http(url):
        return None, None, None, "http 403"

    pw_return = {
        "https://example.com/a": (
            "<html><body>" + _body("A") + "</body></html>",
            "https://example.com/a-final",
            None,
        ),
        "https://example.com/b": (
            "<html><body>" + _body("B") + "</body></html>",
            "https://example.com/b-final",
            None,
        ),
    }
    with patch("lib.steps.download._http_fetch", side_effect=fake_http):
        with patch("lib.steps.download.fetch_urls_html_batch",
                   return_value=pw_return) as pw:
            runner = CliRunner()
            result = runner.invoke(download_cli, ["--db", tmp_db, "--session", "d1"])
    assert result.exit_code == 0, result.output
    pw.assert_called_once()

    # Per policy: download must NOT auto-write sites.scrape_method based on
    # a successful Playwright fallback. The sites table is operator-curated;
    # the runtime adapts via the fallback chain regardless.
    with sqlite3.connect(tmp_db) as conn:
        s = Site.get_by_domain(conn, "example.com")
    if s is not None:
        assert s.scrape_method is None

    # Both txt and html written for playwright path too
    assert len(list(Path("download/txt").glob("*.txt"))) == 2
    assert len(list(Path("download/html").glob("*.html"))) == 2


def test_download_skips_http_for_pinned_playwright_domains(tmp_path, tmp_db, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _setup(tmp_db)
    with sqlite3.connect(tmp_db) as conn:
        Site(domain="example.com", scrape_method="playwright").upsert(conn)

    pw_return = {
        "https://example.com/a": (
            "<html>" + _body("A") + "</html>",
            "https://example.com/a", None,
        ),
        "https://example.com/b": (
            "<html>" + _body("B") + "</html>",
            "https://example.com/b", None,
        ),
    }
    with patch("lib.steps.download._http_fetch") as http_mock:
        with patch("lib.steps.download.fetch_urls_html_batch",
                   return_value=pw_return) as pw:
            runner = CliRunner()
            runner.invoke(download_cli, ["--db", tmp_db, "--session", "d1"])
    http_mock.assert_not_called()
    pw.assert_called_once()


def test_download_writes_report(tmp_path, tmp_db, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _setup(tmp_db)

    def fake_http(url):
        return _body("ok"), "<html>ok</html>", url, None

    with patch("lib.steps.download._http_fetch", side_effect=fake_http):
        runner = CliRunner()
        runner.invoke(download_cli, ["--db", tmp_db, "--session", "d1"])

    report = json.loads(Path("runs/d1/download.json").read_text())
    assert report["downloaded"] == 2
    assert report["http_succeeded"] == 1


def test_download_records_failures_when_both_phases_fail(tmp_path, tmp_db, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _setup(tmp_db)

    def fake_http(url):
        return None, None, None, "http 500"

    pw_return = {
        "https://example.com/a": (None, None, "Timeout"),
        "https://example.com/b": (None, None, "Timeout"),
    }
    with patch("lib.steps.download._http_fetch", side_effect=fake_http):
        with patch("lib.steps.download.fetch_urls_html_batch",
                   return_value=pw_return):
            runner = CliRunner()
            runner.invoke(download_cli, ["--db", tmp_db, "--session", "d1"])

    report = json.loads(Path("runs/d1/download.json").read_text())
    assert report["downloaded"] == 0
    assert len(report["failures"]) == 2


def test_download_routes_bright_data_enabled_domain_through_bd(
    tmp_path, tmp_db, monkeypatch
):
    """Domains marked bright_data_enabled=1 must skip http+playwright entirely
    and go straight through the Bright Data fetcher."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BRIGHTDATA_API_KEY", "test-key")
    init_db(tmp_db)
    state = NewsletterAgentState(session_id="bd1", db_path=tmp_db)
    state.complete_step("start")
    state.complete_step("gather")
    state.headline_data = [
        {"source": "S", "title": "T", "url": "https://www.wsj.com/article-x"},
    ]
    state.save_checkpoint("gather")
    with sqlite3.connect(tmp_db) as conn:
        conn.execute(
            "INSERT INTO urls (initial_url, final_url, title, source) "
            "VALUES (?, ?, ?, ?)",
            (state.headline_data[0]["url"], state.headline_data[0]["url"], "T", "S"),
        )
        conn.commit()

    bd_text = _body("WSJ")
    bd_html = "<html>" + bd_text + "</html>"
    bd_return = {
        "https://www.wsj.com/article-x":
            (bd_text, bd_html, "https://www.wsj.com/article-x", None),
    }
    with patch("lib.steps.download.scrape_urls_brightdata",
               return_value=bd_return) as bd:
        with patch("lib.steps.download._http_fetch") as http_mock:
            with patch("lib.steps.download.fetch_urls_html_batch") as pw:
                runner = CliRunner()
                result = runner.invoke(
                    download_cli, ["--db", tmp_db, "--session", "bd1"]
                )
    assert result.exit_code == 0, result.output
    bd.assert_called_once()
    # The BD-enabled URL was passed in as a Phase-0 candidate.
    called_with_urls = list(bd.call_args[0][0])
    assert called_with_urls == ["https://www.wsj.com/article-x"]
    http_mock.assert_not_called()
    pw.assert_not_called()

    # sites.scrape_method should NOT be set to 'bright_data' — that column
    # is for http/playwright. The bright_data_enabled flag governs BD.
    with sqlite3.connect(tmp_db) as conn:
        s = Site.get_by_domain(conn, "www.wsj.com")
    assert s is not None
    assert s.scrape_method is None
    assert s.bright_data_enabled is True


def test_download_falls_back_to_bright_data_on_playwright_failure(
    tmp_path, tmp_db, monkeypatch
):
    """When http AND playwright both fail on a normal domain, the URL falls
    through to a Bright Data attempt and can still be rescued."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BRIGHTDATA_API_KEY", "test-key")
    _setup(tmp_db)

    def fake_http(url):
        return None, None, None, "http 403"

    pw_return = {
        "https://example.com/a": (None, None, "Timeout"),
        "https://example.com/b": (None, None, "Timeout"),
    }
    bd_text = _body("rescued")
    bd_html = "<html>" + bd_text + "</html>"
    bd_return = {
        "https://example.com/a": (bd_text, bd_html, "https://example.com/a", None),
        "https://example.com/b": (bd_text, bd_html, "https://example.com/b", None),
    }

    with patch("lib.steps.download._http_fetch", side_effect=fake_http):
        with patch("lib.steps.download.fetch_urls_html_batch",
                   return_value=pw_return):
            with patch("lib.steps.download.scrape_urls_brightdata",
                       return_value=bd_return) as bd:
                runner = CliRunner()
                result = runner.invoke(
                    download_cli, ["--db", tmp_db, "--session", "d1"]
                )
    assert result.exit_code == 0, result.output
    bd.assert_called_once()
    fallback_urls = sorted(bd.call_args[0][0])
    assert fallback_urls == ["https://example.com/a", "https://example.com/b"]

    report = json.loads(Path("runs/d1/download.json").read_text())
    assert report["downloaded"] == 2  # both rescued by BD
    assert report["failures"] == []


def test_download_bright_data_failure_is_recorded_with_prior_error(
    tmp_path, tmp_db, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BRIGHTDATA_API_KEY", "test-key")
    _setup(tmp_db)

    def fake_http(url):
        return None, None, None, "http 500"

    pw_return = {
        "https://example.com/a": (None, None, "pw timeout"),
        "https://example.com/b": (None, None, "pw timeout"),
    }
    bd_return = {
        "https://example.com/a": (None, None, None, "bd failed"),
        "https://example.com/b": (None, None, None, "bd failed"),
    }

    with patch("lib.steps.download._http_fetch", side_effect=fake_http):
        with patch("lib.steps.download.fetch_urls_html_batch",
                   return_value=pw_return):
            with patch("lib.steps.download.scrape_urls_brightdata",
                       return_value=bd_return):
                runner = CliRunner()
                runner.invoke(download_cli, ["--db", tmp_db, "--session", "d1"])

    report = json.loads(Path("runs/d1/download.json").read_text())
    assert report["downloaded"] == 0
    assert len(report["failures"]) == 2
    for f in report["failures"]:
        assert f["phase"] == "bright_data_fallback"
        assert "pw timeout" in f["error"]
        assert "bd failed" in f["error"]


def test_download_respects_max_flag(tmp_path, tmp_db, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _setup(tmp_db)

    calls: list[str] = []

    def fake_http(url):
        calls.append(url)
        return _body("ok"), "<html>ok</html>", url, None

    with patch("lib.steps.download._http_fetch", side_effect=fake_http):
        runner = CliRunner()
        runner.invoke(download_cli, ["--db", tmp_db, "--session", "d1", "--max", "1"])
    assert len(calls) == 1


def test_download_uses_canonical_url_when_present(tmp_path, tmp_db, monkeypatch):
    """When the fetched HTML carries a same-domain <link rel='canonical'>,
    final_url and the cache key should follow the canonical, not the
    post-redirect URL."""
    monkeypatch.chdir(tmp_path)
    _setup(tmp_db)

    canonical_html = (
        '<html><head>'
        '<link rel="canonical" href="https://example.com/canonical-a">'
        '</head><body>' + ("body text " * 100) + '</body></html>'
    )

    def fake_http(url):
        if url.endswith("/a"):
            return _body("A"), canonical_html, "https://example.com/redirected-a", None
        return _body("B"), "<html>nope</html>", "https://example.com/b", None

    with patch("lib.steps.download._http_fetch", side_effect=fake_http):
        with patch("lib.steps.download.fetch_urls_html_batch"):
            runner = CliRunner()
            result = runner.invoke(download_cli, ["--db", tmp_db, "--session", "d1"])
            assert result.exit_code == 0, result.output

    with sqlite3.connect(tmp_db) as conn:
        row = conn.execute(
            "SELECT final_url FROM urls WHERE initial_url=?",
            ("https://example.com/a",),
        ).fetchone()
    assert row[0] == "https://example.com/canonical-a"


def test_download_ignores_cross_domain_canonical(tmp_path, tmp_db, monkeypatch):
    """A canonical pointing to a different domain must be rejected; the
    post-redirect URL wins."""
    monkeypatch.chdir(tmp_path)
    _setup(tmp_db)

    hostile_html = (
        '<html><head>'
        '<link rel="canonical" href="https://attacker.com/take-over">'
        '</head><body>' + ("body text " * 100) + '</body></html>'
    )

    def fake_http(url):
        return _body("X"), hostile_html, "https://example.com/redirected-a", None

    with patch("lib.steps.download._http_fetch", side_effect=fake_http):
        with patch("lib.steps.download.fetch_urls_html_batch"):
            runner = CliRunner()
            result = runner.invoke(download_cli, ["--db", tmp_db, "--session", "d1"])
            assert result.exit_code == 0, result.output

    with sqlite3.connect(tmp_db) as conn:
        rows = {r[0] for r in conn.execute("SELECT final_url FROM urls").fetchall()}
    assert "https://example.com/redirected-a" in rows
    assert not any("attacker.com" in u for u in rows)


def test_site_name_resolves_via_domain_regardless_of_source_label(tmp_path, tmp_db, monkeypatch):
    """An article gathered with source='Hacker News' whose final_url is on
    nytimes.com must be labeled with the NYT name from sites.name — no
    aggregator allowlist required."""
    monkeypatch.chdir(tmp_path)
    init_db(tmp_db)
    # Seed the publisher domain in sites.
    with sqlite3.connect(tmp_db) as conn:
        conn.execute(
            "INSERT INTO sites(domain, name) VALUES(?, ?)",
            ("nytimes.com", "The New York Times"),
        )
        conn.commit()

    state = NewsletterAgentState(session_id="d1", db_path=tmp_db)
    state.complete_step("start")
    state.complete_step("gather")
    state.headline_data = [
        {"source": "Hacker News", "title": "T", "url": "https://news.ycombinator.com/item?id=1"},
    ]
    state.save_checkpoint("gather")
    with sqlite3.connect(tmp_db) as conn:
        conn.execute(
            "INSERT INTO urls(initial_url, final_url, title, source) VALUES(?, ?, ?, ?)",
            ("https://news.ycombinator.com/item?id=1",
             "https://news.ycombinator.com/item?id=1",
             "T", "Hacker News"),
        )
        conn.commit()

    def fake_http(url):
        # HN linkout resolves to NYT after redirect.
        return _body("nyt"), "<html><body>" + ("text " * 200) + "</body></html>", \
            "https://www.nytimes.com/2026/foo", None

    with patch("lib.steps.download._http_fetch", side_effect=fake_http):
        with patch("lib.steps.download.fetch_urls_html_batch"):
            runner = CliRunner()
            result = runner.invoke(download_cli, ["--db", tmp_db, "--session", "d1"])
            assert result.exit_code == 0, result.output

    reloaded = NewsletterAgentState(session_id="d1", db_path=tmp_db).load_latest_from_db()
    assert reloaded is not None
    h = reloaded.headline_data[0]
    assert h.get("site_name") == "The New York Times", h


def test_unknown_domain_triggers_sitename_llm_for_any_source(tmp_path, tmp_db, monkeypatch):
    """The LLM-sitename resolution path used to fire only for aggregator
    sources. It must now fire for any source whose final_url domain isn't
    in sites."""
    from lib.prompts.sitename import SitenameOutput, SitenameRecord

    monkeypatch.chdir(tmp_path)
    init_db(tmp_db)

    state = NewsletterAgentState(session_id="d1", db_path=tmp_db)
    state.complete_step("start")
    state.complete_step("gather")
    state.headline_data = [
        # Direct (non-aggregator) source, unknown publisher domain.
        {"source": "Some Direct Feed", "title": "T", "url": "https://brand-new-site.example/post"},
    ]
    state.save_checkpoint("gather")
    with sqlite3.connect(tmp_db) as conn:
        conn.execute(
            "INSERT INTO urls(initial_url, final_url, title, source) VALUES(?, ?, ?, ?)",
            ("https://brand-new-site.example/post",
             "https://brand-new-site.example/post",
             "T", "Some Direct Feed"),
        )
        conn.commit()

    def fake_http(url):
        return _body("x"), "<html><body>" + ("text " * 200) + "</body></html>", \
            "https://brand-new-site.example/post", None

    fake_result = SitenameOutput(results=[
        SitenameRecord(id="0", domain="brand-new-site.example", site_name="Brand New Site"),
    ])

    with patch("lib.steps.download._http_fetch", side_effect=fake_http), \
         patch("lib.steps.download.fetch_urls_html_batch"), \
         patch("lib.steps.download.call_prompt", return_value=fake_result) as call:
        runner = CliRunner()
        result = runner.invoke(download_cli, ["--db", tmp_db, "--session", "d1"])
        assert result.exit_code == 0, result.output
        # The LLM was actually consulted.
        assert call.called
        domains_sent = {item["domain"] for item in call.call_args.args[1]["items"]}
        assert "brand-new-site.example" in domains_sent

    with sqlite3.connect(tmp_db) as conn:
        row = conn.execute(
            "SELECT name FROM sites WHERE domain=?", ("brand-new-site.example",),
        ).fetchone()
    assert row is not None and row[0] == "Brand New Site"
