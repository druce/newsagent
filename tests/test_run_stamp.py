"""Tests for run_stamp() — the YYYY-MM-DD-HHMMSS suffix on out/ filenames."""
import re
from datetime import date
from pathlib import Path

from lib.utilities import run_stamp, write_short_digest


STAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-\d{6}$")


def test_stamp_derived_from_standard_session_id():
    assert run_stamp("2026-07-27-18-00-07") == "2026-07-27-180007"


def test_stamp_is_stable_for_the_same_session():
    assert run_stamp("2026-07-27-07-00-10") == run_stamp("2026-07-27-07-00-10")


def test_stamp_distinguishes_two_runs_on_the_same_day():
    morning = run_stamp("2026-07-27-07-00-10")
    evening = run_stamp("2026-07-27-18-00-07")
    assert morning != evening
    assert morning.startswith("2026-07-27-")
    assert evening.startswith("2026-07-27-")


def test_stamp_falls_back_to_now_for_nonstandard_session_id():
    stamp = run_stamp("my-ad-hoc-session")
    assert STAMP_RE.match(stamp)
    assert stamp.startswith(date.today().isoformat())


def test_stamp_falls_back_to_now_when_session_id_is_none():
    stamp = run_stamp(None)
    assert STAMP_RE.match(stamp)
    assert stamp.startswith(date.today().isoformat())


def test_stamp_rejects_out_of_range_time_components():
    """A same-shaped but invalid id (hour 99) must not produce a bogus stamp."""
    stamp = run_stamp("2026-07-27-99-00-07")
    assert stamp.startswith(date.today().isoformat())


def test_short_digest_filename_carries_the_session_stamp(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    dated, _ = write_short_digest(
        "2026-07-27-18-00-07",
        [{"title": "T", "short_summary": "S", "url": "https://e.com/1"}],
        out_dir=tmp_path,
    )
    assert dated.name == "2026-07-27-180007_short.html"


def test_short_digest_rerun_overwrites_rather_than_duplicating(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    args = ("2026-07-27-18-00-07", [{"title": "T", "short_summary": "S", "url": "https://e.com/1"}])
    write_short_digest(*args, out_dir=tmp_path)
    write_short_digest(*args, out_dir=tmp_path)
    # Exclude the latest_short.html symlink, which also ends in _short.html.
    assert len(list(Path(tmp_path).glob("2026-*_short.html"))) == 1


def test_short_digest_latest_symlink_points_at_stamped_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    dated, _ = write_short_digest(
        "2026-07-27-18-00-07",
        [{"title": "T", "short_summary": "S", "url": "https://e.com/1"}],
        out_dir=tmp_path,
    )
    latest = Path(tmp_path) / "latest_short.html"
    assert latest.exists()
    assert latest.read_text() == dated.read_text()
