"""Tests for evals.harness.label — the CSV labeling web harness."""
import csv
import threading
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from evals.harness import label as L

ROWS = [
    {"case_id": "a", "input_title": "AI lab raises", "rec_gold": "True", "info_new_terra": "True",
     "hidden": "x", "human_is_ai": "True"},
    {"case_id": "b", "input_title": "Chip fab opens", "rec_gold": "False", "info_new_terra": "True",
     "hidden": "y", "human_is_ai": ""},
    {"case_id": "c", "input_title": "Robot <b>arm</b>", "rec_gold": "True", "info_new_terra": "False",
     "hidden": "z", "human_is_ai": ""},
]


@pytest.fixture
def csv_path(tmp_path):
    p = tmp_path / "g.csv"
    with p.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(ROWS[0]))
        w.writeheader()
        w.writerows(ROWS)
    return p


def test_load_errors_on_missing_file_or_no_input_columns(tmp_path):
    with pytest.raises(L.HarnessError):
        L.LabelSet.load(tmp_path / "nope.csv", "human_is_ai")
    p = tmp_path / "x.csv"
    p.write_text("a,b\n1,2\n")
    with pytest.raises(L.HarnessError, match="input_"):
        L.LabelSet.load(p, "human_is_ai")


def test_load_adds_missing_label_column(tmp_path):
    p = tmp_path / "x.csv"
    p.write_text("input_title\nfoo\n")
    ls = L.LabelSet.load(p, "human_is_ai")
    assert ls.fieldnames[-1] == "human_is_ai" and ls.rows[0]["human_is_ai"] == ""


def test_visible_columns_by_prefix(csv_path):
    ls = L.LabelSet.load(csv_path, "human_is_ai")
    assert ls.columns("input_") == ["input_title"]
    assert ls.columns("rec_") == ["rec_gold"]
    assert ls.columns("info_") == ["info_new_terra"]


def test_first_unlabeled(csv_path):
    assert L.LabelSet.load(csv_path, "human_is_ai").first_unlabeled() == 1


def test_set_writes_file_in_place_preserving_other_columns(csv_path):
    ls = L.LabelSet.load(csv_path, "human_is_ai")
    ls.set(1, "False")
    rows = list(csv.DictReader(csv_path.open()))
    assert [r["human_is_ai"] for r in rows] == ["True", "False", ""]
    assert [r["hidden"] for r in rows] == ["x", "y", "z"]
    assert list(rows[0]) == list(ROWS[0])
    with pytest.raises(ValueError):
        ls.set(1, "maybe")


def test_render_page_shows_sections_and_disables_bounds(csv_path):
    ls = L.LabelSet.load(csv_path, "human_is_ai")
    first = L.render(ls, 0, show_rec=True, show_info=True)
    assert "AI lab raises" in first and "rec_gold" in first and "info_new_terra" in first
    assert "hidden" not in first.split("<body")[1].replace("hidden-", "")
    assert 'id="prev" disabled' in first and 'id="next" disabled' not in first
    last = L.render(ls, 2, show_rec=False, show_info=False)
    assert 'id="next" disabled' in last and "rec_gold" not in last and "info_new_terra" not in last
    assert "Robot &lt;b&gt;arm&lt;/b&gt;" in last           # escaped
    assert 'class="true current"' in first and 'current' not in last.split('<nav>')[1]  # saved value highlighted


def _post(url, data):
    req = urllib.request.Request(url, data=urllib.parse.urlencode(data).encode(), method="POST")
    return urllib.request.urlopen(req)


def test_server_root_redirects_to_first_unlabeled_and_post_saves_then_advances(csv_path):
    ls = L.LabelSet.load(csv_path, "human_is_ai")
    srv = ThreadingHTTPServer(("127.0.0.1", 0), L.make_handler(ls, show_rec=True, show_info=True))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_port}"
    try:
        r = urllib.request.urlopen(base + "/")
        assert r.url.endswith("/r/1")
        r = _post(base + "/r/1", {"value": "True"})
        assert r.url.endswith("/r/2")
        r = _post(base + "/r/2", {"value": "False"})          # last record: stay
        assert r.url.endswith("/r/2")
        assert [x["human_is_ai"] for x in csv.DictReader(csv_path.open())] == ["True", "True", "False"]
        with pytest.raises(urllib.error.HTTPError):
            urllib.request.urlopen(base + "/r/9")
    finally:
        srv.shutdown()
