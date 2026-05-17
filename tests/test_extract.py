from lib.fetch.extract import extract_article_links


_HTML = """
<html><body>
<a href="https://example.com/news/2026/05/article-1">Article One Headline (Long Enough)</a>
<a href="https://example.com/news/2026/05/article-2">Article Two Headline (Long Enough)</a>
<a href="https://example.com/ads/banner-1">SponsoredAdShort</a>
<a href="https://other.com/x">Off-domain link</a>
<a href="/relative/path">Relative link should resolve via base</a>
<a href="https://example.com/news/short">x</a>
<a href="javascript:void(0)">js link</a>
<a href="mailto:hi@example.com">email</a>
</body></html>
"""


def test_extract_respects_include_pattern():
    cfg = {
        "url": "https://example.com/",
        "include": [r"^https://example\.com/news/"],
        "minlength": 10,
    }
    links = extract_article_links(_HTML, cfg, source_name="Example")
    urls = [a.url for a in links]
    assert "https://example.com/news/2026/05/article-1" in urls
    assert "https://example.com/news/2026/05/article-2" in urls
    assert "https://example.com/ads/banner-1" not in urls
    assert "https://other.com/x" not in urls


def test_extract_respects_exclude_pattern():
    cfg = {
        "url": "https://example.com/",
        "exclude": [r"^https://example\.com/ads/"],
        "minlength": 10,
    }
    links = extract_article_links(_HTML, cfg, source_name="Example")
    urls = [a.url for a in links]
    assert "https://example.com/ads/banner-1" not in urls


def test_extract_drops_short_titles():
    cfg = {"url": "https://example.com/", "minlength": 20}
    links = extract_article_links(_HTML, cfg, source_name="Example")
    titles = [a.title for a in links]
    assert "x" not in titles
    assert "SponsoredAdShort" not in titles  # under 20 chars


def test_extract_drops_javascript_and_mailto():
    cfg = {"url": "https://example.com/", "minlength": 1}
    links = extract_article_links(_HTML, cfg, source_name="Example")
    urls = [a.url for a in links]
    assert not any(u.startswith("javascript:") for u in urls)
    assert not any(u.startswith("mailto:") for u in urls)


def test_extract_resolves_relative_urls():
    cfg = {"url": "https://example.com/", "minlength": 1}
    links = extract_article_links(_HTML, cfg, source_name="Example")
    urls = [a.url for a in links]
    assert "https://example.com/relative/path" in urls


def test_extract_returns_article_objects():
    cfg = {"url": "https://example.com/",
           "include": [r"^https://example\.com/news/"], "minlength": 10}
    links = extract_article_links(_HTML, cfg, source_name="Example")
    assert all(a.source == "Example" for a in links)
    assert all(a.published is None for a in links)
