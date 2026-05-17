import pytest
from pathlib import Path


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
