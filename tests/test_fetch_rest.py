import respx
import httpx
from lib.fetch.rest import fetch_rest


_CFG = {
    "url": "https://api.example.com/articles",
    "api_key_env": "TEST_API_KEY",
    "api_key_header": "X-Api-Key",
    "items_path": "articles",
    "title_field": "title",
    "url_field": "url",
    "published_field": "publishedAt",
    "summary_field": "description",
}


@respx.mock
def test_fetch_rest_extracts_articles(monkeypatch):
    monkeypatch.setenv("TEST_API_KEY", "k123")
    route = respx.get("https://api.example.com/articles").mock(
        return_value=httpx.Response(200, json={
            "articles": [
                {"title": "Article A", "url": "https://e.com/a",
                 "publishedAt": "2026-05-17T10:00:00Z", "description": "desc A"},
                {"title": "Article B", "url": "https://e.com/b",
                 "publishedAt": "2026-05-17T11:00:00Z", "description": "desc B"},
            ]
        })
    )
    result = fetch_rest("ExampleAPI", _CFG)
    assert result.ok
    assert len(result.articles) == 2
    assert result.articles[0].title == "Article A"
    assert result.articles[0].rss_summary == "desc A"
    # Auth header sent
    assert route.calls.last.request.headers["x-api-key"] == "k123"


@respx.mock
def test_fetch_rest_skips_articles_missing_url(monkeypatch):
    monkeypatch.setenv("TEST_API_KEY", "k")
    respx.get("https://api.example.com/articles").mock(
        return_value=httpx.Response(200, json={
            "articles": [
                {"title": "A", "url": "https://e.com/a"},
                {"title": "B", "url": ""},   # skipped
                {"title": "C"},              # skipped
            ]
        })
    )
    result = fetch_rest("ExampleAPI", _CFG)
    assert len(result.articles) == 1


@respx.mock
def test_fetch_rest_http_error(monkeypatch):
    monkeypatch.setenv("TEST_API_KEY", "k")
    respx.get("https://api.example.com/articles").mock(
        return_value=httpx.Response(401, text="bad key")
    )
    result = fetch_rest("ExampleAPI", _CFG)
    assert not result.ok
    assert "401" in (result.error or "")


def test_fetch_rest_missing_api_key_env(monkeypatch):
    monkeypatch.delenv("TEST_API_KEY", raising=False)
    result = fetch_rest("ExampleAPI", _CFG)
    assert not result.ok
    assert "TEST_API_KEY" in (result.error or "")
