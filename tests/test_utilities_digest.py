"""Tests for the rated-digest HTML rendering in lib.utilities.

Focus: the 'no content' sentinel (emitted by the summarize prompt when an
article body could not be extracted) must NOT shadow the real scraped title in
the digest headline, and must not render as a '- no content' summary line.
"""
from lib.utilities import _render_rated_item


def test_no_content_short_summary_falls_back_to_title():
    item = {
        "rating": -2.13,
        "short_summary": "no content",
        "title": "The Future of Climate AI Is Multimodal",
        "summary": "- no content",
        "final_url": "https://hackernoon.com/the-future-of-climate-ai-is-multimodal",
        "site_name": "Hacker Noon",
    }
    html = _render_rated_item(item)
    assert "The Future of Climate AI Is Multimodal" in html
    assert "no content" not in html


def test_no_content_summary_not_rendered_as_bullet():
    item = {
        "rating": -2.8,
        "short_summary": "no content",
        "title": "Some Real Title",
        "summary": "- no content",
        "final_url": "https://example.com/x",
        "site_name": "Example",
    }
    html = _render_rated_item(item)
    # The '- no content' sentinel summary collapses to empty, not a visible line.
    assert "no content" not in html


def test_real_short_summary_is_used_over_title():
    item = {
        "rating": 5.0,
        "short_summary": "Nvidia unveils RTX Spark superchip at Computex",
        "title": "raw scraped title that should be ignored",
        "summary": "- A real bullet point.",
        "final_url": "https://example.com/y",
        "site_name": "Example",
    }
    html = _render_rated_item(item)
    assert "Nvidia unveils RTX Spark superchip at Computex" in html
    assert "raw scraped title that should be ignored" not in html
    assert "A real bullet point." in html


def test_multi_bullet_summary_preserved():
    item = {
        "rating": 4.0,
        "short_summary": "A headline",
        "title": "t",
        "summary": "- First bullet\n- Second bullet",
        "final_url": "https://example.com/z",
        "site_name": "Example",
    }
    html = _render_rated_item(item)
    assert "First bullet" in html
    assert "Second bullet" in html


def test_row_includes_copy_and_bluesky_links():
    """Each digest row carries the same copy (⎘) + queue-to-Bluesky (🦋) links
    as the full newsletter, with the article URL url-encoded into /enqueue."""
    item = {
        "rating": 7.0,
        "short_summary": "Nvidia unveils RTX Spark superchip",
        "title": "t",
        "summary": "- A bullet.",
        "final_url": "https://example.com/story?a=1",
        "site_name": "Example",
    }
    html = _render_rated_item(item)
    assert "&#x2398;" in html                       # copy-headline glyph
    assert "\U0001f98b" in html                     # butterfly glyph
    assert "/enqueue?u=https%3A%2F%2Fexample.com%2Fstory%3Fa%3D1" in html


def test_row_omits_bluesky_link_without_url():
    item = {
        "rating": 1.0,
        "short_summary": "No link story",
        "title": "t",
        "summary": "- A bullet.",
        "final_url": "",
        "site_name": "Example",
    }
    html = _render_rated_item(item)
    assert "\U0001f98b" not in html
    assert "/enqueue" not in html
