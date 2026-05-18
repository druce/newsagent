"""Tests for lib/steps/bluesky.py — news:bluesky digest pipeline."""
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


def _make_reorder_output(n: int):
    """Returns a BskyReorderOutput with indexes 0..n-1 in order."""
    from lib.prompts.bsky_reorder import BskyReorderOutput
    return BskyReorderOutput(indexes=list(range(n)))


def _make_titles_output(n_groups: int):
    """Returns a BskySectionTitlesOutput with n_groups titles."""
    from lib.prompts.bsky_section_titles import BskySectionTitlesOutput
    return BskySectionTitlesOutput(titles=[f"Section Title {i+1}" for i in range(n_groups)])


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
            _make_reorder_output(3),
            _make_titles_output(1),  # 3 posts / group_size=5 → 1 group
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
        if name == "bsky_reorder":
            return _make_reorder_output(3)
        if name == "bsky_section_titles":
            return _make_titles_output(1)
        raise ValueError(f"Unexpected prompt: {name}")

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
