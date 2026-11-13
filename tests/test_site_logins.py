"""Unit tests for lib.fetch.site_logins — per-batch auto-login for sites whose
session cookies are browser-session-only (Digitimes). No browser: FakeCtx /
FakePage record what the login flow did."""
import asyncio

import pytest

import lib.fetch.site_logins as sl


DT_URL = "https://www.digitimes.com/news/a20261002PD224/micron.html"


class FakePage:
    def __init__(self, final_url="https://www.digitimes.com/"):
        self.url = "about:blank"
        self.final_url = final_url
        self.calls: list[tuple] = []
        self.closed = False

    async def goto(self, url, **kw):
        self.calls.append(("goto", url))
        self.url = url

    async def fill(self, sel, value):
        self.calls.append(("fill", sel, value))

    async def click(self, sel, **kw):
        self.calls.append(("click", sel))
        self.url = self.final_url

    async def wait_for_load_state(self, *a, **kw):
        pass

    async def wait_for_timeout(self, ms):
        pass

    async def close(self):
        self.closed = True


class FakeCtx:
    def __init__(self, page=None):
        self.page = page or FakePage()
        self.new_page_calls = 0

    async def new_page(self):
        self.new_page_calls += 1
        return self.page


@pytest.fixture
def dt_creds(monkeypatch):
    monkeypatch.setenv("DIGITIMES_EMAIL", "me@example.com")
    monkeypatch.setenv("DIGITIMES_PASSWORD", "secret")


def test_logins_for_matches_host_and_subdomains():
    got = sl.logins_for([DT_URL, "https://digitimes.com/x", "https://example.com/a"])
    assert [l.domain for l in got] == ["digitimes.com"]


def test_logins_for_ignores_lookalike_domains():
    assert sl.logins_for(["https://notdigitimes.com/a"]) == []


def test_logins_for_empty_when_no_matching_urls():
    assert sl.logins_for(["https://example.com/a"]) == []


def test_perform_logins_skips_without_credentials(monkeypatch):
    monkeypatch.delenv("DIGITIMES_EMAIL", raising=False)
    monkeypatch.delenv("DIGITIMES_PASSWORD", raising=False)
    ctx = FakeCtx()
    done = asyncio.run(sl.perform_logins(ctx, [DT_URL]))
    assert done == []
    assert ctx.new_page_calls == 0


def test_perform_logins_noop_for_unrelated_urls(dt_creds):
    ctx = FakeCtx()
    done = asyncio.run(sl.perform_logins(ctx, ["https://example.com/a"]))
    assert done == []
    assert ctx.new_page_calls == 0


def test_digitimes_login_fills_form_and_reports_success(dt_creds):
    ctx = FakeCtx()
    done = asyncio.run(sl.perform_logins(ctx, [DT_URL, DT_URL]))
    assert done == ["digitimes.com"]
    assert ctx.new_page_calls == 1          # once per batch, not per URL
    calls = ctx.page.calls
    assert ("goto", sl.DIGITIMES_LOGIN_URL) in calls
    assert ("fill", "input[name=EMail]", "me@example.com") in calls
    assert ("fill", "input[name=Password]", "secret") in calls
    assert ctx.page.closed


def test_digitimes_login_failure_when_still_on_login_page(dt_creds):
    # Bad credentials: the form posts back to join.asp instead of redirecting.
    ctx = FakeCtx(FakePage(final_url=sl.DIGITIMES_LOGIN_URL))
    done = asyncio.run(sl.perform_logins(ctx, [DT_URL]))
    assert done == []
    assert ctx.page.closed


def test_perform_logins_swallows_exceptions(dt_creds):
    class BoomPage(FakePage):
        async def goto(self, url, **kw):
            raise RuntimeError("net down")

    ctx = FakeCtx(BoomPage())
    done = asyncio.run(sl.perform_logins(ctx, [DT_URL]))
    assert done == []
    assert ctx.page.closed


def test_batch_fetch_runs_logins_before_fetching(monkeypatch):
    """fetch_urls_html_batch_async logs in on the shared context first."""
    import lib.fetch.playwright_runner as pr

    order: list[str] = []

    class Page:
        url = DT_URL

        async def goto(self, u, **kw):
            order.append("fetch")

        async def content(self):
            return "<html></html>"

        async def close(self):
            pass

    class Ctx:
        async def new_page(self):
            return Page()

        async def close(self):
            pass

    async def fake_launch(p, headless=True):
        return Ctx()

    async def fake_logins(ctx, urls):
        order.append("login")
        return []

    class FakePW:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(pr, "launch_stealth_context_async", fake_launch)
    monkeypatch.setattr(pr, "perform_logins", fake_logins)
    monkeypatch.setattr(pr, "_await_aggregator_redirect", lambda page: asyncio.sleep(0))
    monkeypatch.setattr(pr, "_await_hydration", lambda page: asyncio.sleep(0))
    import playwright.async_api as pwa
    monkeypatch.setattr(pwa, "async_playwright", lambda: FakePW())

    res = asyncio.run(pr.fetch_urls_html_batch_async([DT_URL], block_resources=False))
    assert res[DT_URL][2] is None
    assert order == ["login", "fetch"]
