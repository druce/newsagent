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


# --- article body extraction (trafilatura + __NEXT_DATA__ fallback) ---------
import json as _json
import time

from lib.fetch.extract import article_body_from_next_data, extract_article_text


def _next_data_html(body: str) -> str:
    payload = {"props": {"pageProps": {"data": {"articleBody": body}}}}
    return (
        "<html><head>"
        '<script id="__NEXT_DATA__" type="application/json">'
        + _json.dumps(payload)
        + "</script></head><body>"
        "<div>byKaran Sehgal | About Author</div>"  # boilerplate only
        "</body></html>"
    )


def test_article_body_from_next_data_returns_articlebody():
    body = "Real article prose. " * 50
    html = _next_data_html(body)
    assert article_body_from_next_data(html) == body


def test_article_body_from_next_data_absent_returns_none():
    assert article_body_from_next_data("<html><body>no next data</body></html>") is None


def test_article_body_from_next_data_malformed_json_returns_none():
    html = (
        '<html><head><script id="__NEXT_DATA__" type="application/json">'
        "{not valid json}</script></head></html>"
    )
    assert article_body_from_next_data(html) is None


def test_extract_article_text_prefers_next_data_when_trafilatura_thin():
    # trafilatura would only see the tiny boilerplate div; the real body lives
    # in __NEXT_DATA__. extract_article_text must recover the full body.
    body = "Climate-risk intelligence has a representation problem. " * 40
    html = _next_data_html(body)
    assert extract_article_text(html) == body


def test_extract_article_text_uses_trafilatura_when_richer():
    # A normal server-rendered article (no __NEXT_DATA__) still extracts via
    # trafilatura.
    paras = "".join(
        f"<p>{'This is a substantial paragraph of real article content. ' * 5}</p>"
        for _ in range(8)
    )
    html = f"<html><body><article>{paras}</article></body></html>"
    text = extract_article_text(html)
    assert text and "substantial paragraph" in text


def test_extract_article_text_serializes_trafilatura_calls(monkeypatch):
    """Concurrent extraction must never enter trafilatura simultaneously.

    trafilatura parses through a single module-global lxml HTMLParser
    (trafilatura/utils.py: HTML_PARSER). lxml parsers are not safe to share
    across threads: concurrent use corrupts the parser's interned-name dict
    and aborts the process inside _fixHtmlDictNames (SIGABRT), which killed
    the download step's 8-way Phase 1.
    """
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from lib.fetch import extract as extract_mod

    inside = 0
    overlaps = []
    guard = threading.Lock()

    def fake_extract(html, **kwargs):
        nonlocal inside
        with guard:
            inside += 1
            if inside > 1:
                overlaps.append(inside)
        time.sleep(0.01)  # widen the race window
        with guard:
            inside -= 1
        return "extracted body text"

    monkeypatch.setattr(extract_mod.trafilatura, "extract", fake_extract)

    html = "<html><body><article><p>hello</p></article></body></html>"
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: extract_article_text(html), range(32)))

    assert all(r == "extracted body text" for r in results)
    assert not overlaps, f"trafilatura.extract ran concurrently: {overlaps}"


def _split_body_html() -> str:
    """Ars-style page: body split into several post-content blocks with ads between."""
    def block(tag: str) -> str:
        paras = "".join(
            f"<p>{tag} paragraph {i} carries real reporting about the story at hand. "
            f"{'More substantive article detail follows here. ' * 4}</p>"
            for i in range(4)
        )
        return f'<div class="post-content post-content-double">{paras}</div>'

    ad = '<div class="ad-wrapper"><span>Advertisement</span></div>'
    return (
        "<html><head><title>Split story</title></head><body>"
        '<article class="double-column h-entry">'
        f'<div class="relative">{block("First")}</div>{ad}'
        f'<div class="relative">{block("Second")}</div>{ad}'
        f'<div class="relative">{block("Third")}</div>'
        "</article>"
        '<footer class="site-footer"><p>Ars Technica has been separating the signal '
        "from the noise for over 25 years with our unique combination of skills.</p></footer>"
        "</body></html>"
    )


def test_extract_article_text_joins_split_post_content_blocks():
    # Ars Technica splits one article across several div.post-content blocks
    # separated by ad slots; trafilatura alone keeps only the first block.
    text = extract_article_text(_split_body_html())
    assert text
    for tag in ("First", "Second", "Third"):
        assert f"{tag} paragraph 3" in text
    assert "separating the signal" not in text


def test_post_content_join_needs_multiple_blocks():
    from lib.fetch.extract import article_body_from_post_content

    single = (
        '<html><body><div class="post-content"><p>only one block</p></div></body></html>'
    )
    assert article_body_from_post_content(single) is None


# --- headline vs dek when several anchors share a URL -----------------------
_WSJ = """<div><a href="https://www.wsj.com/finance/stocks/nvidia-underappreciated-bdfd936c?mod=ai_lead_pos3" slotname="media"><picture><img alt="x" src="i.jpg"></picture></a>
<h3 class="css-fsvegl"><a data-testid="flexcard-headline" href="https://www.wsj.com/finance/stocks/nvidia-underappreciated-bdfd936c?mod=ai_lead_pos3"><div class="HeadlineTextBlock">Even at $5 Trillion, Nvidia Is Underappreciated</div></a></h3>
<div><a href="https://www.wsj.com/finance/stocks/nvidia-underappreciated-bdfd936c?mod=ai_lead_pos3"><span><p data-testid="flexcard-text">Competition is growing, but the AI chip maker’s sluggish stock doesn’t give enough credit for its strong position.</p></span></a></div>
<a href="https://www.wsj.com/finance/stocks/nvidia-underappreciated-bdfd936c?mod=ai_lead_pos3#comments_sector">82</a></div>"""

_FT = """<li class="o-header__mega-item"><a class="o-header__mega-link" href="/content/52b5f9a8">How Singapore became a route for Nvidia chips to China</a></li>
<div class="o-teaser__heading"><a class="js-teaser-heading-link" data-trackable="heading-link" href="/content/52b5f9a8?syn=1">How Singapore became a route for Nvidia chips to China</a></div>
<p class="o-teaser__standfirst"><a class="js-teaser-standfirst-link" data-trackable="standfirst-link" href="/content/52b5f9a8?syn=1">A suspected Chinese spy and Singaporean chip deal reveal the Asian networks testing Washington’s grip on AI technology</a></p>"""

_BLOOMBERG = """<a data-component="story-link" href="/news/features/2026-11-10/jensen-huang-succession"><div data-component="story-block"><div data-component="optional-eyebrow">The Big Take</div><div class="Headline_phoenix" data-component="headline" data-testid="headline"><span>Jensen Huang’s 700,000% Return Magnifies Nvidia Succession Crisis</span></div><section class="Summary_summary typography_articleDek" data-component="summary">The board is weighing the risk of a future without its singular leader.</section></div></a>"""

_ARS = """<h2><a href="https://arstechnica.com/ai/2026/06/pokemon-go-players/"><em>Pokémon Go</em> players unwittingly contributed to tech with military drone uses</a></h2>"""

_IMG_CAPTION = """<a href="https://venturebeat.com/ai/story-x">Credit: VentureBeat made with Midjourney</a>
<a href="https://venturebeat.com/ai/story-x">Startup ships a reasoning model that beats the frontier labs</a>"""


def _one(html, url):
    arts = extract_article_links(html, {"url": url}, source_name="S")
    assert len(arts) == 1, arts
    return arts[0]


def test_wsj_prefers_headline_anchor_and_keeps_dek_as_summary():
    a = _one(_WSJ, "https://www.wsj.com/tech/ai")
    assert a.title == "Even at $5 Trillion, Nvidia Is Underappreciated"
    assert a.rss_summary and a.rss_summary.startswith("Competition is growing")


def test_ft_prefers_heading_link_over_standfirst():
    a = _one(_FT, "https://www.ft.com/artificial-intelligence")
    assert a.title == "How Singapore became a route for Nvidia chips to China"
    assert a.rss_summary.startswith("A suspected Chinese spy")


def test_bloomberg_uses_inner_headline_not_eyebrow_and_dek():
    a = _one(_BLOOMBERG, "https://www.bloomberg.com/ai")
    assert a.title == "Jensen Huang’s 700,000% Return Magnifies Nvidia Succession Crisis"
    assert a.rss_summary == "The board is weighing the risk of a future without its singular leader."


def test_inline_markup_keeps_word_spacing():
    a = _one(_ARS, "https://arstechnica.com/ai/")
    assert a.title == "Pokémon Go players unwittingly contributed to tech with military drone uses"
    assert a.rss_summary is None


def test_without_headline_markup_longest_text_still_wins():
    a = _one(_IMG_CAPTION, "https://venturebeat.com/ai")
    assert a.title == "Startup ships a reasoning model that beats the frontier labs"
    assert a.rss_summary is None


def test_dek_inside_headline_anchor_without_dek_markup():
    # The Register / Washington Post: headline element + bare dek text in one anchor.
    html = """<a href="https://www.theregister.com/2026/11/10/leibniz/"><h4>Insight about how 'nature makes no leaps' promises better robot predictions</h4>
    <div>Leibniz lives on as researchers apply continuity to motion planning for humanoids</div></a>"""
    a = _one(html, "https://www.theregister.com/")
    assert a.title == "Insight about how 'nature makes no leaps' promises better robot predictions"
    assert a.rss_summary == "Leibniz lives on as researchers apply continuity to motion planning for humanoids"


def test_short_headline_marked_fragments_still_respect_minlength():
    html = """<h3><a href="https://www.businessinsider.com/so-expensive">So Expensive</a></h3>"""
    assert extract_article_links(html, {"url": "https://www.businessinsider.com/"}, source_name="S") == []


def test_style_tags_named_like_headlines_are_ignored():
    html = """<h3><a data-testid="flexcard-headline" href="https://www.wsj.com/opinion/honeypot-25b4"><style data-emotion="css kcrx2-HeadlineTextBlock">.x{margin:0}</style><div class="HeadlineTextBlock">Nvidia Gets a Chinese Honeypot Lesson</div></a></h3>"""
    assert _one(html, "https://www.wsj.com/tech/ai").title == "Nvidia Gets a Chinese Honeypot Lesson"


def test_short_headline_wins_when_url_also_has_a_long_dek():
    html = """<h3><a data-testid="flexcard-headline" href="https://www.wsj.com/tech/ai/big-blue-blues-b83c"><div>Big Blue Blues</div></a></h3>
    <div><a href="https://www.wsj.com/tech/ai/big-blue-blues-b83c"><p>Plus, what happens when you give an AI agent access to your passwords.</p></a></div>"""
    a = _one(html, "https://www.wsj.com/tech/ai")
    assert a.title == "Big Blue Blues"
    assert a.rss_summary.startswith("Plus, what happens")


def test_flag_labels_are_dropped_from_headline():
    html = """<h3><a data-testid="flexcard-headline" href="https://www.wsj.com/tech/ai/google-blackstone-0e35"><div class="HeadlineTextBlock"><div><div class="FlagContainer" data-testid="content-tag"><span>EXCLUSIVE</span></div></div>Google and Blackstone to Create New AI Cloud Company</div></a></h3>"""
    assert _one(html, "https://www.wsj.com/tech/ai").title == "Google and Blackstone to Create New AI Cloud Company"
