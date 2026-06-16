"""Tests for lib/steps/bluesky.py — newsagent:bluesky digest pipeline."""
import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from click.testing import CliRunner


# Fake feed items for mocking bsky_get_author_feed
_FAKE_FEED = [
    {
        "post": {
            "uri": "at://did:plc:test/app.bsky.feed.post/1",
            "author": {"handle": "ai_researcher.bsky.social"},
            "record": {
                "text": "GPT-5 just dropped and it's impressive: https://openai.com/gpt5",
                "createdAt": "2026-05-18T10:00:00Z",
            },
            "embed": {
                "$type": "app.bsky.embed.external#view",
                "external": {
                    "uri": "https://openai.com/gpt5",
                    "title": "GPT-5 is here",
                    "description": "OpenAI releases GPT-5.",
                },
            },
        }
    },
    {
        "post": {
            "uri": "at://did:plc:test/app.bsky.feed.post/2",
            "author": {"handle": "ml_engineer.bsky.social"},
            "record": {
                "text": "Interesting paper on diffusion models for text generation.",
                "createdAt": "2026-05-18T11:00:00Z",
            },
        }
    },
    {
        "post": {
            "uri": "at://did:plc:test/app.bsky.feed.post/3",
            "author": {"handle": "tech_writer.bsky.social"},
            "record": {
                "text": "Nvidia announces new H200 GPU cluster.",
                "createdAt": "2026-05-18T12:00:00Z",
            },
        }
    },
]


def _reorder_groups(groups: list[list[int]]):
    """Build a BskyReorderOutput from a list of index-groups (ordered)."""
    from lib.prompts.bsky_reorder import BskyReorderOutput, BskyReorderGroup
    return BskyReorderOutput(
        groups=[BskyReorderGroup(label=f"Topic {i+1}", indexes=ix) for i, ix in enumerate(groups)]
    )


def _make_headlines_output(n: int):
    """Returns a BskyHeadlinesOutput with n punny rewrites."""
    from lib.prompts.bsky_headlines import BskyHeadlinesOutput
    return BskyHeadlinesOutput(headlines=[f"Punny Rewrite {i+1}" for i in range(n)])


def _smart_call_prompt(name, inputs, **kwargs):
    """Adaptive mock: reorder → one group of all indexes; headlines → one per headline."""
    if name == "bsky_reorder":
        n = len(inputs.posts)
        return _reorder_groups([list(range(n))])
    if name == "bsky_headlines":
        return _make_headlines_output(len(inputs.headlines))
    raise ValueError(f"Unexpected prompt: {name}")


def test_bluesky_step_renders_flat_html_with_posts(tmp_path, monkeypatch):
    """Step renders a flat HTML digest (legacy skynet.html format): no section
    headers, post text as the link, source in <em>, <hr /> separators, footer."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BSKY_USERNAME", "testuser.bsky.social")
    monkeypatch.setenv("BSKY_SECRET", "test-secret")

    fake_session = {"accessJwt": "fake-token", "did": "did:plc:test", "handle": "testuser.bsky.social"}

    with (
        patch("lib.steps.bluesky.bsky_login", return_value=fake_session),
        patch("lib.steps.bluesky.bsky_get_author_feed", return_value=_FAKE_FEED),
        patch("lib.steps.bluesky.get_og_tags", return_value={}),
        patch("lib.steps.bluesky.download_image", return_value=None),
        patch("lib.steps.bluesky.call_prompt") as mock_call_prompt,
    ):
        mock_call_prompt.side_effect = [
            _reorder_groups([[0, 1, 2]]),  # one ordered group of all 3 posts
            _make_headlines_output(3),
        ]

        from lib.steps.bluesky import cli
        runner = CliRunner()
        result = runner.invoke(cli, ["--user", "testuser.bsky.social"])

    assert result.exit_code == 0, result.output
    out_dir = tmp_path / "out"
    html_files = list(out_dir.glob("bsky-*.html"))
    assert len(html_files) == 1
    html = html_files[0].read_text()

    # Flat format: NO section headers, post text present.
    assert "<h2>" not in html
    assert "GPT-5" in html
    # Post 0 has a link → rendered as <a>post text</a> + source <em>, with the
    # domain as the source fallback (no og:site_name in the mock).
    assert "<a href='https://openai.com/gpt5'>" in html
    assert "<em>openai.com</em>" in html
    # Link-less posts render as bare paragraphs (no source).
    assert "diffusion models" in html
    # Separators + footer present.
    assert "<hr />" in html
    assert "on Bluesky</a></p>" in html
    # Punny rewrites are NOT injected into the digest HTML.
    assert "Punny Rewrite" not in html


def test_site_name_uses_sites_table_then_bare_domain(tmp_path):
    """_site_name mirrors the legacy notebook: og:site_name wins, else the
    curated sites-table name for the URL's domain, else the bare domain."""
    import sqlite3
    from lib.steps.bluesky import _site_name
    from lib.sources import reset_db_cache

    db = tmp_path / "sites.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE sites (domain TEXT, name TEXT)")
    conn.execute("INSERT INTO sites VALUES ('lemonde.fr', 'Le Monde')")
    conn.commit()
    conn.close()
    reset_db_cache()
    try:
        # og:site_name absent → curated sites-table name (subdomain stripped)
        assert _site_name("https://www.lemonde.fr/a", {}, db_path=str(db)) == "Le Monde"
        # og:site_name present → it wins over the table
        assert (
            _site_name("https://www.lemonde.fr/a", {"site_name": "Le Monde Tech"}, db_path=str(db))
            == "Le Monde Tech"
        )
        # unknown domain → bare-domain fallback (sans www.)
        assert _site_name("https://www.example.org/x", {}, db_path=str(db)) == "example.org"
    finally:
        reset_db_cache()


def test_bluesky_step_requires_env_vars(tmp_path, monkeypatch):
    """Step exits with error when BSKY_USERNAME or BSKY_SECRET is missing."""
    monkeypatch.chdir(tmp_path)
    # Ensure env vars are NOT set
    monkeypatch.delenv("BSKY_USERNAME", raising=False)
    monkeypatch.delenv("BSKY_SECRET", raising=False)

    from lib.steps.bluesky import cli
    runner = CliRunner()
    result = runner.invoke(cli, ["--user", "testuser.bsky.social"])

    assert result.exit_code != 0


def test_bluesky_step_calls_llm_reorder_and_headlines(tmp_path, monkeypatch):
    """Step invokes call_prompt for both bsky_reorder and bsky_headlines."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BSKY_USERNAME", "testuser.bsky.social")
    monkeypatch.setenv("BSKY_SECRET", "test-secret")

    fake_session = {"accessJwt": "fake-token", "did": "did:plc:test", "handle": "testuser.bsky.social"}

    call_prompt_calls = []

    def recording_call_prompt(name, inputs, **kwargs):
        call_prompt_calls.append(name)
        return _smart_call_prompt(name, inputs, **kwargs)

    with (
        patch("lib.steps.bluesky.bsky_login", return_value=fake_session),
        patch("lib.steps.bluesky.bsky_get_author_feed", return_value=_FAKE_FEED),
        patch("lib.steps.bluesky.get_og_tags", return_value={}),
        patch("lib.steps.bluesky.download_image", return_value=None),
        patch("lib.steps.bluesky.call_prompt", side_effect=recording_call_prompt),
    ):
        from lib.steps.bluesky import cli
        runner = CliRunner()
        result = runner.invoke(cli, ["--user", "testuser.bsky.social"])

    assert result.exit_code == 0, result.output
    assert "bsky_reorder" in call_prompt_calls
    assert "bsky_headlines" in call_prompt_calls


def _run_cli(args, feed=_FAKE_FEED, og=None):
    """Helper: invoke the bluesky CLI with all network/LLM boundaries mocked."""
    fake_session = {"accessJwt": "fake-token", "did": "did:plc:test", "handle": "x"}
    with (
        patch("lib.steps.bluesky.bsky_login", return_value=fake_session),
        patch("lib.steps.bluesky.bsky_get_author_feed", return_value=feed),
        patch("lib.steps.bluesky.get_og_tags", return_value=og or {}),
        patch("lib.steps.bluesky.download_image", return_value=None),
        patch("lib.steps.bluesky.call_prompt", side_effect=_smart_call_prompt),
    ):
        from lib.steps.bluesky import cli
        return CliRunner().invoke(cli, args)


def test_bluesky_groups_flatten_to_ordered_posts_no_sections(tmp_path, monkeypatch):
    """Reorder groups are flattened into a single ordered list — NO <h2> headers.
    Posts appear in group-flattened order ([0,2] then [1])."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BSKY_USERNAME", "u.bsky.social")
    monkeypatch.setenv("BSKY_SECRET", "s")

    def two_groups(name, inputs, **kwargs):
        if name == "bsky_reorder":
            return _reorder_groups([[0, 2], [1]])  # adjacency for ordering only
        if name == "bsky_headlines":
            return _make_headlines_output(len(inputs.headlines))
        raise ValueError(name)

    fake_session = {"accessJwt": "t", "did": "did:plc:test", "handle": "u"}
    with (
        patch("lib.steps.bluesky.bsky_login", return_value=fake_session),
        patch("lib.steps.bluesky.bsky_get_author_feed", return_value=_FAKE_FEED),
        patch("lib.steps.bluesky.get_og_tags", return_value={}),
        patch("lib.steps.bluesky.download_image", return_value=None),
        patch("lib.steps.bluesky.call_prompt", side_effect=two_groups),
    ):
        from lib.steps.bluesky import cli
        result = CliRunner().invoke(cli, ["--user", "u.bsky.social"])

    assert result.exit_code == 0, result.output
    html = (tmp_path / "out" / "latest-bsky.html").read_text()
    # No section headers and no group labels in the body.
    assert "<h2>" not in html
    assert "Topic 1" not in html and "Topic 2" not in html
    # all three posts appear
    assert "GPT-5" in html and "diffusion models" in html and "H200" in html
    # flattened order is [0, 2, 1]: GPT-5 (0) → H200 (2) → diffusion (1)
    assert html.index("GPT-5") < html.index("H200") < html.index("diffusion models")


def test_bluesky_cross_run_dedup_skips_seen_posts(tmp_path, monkeypatch):
    """A stored marker truncates the feed at the previously-seen post."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BSKY_USERNAME", "u.bsky.social")
    monkeypatch.setenv("BSKY_SECRET", "s")

    # Pre-seed the dedup marker with post #2's URI (second item, newest-first feed).
    state = tmp_path / "download" / "bsky-state"
    state.mkdir(parents=True)
    (state / "u.bsky.social.txt").write_text("at://did:plc:test/app.bsky.feed.post/2")

    result = _run_cli(["--user", "u.bsky.social"])
    assert result.exit_code == 0, result.output
    html = (tmp_path / "out" / "latest-bsky.html").read_text()
    # Only the first (unseen) post should be present.
    assert "GPT-5" in html
    assert "diffusion models" not in html
    assert "H200" not in html


def test_bluesky_no_new_posts_exits_without_writing(tmp_path, monkeypatch):
    """When the marker matches the newest post, nothing new → no HTML written."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BSKY_USERNAME", "u.bsky.social")
    monkeypatch.setenv("BSKY_SECRET", "s")

    state = tmp_path / "download" / "bsky-state"
    state.mkdir(parents=True)
    (state / "u.bsky.social.txt").write_text("at://did:plc:test/app.bsky.feed.post/1")

    result = _run_cli(["--user", "u.bsky.social"])
    assert result.exit_code == 0, result.output
    assert not list((tmp_path / "out").glob("bsky-*.html")) if (tmp_path / "out").exists() else True
    assert "no new posts" in result.output.lower()


def test_bluesky_no_dedup_flag_processes_everything(tmp_path, monkeypatch):
    """--no-dedup ignores the marker and renders the full feed."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BSKY_USERNAME", "u.bsky.social")
    monkeypatch.setenv("BSKY_SECRET", "s")

    state = tmp_path / "download" / "bsky-state"
    state.mkdir(parents=True)
    (state / "u.bsky.social.txt").write_text("at://did:plc:test/app.bsky.feed.post/1")

    result = _run_cli(["--user", "u.bsky.social", "--no-dedup"])
    assert result.exit_code == 0, result.output
    html = (tmp_path / "out" / "latest-bsky.html").read_text()
    assert "GPT-5" in html and "diffusion models" in html and "H200" in html


def test_clean_text_strips_trailing_url_abbreviation():
    from lib.steps.bluesky import _clean_text

    assert _clean_text("Elon says we run out of power newatlas.com/technology/e...") == (
        "Elon says we run out of power"
    )
    # leaves normal text untouched
    assert _clean_text("Just a normal post.") == "Just a normal post."


# ── Staged mode (in-session Agent dispatch — no claude -p, no API engine) ──────

def _run_staged(args, feed=_FAKE_FEED, og=None):
    """Invoke the CLI in a staged mode, asserting call_prompt is NEVER used."""
    fake_session = {"accessJwt": "t", "did": "did:plc:test", "handle": "u"}
    forbidden = MagicMock(side_effect=AssertionError("call_prompt must not run in staged mode"))
    with (
        patch("lib.steps.bluesky.bsky_login", return_value=fake_session),
        patch("lib.steps.bluesky.bsky_get_author_feed", return_value=feed),
        patch("lib.steps.bluesky.get_og_tags", return_value=og or {}),
        patch("lib.steps.bluesky.download_image", return_value=None),
        patch("lib.steps.bluesky.call_prompt", forbidden),
    ):
        from lib.steps.bluesky import cli
        return CliRunner().invoke(cli, args)


def test_staged_fetch_writes_fetch_json_and_reorder_request(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BSKY_USERNAME", "u.bsky.social")
    monkeypatch.setenv("BSKY_SECRET", "s")

    result = _run_staged(["--user", "u.bsky.social", "--fetch"])
    assert result.exit_code == 0, result.output

    wd = tmp_path / "runs" / "bsky-u.bsky.social"
    fetch = json.loads((wd / "fetch.json").read_text())
    assert len(fetch["feed_items"]) == 3
    assert fetch["handle"] == "u.bsky.social"
    assert fetch["newest_uri"] == "at://did:plc:test/app.bsky.feed.post/1"

    req = json.loads((wd / "reorder-request.json").read_text())
    assert req["prompt"] == "bsky_reorder"
    assert "system_prompt" in req and "user_prompt" in req and "output_schema" in req
    # The rendered prompt embeds the actual post text (single source of truth).
    assert "GPT-5" in req["user_prompt"]


def test_staged_apply_reorder_renders_ordered_html(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BSKY_USERNAME", "u.bsky.social")
    monkeypatch.setenv("BSKY_SECRET", "s")

    # Stage 1
    assert _run_staged(["--user", "u.bsky.social", "--fetch"]).exit_code == 0
    wd = tmp_path / "runs" / "bsky-u.bsky.social"

    # Simulate the dispatched reorder Agent's output (two topical groups).
    (wd / "reorder-result.json").write_text(json.dumps({
        "groups": [
            {"label": "OpenAI news", "indexes": [0]},
            {"label": "Hardware and research", "indexes": [2, 1]},
        ]
    }))

    # Stage 3
    result = _run_staged(["--user", "u.bsky.social", "--apply-reorder"])
    assert result.exit_code == 0, result.output

    html = (tmp_path / "out" / "latest-bsky.html").read_text()
    # Flat format: no section headers, no group labels in the body.
    assert "<h2>" not in html
    assert "OpenAI news" not in html and "Hardware and research" not in html
    assert "GPT-5" in html and "diffusion models" in html and "H200" in html
    # flattened order follows the groups: [0] then [2, 1]
    assert html.index("GPT-5") < html.index("H200") < html.index("diffusion models")
    assert "on Bluesky</a></p>" in html  # footer

    ordered = json.loads((wd / "ordered.json").read_text())
    assert [g["label"] for g in ordered["groups"]] == ["OpenAI news", "Hardware and research"]

    # headlines-request prepped for the next dispatch (punny rewrites)
    hreq = json.loads((wd / "headlines-request.json").read_text())
    assert hreq["prompt"] == "bsky_headlines"
    assert "irreverent" in hreq["system_prompt"].lower() or "witty" in hreq["system_prompt"].lower()
    # the request carries the ordered headlines (flattened post text)
    assert hreq["headlines"][0].startswith("GPT-5")

    # dedup marker advanced to the newest fetched post
    marker = (tmp_path / "download" / "bsky-state" / "u.bsky.social.txt").read_text().strip()
    assert marker == "at://did:plc:test/app.bsky.feed.post/1"


def test_staged_apply_headlines_saves_separate_artifact(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BSKY_USERNAME", "u.bsky.social")
    monkeypatch.setenv("BSKY_SECRET", "s")

    assert _run_staged(["--user", "u.bsky.social", "--fetch"]).exit_code == 0
    wd = tmp_path / "runs" / "bsky-u.bsky.social"
    (wd / "reorder-result.json").write_text(json.dumps({
        "groups": [
            {"label": "OpenAI news", "indexes": [0]},
            {"label": "Hardware and research", "indexes": [2, 1]},
        ]
    }))
    assert _run_staged(["--user", "u.bsky.social", "--apply-reorder"]).exit_code == 0

    # Simulate the dispatched headlines Agent's output (one rewrite per post).
    (wd / "headlines-result.json").write_text(json.dumps({
        "headlines": ["GPT-fived", "H-two-hundred proof", "Diffuse the situation"]
    }))

    result = _run_staged(["--user", "u.bsky.social", "--apply-headlines"])
    assert result.exit_code == 0, result.output

    hl = json.loads((wd / "headlines.json").read_text())
    assert hl["headlines"] == ["GPT-fived", "H-two-hundred proof", "Diffuse the situation"]
    # pairs map each rewrite back to its original (flattened) headline
    assert hl["pairs"][0]["rewrite"] == "GPT-fived"
    assert hl["pairs"][0]["headline"].startswith("GPT-5")
    # plain-text copy/paste artifact: just the rewrites, one per line
    assert (wd / "headlines.txt").read_text() == "GPT-fived\nH-two-hundred proof\nDiffuse the situation\n"
    # the digest HTML is NOT rewritten with the puns
    html = (tmp_path / "out" / "latest-bsky.html").read_text()
    assert "GPT-fived" not in html
    assert "Diffuse the situation" not in html
