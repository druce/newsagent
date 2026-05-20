"""Tests for lib.fetch.brightdata.

Unit tests inject a fake SyncBrightDataClient via the `client=` kwarg so we
don't touch the network. A live integration test (`test_brightdata_live_wsj`)
is gated behind BRIGHTDATA_API_KEY + RUN_LIVE=1 so we don't burn proxy credits
on every test run.
"""
from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from lib.fetch import brightdata


_WSJ = (
    "https://www.wsj.com/tech/ai/"
    "the-american-rebellion-against-ai-is-gaining-steam-94b72529"
)


def _fake_result(status: str = "ready", data: object = None) -> SimpleNamespace:
    return SimpleNamespace(status=status, data=data)


def test_brightdata_returns_text_html_final_url_on_success():
    html = (
        "<html><body>"
        "<article><p>" + ("This is a substantial article body. " * 60) + "</p></article>"
        "</body></html>"
    )
    client = MagicMock()
    client.scrape_url.return_value = _fake_result(data=html)

    text, raw_html, final_url, err = brightdata.scrape_single_url_brightdata(
        "https://www.wsj.com/x", client=client
    )
    assert err is None
    assert raw_html == html
    assert text and len(text) > 100
    assert final_url == "https://www.wsj.com/x"
    client.scrape_url.assert_called_once_with("https://www.wsj.com/x")


def test_brightdata_thin_extract_returns_error():
    html = "<html><body><p>short</p></body></html>"
    client = MagicMock()
    client.scrape_url.return_value = _fake_result(data=html)
    text, raw_html, final_url, err = brightdata.scrape_single_url_brightdata(
        "https://www.wsj.com/x", client=client
    )
    assert text is None
    assert raw_html == html
    assert err is not None and "thin" in err.lower()


def test_brightdata_error_status_returns_error():
    client = MagicMock()
    client.scrape_url.return_value = _fake_result(status="error", data="")
    text, _, _, err = brightdata.scrape_single_url_brightdata(
        "https://www.wsj.com/x", client=client
    )
    assert text is None
    assert err and "status=error" in err


def test_brightdata_empty_data_returns_error():
    client = MagicMock()
    client.scrape_url.return_value = _fake_result(data="")
    text, _, _, err = brightdata.scrape_single_url_brightdata(
        "https://www.wsj.com/x", client=client
    )
    assert text is None
    assert err and "empty" in err.lower()


def test_brightdata_no_api_key_returns_error(monkeypatch):
    monkeypatch.delenv("BRIGHTDATA_API_KEY", raising=False)
    text, raw_html, final_url, err = brightdata.scrape_single_url_brightdata(
        "https://www.wsj.com/x"
    )
    assert text is None
    assert raw_html is None
    assert err is not None and "BRIGHTDATA_API_KEY" in err


def test_brightdata_passes_zone_when_set():
    client = MagicMock()
    client.scrape_url.return_value = _fake_result(data="<html>" + "x " * 600 + "</html>")
    brightdata.scrape_single_url_brightdata(
        "https://target.example/a", client=client, zone="myzone"
    )
    client.scrape_url.assert_called_once_with("https://target.example/a", zone="myzone")


def test_brightdata_sdk_exception_returns_error():
    client = MagicMock()
    client.scrape_url.side_effect = RuntimeError("boom")
    text, _, _, err = brightdata.scrape_single_url_brightdata(
        "https://x.example/a", client=client
    )
    assert text is None
    assert err and "boom" in err


def test_brightdata_batch_passes_each_url_to_same_client():
    html = "<html>" + ("x " * 600) + "</html>"
    client = MagicMock()
    client.scrape_url.return_value = _fake_result(data=html)

    # Patch SyncBrightDataClient context manager to yield our mock client.
    from unittest.mock import patch
    cm = MagicMock()
    cm.__enter__.return_value = client
    cm.__exit__.return_value = False
    with patch("lib.fetch.brightdata.SyncBrightDataClient", return_value=cm):
        results = brightdata.scrape_urls_brightdata(
            ["https://a.example/x", "https://b.example/y"],
            api_key="tok",
        )
    assert set(results.keys()) == {"https://a.example/x", "https://b.example/y"}
    assert all(r[3] is None for r in results.values())  # no errors
    assert client.scrape_url.call_count == 2


@pytest.mark.skipif(
    not (os.environ.get("BRIGHTDATA_API_KEY") and os.environ.get("RUN_LIVE")),
    reason="set BRIGHTDATA_API_KEY and RUN_LIVE=1 to run live Bright Data test",
)
def test_brightdata_live_wsj():  # pragma: no cover - network
    text, raw_html, final_url, err = brightdata.scrape_single_url_brightdata(_WSJ)
    assert err is None, f"Bright Data error: {err}"
    assert raw_html and len(raw_html) > 5000
    assert text and len(text) > 1000
    assert "AI" in text or "ai" in text.lower()
