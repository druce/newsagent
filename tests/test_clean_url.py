from lib.utilities import clean_url


def test_strips_query_string():
    assert clean_url("https://example.com/article?utm_source=twitter&id=42") == \
        "https://example.com/article"


def test_strips_fragment():
    assert clean_url("https://example.com/post#section-2") == \
        "https://example.com/post"


def test_strips_query_and_fragment():
    assert clean_url("https://example.com/p/foo?x=1#top") == \
        "https://example.com/p/foo"


def test_strips_semicolon_params():
    assert clean_url("https://example.com/path;jsessionid=abc?x=1") == \
        "https://example.com/path"


def test_passes_clean_url_unchanged():
    assert clean_url("https://example.com/news/2026/05/article-1") == \
        "https://example.com/news/2026/05/article-1"


def test_preserves_trailing_slash():
    assert clean_url("https://example.com/section/?q=1") == \
        "https://example.com/section/"


def test_preserves_path_only_with_no_query():
    assert clean_url("https://example.com/") == "https://example.com/"


def test_empty_returns_empty():
    assert clean_url("") == ""


def test_non_string_returns_empty():
    assert clean_url(None) == ""  # type: ignore[arg-type]
    assert clean_url(123) == ""   # type: ignore[arg-type]
