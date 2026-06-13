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


def _make_titles_output(n_groups: int):
    """Returns a BskySectionTitlesOutput with n_groups titles."""
    from lib.prompts.bsky_section_titles import BskySectionTitlesOutput
    return BskySectionTitlesOutput(titles=[f"Section Title {i+1}" for i in range(n_groups)])


def _smart_call_prompt(name, inputs, **kwargs):
    """Adaptive mock: reorder → one group of all indexes; titles → one per group."""
    if name == "bsky_reorder":
        n = len(inputs.posts)
        return _reorder_groups([list(range(n))])
    if name == "bsky_section_titles":
        return _make_titles_output(len(inputs.groups))
    raise ValueError(f"Unexpected prompt: {name}")


def test_bluesky_step_renders_html_with_title_and_posts(tmp_path, monkeypatch):
    """Step renders an HTML file with digest title and post content."""
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
            _reorder_groups([[0, 1, 2]]),  # one topical group of all 3 posts
            _make_titles_output(1),
        ]

        from lib.steps.bluesky import cli
        runner = CliRunner()
        result = runner.invoke(cli, ["--user", "testuser.bsky.social"])

    assert result.exit_code == 0, result.output
    # Check HTML was written
    out_dir = tmp_path / "out"
    html_files = list(out_dir.glob("bsky-*.html"))
    assert len(html_files) == 1
    html_content = html_files[0].read_text()
    assert "Bluesky Digest" in html_content
    assert "GPT-5" in html_content or "GPT" in html_content


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


def test_bluesky_step_calls_llm_reorder_and_titles(tmp_path, monkeypatch):
    """Step invokes call_prompt for both bsky_reorder and bsky_section_titles."""
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
    assert "bsky_section_titles" in call_prompt_calls


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


def test_bluesky_topical_grouping_yields_multiple_sections(tmp_path, monkeypatch):
    """Reorder returns N topical groups → N <h2> sections, one punny title each."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BSKY_USERNAME", "u.bsky.social")
    monkeypatch.setenv("BSKY_SECRET", "s")

    def two_groups(name, inputs, **kwargs):
        if name == "bsky_reorder":
            return _reorder_groups([[0, 2], [1]])  # two distinct topic groups
        if name == "bsky_section_titles":
            return _make_titles_output(len(inputs.groups))
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
    assert html.count("<h2>") == 2
    # all three posts appear, grouped by the LLM's topical assignment
    assert "GPT-5" in html
    assert "diffusion models" in html
    assert "H200" in html


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
    assert html.count("<h2>") == 2
    # neutral topic labels are the headers at this stage (titles not yet applied)
    assert "OpenAI news" in html and "Hardware and research" in html
    assert "GPT-5" in html and "diffusion models" in html and "H200" in html

    ordered = json.loads((wd / "ordered.json").read_text())
    assert [g["label"] for g in ordered["groups"]] == ["OpenAI news", "Hardware and research"]

    # titles-request prepped for the next dispatch
    treq = json.loads((wd / "titles-request.json").read_text())
    assert treq["prompt"] == "bsky_section_titles"
    assert "irreverent" in treq["system_prompt"].lower() or "witty" in treq["system_prompt"].lower()

    # dedup marker advanced to the newest fetched post
    marker = (tmp_path / "download" / "bsky-state" / "u.bsky.social.txt").read_text().strip()
    assert marker == "at://did:plc:test/app.bsky.feed.post/1"


def test_staged_apply_titles_saves_separate_artifact(tmp_path, monkeypatch):
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

    # Simulate the dispatched titles Agent's output.
    (wd / "titles-result.json").write_text(json.dumps({
        "titles": ["Sue-perintelligence", "Chips Ahoy"]
    }))

    result = _run_staged(["--user", "u.bsky.social", "--apply-titles"])
    assert result.exit_code == 0, result.output

    titles = json.loads((wd / "titles.json").read_text())
    assert titles["titles"] == ["Sue-perintelligence", "Chips Ahoy"]
    # legacy parity: the HTML is NOT rewritten with the puns
    html = (tmp_path / "out" / "latest-bsky.html").read_text()
    assert "Sue-perintelligence" not in html
    assert "Chips Ahoy" not in html
