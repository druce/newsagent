import pytest
from pathlib import Path


@pytest.fixture(autouse=True)
def _isolate_brightdata_env(monkeypatch):
    """Tests should never accidentally hit the live Bright Data proxy because
    BRIGHTDATA_API_KEY happens to be set in the dev shell. Individual tests
    that exercise BD wiring should set it themselves (and mock the call)."""
    monkeypatch.delenv("BRIGHTDATA_API_KEY", raising=False)
    monkeypatch.delenv("BRIGHTDATA_ZONE", raising=False)


@pytest.fixture
def tmp_db(tmp_path: Path) -> str:
    """Return a path to a fresh SQLite DB file in a temp dir."""
    return str(tmp_path / "test_newsletter.db")


@pytest.fixture
def sample_sources_yaml(tmp_path: Path) -> str:
    """Write a minimal sources.yaml and return its path."""
    content = """
sources:
  example_rss:
    type: rss
    url: https://example.com/feed.xml
    enabled: true
  example_html:
    type: html
    url: https://example.com/news
    enabled: false
"""
    p = tmp_path / "sources.yaml"
    p.write_text(content)
    return str(p)
