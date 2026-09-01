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


def test_row_action_links_use_loopback_ip_not_localhost():
    """`localhost` resolves to BOTH 127.0.0.1 and ::1, but the daemon binds IPv4
    only — a client preferring ::1 gets connection-refused."""
    from lib.utilities import render_row_action_links
    html = render_row_action_links("H", "https://e.com/x", queue_port=8765)
    assert "http://127.0.0.1:8765/enqueue" in html
    assert "localhost" not in html


def test_share_anchor_keeps_real_href_and_delegates_to_script():
    """The anchor keeps a working href + target for the no-JS / email path, and
    delegates behaviour to the shared script instead of the old inline fetch
    that flipped the glyph to ✓ unconditionally."""
    from lib.utilities import render_row_action_links
    html = render_row_action_links("H", "https://e.com/x", queue_port=8765)
    assert 'href="http://127.0.0.1:8765/enqueue' in html
    assert 'target="_blank"' in html
    assert 'onclick="return __bskyQ(this);"' in html
    assert "textContent" not in html   # no unconditional ✓
    assert "no-cors" not in html       # response must be readable now


def test_share_script_is_self_contained_and_idempotent():
    from lib.utilities import render_bsky_share_script
    js = render_bsky_share_script(8765)
    assert "<script>" in js and "</script>" in js
    assert "if (window.__bskyQ)" in js          # tolerates a double include
    assert 'var PORT = "8765"' in js
    assert "bsky-daemon-banner" in js
    assert ".venv/bin/python -m lib.steps.bsky_share" in js


def test_share_script_probe_is_the_writeless_enqueue_form():
    """Guards the backward-compat decision: probing a brand-new /health would
    404 against an already-running pre-upgrade daemon and show a false alarm."""
    from lib.utilities import render_bsky_share_script
    js = render_bsky_share_script(8765)
    assert '"http://127.0.0.1:" + PORT + "/enqueue"' in js
    assert "/health" not in js


def test_share_script_port_defaults_to_env(monkeypatch):
    monkeypatch.setenv("BSKY_QUEUE_PORT", "9999")
    from lib.utilities import render_bsky_share_script
    assert 'var PORT = "9999"' in render_bsky_share_script()


def test_digest_shell_embeds_the_share_client_script(monkeypatch):
    """Anchor port and script port must agree, or every click looks broken."""
    monkeypatch.setenv("BSKY_QUEUE_PORT", "9999")
    from lib.utilities import render_rated_digest
    item = {
        "rating": 7.0, "short_summary": "A story", "title": "t",
        "summary": "- A bullet.", "final_url": "https://example.com/s",
        "site_name": "Example",
    }
    html = render_rated_digest("s1", [item], "2026-08-10 09:00:00")
    assert "__bskyQ" in html
    assert "bsky-daemon-banner" in html
    assert "http://127.0.0.1:9999/enqueue" in html   # anchor port ...
    assert 'var PORT = "9999"' in html               # ... matches script port
    assert html.index("__bskyQ") < html.index("</body>")


def test_digest_shell_omits_script_when_no_rows_have_urls():
    """A page with nothing to queue must not pin a daemon-down banner."""
    from lib.utilities import render_rated_digest
    item = {
        "rating": 1.0, "short_summary": "No link", "title": "t",
        "summary": "- A bullet.", "final_url": "", "site_name": "Example",
    }
    html = render_rated_digest("s1", [item], "2026-08-10 09:00:00")
    assert "__bskyQ" not in html


def test_strip_source_attribution_lives_in_utilities():
    # Shared home so both the Bluesky digest and summarize can use it.
    from lib.utilities import strip_source_attribution as s

    # The exact shape the user flagged on 2026-07-14:
    assert s(
        "Trump administration and industry groups discuss framework to "
        "streamline releases of US open models matching Chinese open-model "
        "capability, Washington Post reports."
    ) == (
        "Trump administration and industry groups discuss framework to "
        "streamline releases of US open models matching Chinese open-model "
        "capability"
    )
    # Primary-source attribution stays.
    assert s("AI could displace 15 million jobs, Goldman Sachs says") == (
        "AI could displace 15 million jobs, Goldman Sachs says"
    )


def test_strip_source_attribution_bluesky_alias_is_same_function():
    from lib.steps.bluesky import _strip_source_attribution
    from lib.utilities import strip_source_attribution

    assert _strip_source_attribution is strip_source_attribution
