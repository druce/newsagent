"""Tests for lib.fetch.canonical.extract_canonical_url."""
from __future__ import annotations

from lib.fetch.canonical import extract_canonical_url


def _html(canonical: str | None) -> str:
    link = f'<link rel="canonical" href="{canonical}">' if canonical else ""
    return f"<html><head>{link}</head><body>x</body></html>"


def test_absolute_same_domain_returned():
    html = _html("https://example.com/foo")
    assert extract_canonical_url(html, "https://example.com/foo?utm=x") == \
        "https://example.com/foo"


def test_relative_resolved_against_page_url():
    html = _html("/foo/bar")
    assert extract_canonical_url(html, "https://example.com/baz") == \
        "https://example.com/foo/bar"


def test_www_prefix_treated_as_same_domain():
    html = _html("https://example.com/foo")
    assert extract_canonical_url(html, "https://www.example.com/foo") == \
        "https://example.com/foo"


def test_subdomain_is_not_same_domain():
    # news.example.com canonicalizing to example.com — reject, too risky.
    html = _html("https://example.com/foo")
    assert extract_canonical_url(html, "https://news.example.com/foo") is None


def test_cross_domain_canonical_rejected():
    html = _html("https://attacker.com/x")
    assert extract_canonical_url(html, "https://example.com/foo") is None


def test_no_canonical_returns_none():
    assert extract_canonical_url(_html(None), "https://example.com/foo") is None


def test_malformed_href_returns_none():
    html = _html("javascript:void(0)")
    assert extract_canonical_url(html, "https://example.com/foo") is None


def test_empty_href_returns_none():
    html = '<html><head><link rel="canonical" href=""></head></html>'
    assert extract_canonical_url(html, "https://example.com/foo") is None


def test_first_valid_canonical_when_multiple():
    html = (
        '<html><head>'
        '<link rel="canonical" href="https://example.com/first">'
        '<link rel="canonical" href="https://example.com/second">'
        '</head></html>'
    )
    assert extract_canonical_url(html, "https://example.com/foo") == \
        "https://example.com/first"


def test_garbage_html_returns_none():
    assert extract_canonical_url("not html", "https://example.com/foo") is None
