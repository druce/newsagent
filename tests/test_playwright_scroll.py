"""Unit tests for the convergence-based infinite-scroll loop in
lib.fetch.playwright_runner. No browser: a FakePage scripts what the in-page
metric returns as a function of the scroll calls made so far, and a FakeClock
makes sleeps/time deterministic."""
import asyncio

import pytest

import lib.fetch.playwright_runner as pr


class FakeClock:
    def __init__(self):
        self.t = 0.0
        self.sleeps: list[float] = []

    def now(self) -> float:
        return self.t

    async def sleep(self, d: float) -> None:
        self.sleeps.append(d)
        self.t += d


class FakePage:
    """`evaluate(js, arg)` dispatches on the JS text: the scroll snippet records
    the (selector, nudge) arg and returns whether the selector matched; the
    metric snippet returns `metric_fn(scroll_calls)` so growth is a function
    of how many (and which) scrolls have happened — not of how often we poll."""

    def __init__(self, metric_fn, selector_found: bool = True):
        self.metric_fn = metric_fn
        self.selector_found = selector_found
        self.scroll_calls: list[bool] = []   # nudge flag per scroll
        self.evaluate_calls: list[tuple[str, object]] = []
        self.wfs_calls: list[tuple[str, float]] = []

    async def wait_for_selector(self, sel, timeout):
        self.wfs_calls.append((sel, timeout))
        if not self.selector_found:
            raise RuntimeError("Timeout waiting for selector")

    async def evaluate(self, js, arg=None):
        self.evaluate_calls.append((js, arg))
        if "scrollTop" in js:
            _sel, nudge = arg
            self.scroll_calls.append(bool(nudge))
            return self.selector_found
        return self.metric_fn(self.scroll_calls)


@pytest.fixture
def clock(monkeypatch):
    c = FakeClock()
    monkeypatch.setattr(pr, "_now", c.now)
    monkeypatch.setattr(pr, "_sleep", c.sleep)
    monkeypatch.setattr(pr, "_uniform", lambda a, b: a)
    return c


def _run(page, **kw):
    defaults = dict(scroll_div="#frame", max_pages=5, scroll_wait=2.0,
                    initial_sleep=0.0, time_budget=600.0)
    defaults.update(kw)
    return asyncio.run(pr._scroll_until_settled(page, **defaults))


def test_runs_exactly_n_pages_when_growth_continues(clock):
    page = FakePage(lambda calls: 40 + 21 * len(calls))
    stats = _run(page, max_pages=5, initial_sleep=3.0)
    assert stats.pages == 5
    assert stats.stopped == "max_pages"
    assert stats.links == 40 + 21 * 4
    assert page.scroll_calls == [False, False, False, False]
    # initial_sleep is still a blind minimum settle (plus the low jitter bound)
    assert clock.sleeps[0] == pytest.approx(3.0 + 0.5)


def test_stops_early_when_no_growth_after_retries(clock):
    page = FakePage(lambda calls: 40)
    stats = _run(page, max_pages=10, max_retries=2)
    assert stats.stopped == "end_of_feed"
    assert stats.pages == 1
    assert stats.links == 40
    # one plain scroll, then two nudged retries
    assert page.scroll_calls == [False, True, True]


def test_nudge_recovers_stalled_load(clock):
    # Only a nudged scroll makes the feed grow.
    page = FakePage(lambda calls: 40 + 21 * sum(1 for c in calls if c))
    stats = _run(page, max_pages=3)
    assert stats.pages == 3
    assert stats.stopped == "max_pages"
    assert page.scroll_calls == [False, True, False, True]


def test_missing_selector_falls_back_to_window_and_terminates(clock):
    page = FakePage(lambda calls: 40, selector_found=False)
    stats = _run(page, scroll_div="#nope", max_pages=4, max_retries=1)
    assert page.wfs_calls and page.wfs_calls[0][0] == "#nope"
    assert stats.stopped == "end_of_feed"
    assert len(page.scroll_calls) == 2   # still scrolled (window fallback), still terminated


def test_time_budget_terminates(clock):
    page = FakePage(lambda calls: 40 + 21 * len(calls))
    stats = _run(page, max_pages=1000, time_budget=5.0, scroll_wait=1.0)
    assert stats.stopped == "time_budget"
    assert 1 < stats.pages < 1000
    assert clock.t >= 5.0


def test_scroll_zero_is_noop(clock):
    page = FakePage(lambda calls: 40)
    stats = _run(page, max_pages=0, initial_sleep=0.0)
    assert stats.stopped == "no_scroll"
    assert page.evaluate_calls == []
    assert page.wfs_calls == []


def test_scroll_js_passes_selector_as_argument(clock):
    sel = "#a[data-x='y\"z']"
    page = FakePage(lambda calls: 40 + 21 * len(calls))
    _run(page, scroll_div=sel, max_pages=2)
    assert page.evaluate_calls
    for js, arg in page.evaluate_calls:
        assert sel not in js
        assert arg == sel or (isinstance(arg, (list, tuple)) and arg[0] == sel)


def test_no_scroll_div_scrolls_window(clock):
    page = FakePage(lambda calls: 40 + 21 * len(calls))
    stats = _run(page, scroll_div="", max_pages=3)
    assert stats.pages == 3
    assert page.wfs_calls == []
    assert all(arg[0] == "" for js, arg in page.evaluate_calls if "scrollTop" in js)
