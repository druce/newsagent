"""Tests for lib/steplog.py — tee a step's stdout/stderr to runs/<SID>/<step>.log."""
import os
import sys

import click
from click.testing import CliRunner

from lib.steplog import running_marker, step_log, tee_step_log


def test_tee_writes_file_and_stdout(tmp_path, capsys):
    with tee_step_log("rate", "S1", runs_root=tmp_path):
        print("hello")
        click.echo("from click")
        print("oops", file=sys.stderr)
    out = capsys.readouterr()
    assert "hello" in out.out and "from click" in out.out
    assert "oops" in out.err
    text = (tmp_path / "S1" / "rate.log").read_text()
    assert "=== rate" in text  # per-invocation header
    assert "hello" in text and "from click" in text and "oops" in text
    # every content line is timestamped HH:MM:SS
    line = next(ln for ln in text.splitlines() if "hello" in ln)
    assert line[2] == ":" and line[5] == ":"


def test_tee_appends_and_restores_streams(tmp_path):
    before = sys.stdout
    with tee_step_log("gather", "S1", runs_root=tmp_path):
        print("one")
    with tee_step_log("gather", "S1", runs_root=tmp_path):
        print("two")
    assert sys.stdout is before
    text = (tmp_path / "S1" / "gather.log").read_text()
    assert text.count("=== gather") == 2 and "one" in text and "two" in text


def test_running_marker_lifecycle(tmp_path):
    marker = running_marker(tmp_path, "S1", "cluster")
    with tee_step_log("cluster", "S1", runs_root=tmp_path):
        assert marker.exists()
        assert marker.read_text().strip() == str(os.getpid())
    assert not marker.exists()


def test_marker_removed_on_exception(tmp_path):
    marker = running_marker(tmp_path, "S1", "cluster")
    try:
        with tee_step_log("cluster", "S1", runs_root=tmp_path):
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert not marker.exists()
    assert "RuntimeError" in (tmp_path / "S1" / "cluster.log").read_text()


def test_decorator_on_click_cli(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    @click.command()
    @click.option("--session", "session_id", required=True)
    @step_log("dedupe")
    def cli(session_id):
        click.echo(f"working on {session_id}")

    res = CliRunner().invoke(cli, ["--session", "S9"])
    assert res.exit_code == 0, res.output
    import lib.steplog
    assert "working on S9" in (lib.steplog.RUNS_ROOT / "S9" / "dedupe.log").read_text()


def test_decorator_without_session_is_noop(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    @click.command()
    @click.option("--session", "session_id", default=None)
    @step_log("start")
    def cli(session_id):
        click.echo("ok")

    res = CliRunner().invoke(cli, [])
    assert res.exit_code == 0 and "ok" in res.output
    import lib.steplog
    assert not lib.steplog.RUNS_ROOT.exists()


def test_end_line_on_success(tmp_path):
    with tee_step_log("rate", "S1", runs_root=tmp_path):
        print("partial", end="")
    lines = (tmp_path / "S1" / "rate.log").read_text().splitlines()
    assert lines[-1].startswith("=== end rate ") and " rc=0 · " in lines[-1]
    assert "✗" not in lines[-1]
    assert lines[-2].endswith("partial")  # dangling line terminated before the footer


def test_exception_marks_traceback_and_end_line(tmp_path, capsys):
    try:
        with tee_step_log("crossdedupe", "S1", runs_root=tmp_path):
            print("working")
            raise RuntimeError("no credits remaining")
    except RuntimeError:
        pass
    text = (tmp_path / "S1" / "crossdedupe.log").read_text()
    tb = [ln for ln in text.splitlines() if "Traceback" in ln or "RuntimeError" in ln]
    assert tb and all(ln[9:11] == "✗ " for ln in tb if not ln.startswith("==="))
    working = next(ln for ln in text.splitlines() if "working" in ln)
    assert "✗" not in working
    end = text.splitlines()[-1]
    assert " rc=1 " in end and "✗ RuntimeError: no credits remaining" in end
    assert "Traceback" not in capsys.readouterr().err  # not duplicated on the console


def test_click_error_is_one_marked_line(tmp_path, monkeypatch):
    import lib.steplog
    monkeypatch.setattr(lib.steplog, "RUNS_ROOT", tmp_path)

    @click.command()
    @click.option("--session", "session_id", required=True)
    @step_log("gather")
    def cli(session_id):
        raise click.ClickException("source halted")

    res = CliRunner().invoke(cli, ["--session", "S2"])
    assert res.exit_code == 1
    text = (tmp_path / "S2" / "gather.log").read_text()
    assert "✗ Error: source halted" in text and "Traceback" not in text
    assert " rc=1 " in text.splitlines()[-1]


def test_logging_errors_marked(tmp_path):
    import logging
    with tee_step_log("download", "S1", runs_root=tmp_path):
        logging.getLogger("x").error("boom")
        logging.getLogger("x").warning("careful")
    text = (tmp_path / "S1" / "download.log").read_text()
    assert "✗ ERROR x: boom" in text
    assert "✗ WARNING" not in text and "WARNING x: careful" in text


def test_exit_line_written_at_interpreter_exit(tmp_path):
    import subprocess
    import sys
    code = ("import lib.steplog as s\n"
            f"with s.tee_step_log('send', 'S1', runs_root={str(tmp_path)!r}):\n"
            "    print('hi')\n")
    subprocess.run([sys.executable, "-c", code], check=True, capture_output=True)
    lines = (tmp_path / "S1" / "send.log").read_text().splitlines()
    assert lines[-2].startswith("=== end send ") and lines[-1].startswith("=== exit send ")
