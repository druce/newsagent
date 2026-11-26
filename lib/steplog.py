"""Tee a pipeline step's stdout/stderr to runs/<SID>/<step>.log.

Steps print with click.echo and nothing used to be persisted; the GUI's Log tab
and output pane read these files. Each invocation appends a `=== <step> ...`
header, and every line is prefixed with HH:MM:SS. While the step runs a
`runs/<SID>/<step>.running` marker holds the PID so the GUI can show it as
running (checkpoints are only written when a step completes).

Usage on a step CLI (innermost decorator, below the click options):

    @click.command()
    @click.option("--session", "session_id", required=True)
    @step_log("rate")
    def cli(session_id, ...): ...
"""
from __future__ import annotations

import atexit
import functools
import logging
import os
import sys
import traceback
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator, TextIO

RUNS_ROOT = Path("runs")


def log_path(runs_root: Path | str, session_id: str, step: str) -> Path:
    return Path(runs_root) / session_id / f"{step}.log"


def running_marker(runs_root: Path | str, session_id: str, step: str) -> Path:
    return Path(runs_root) / session_id / f"{step}.running"


ERROR_MARK = "✗ "


def _fmt_elapsed(sec: float) -> str:
    if sec < 60:
        return f"{sec:.1f}s"
    m, s = divmod(int(round(sec)), 60)
    return f"{m}m {s:02d}s" if m < 60 else f"{m // 60}h {m % 60:02d}m"


class _Tee:
    """Text stream that writes to the original stream and a timestamped log file."""

    def __init__(self, orig: TextIO, fh: TextIO) -> None:
        self._orig = orig
        self._fh = fh
        self._bol = True  # at beginning of a line in the file

    def write(self, s: str) -> int:
        self._orig.write(s)
        self.write_file(s)
        return len(s)

    def write_file(self, s: str, mark: bool = False) -> None:
        """Append to the log only; `mark` prefixes every line with ✗ (errors)."""
        if not s:
            return
        stamp = datetime.now().strftime("%H:%M:%S ")
        out = []
        for part in s.splitlines(keepends=True):
            if self._bol:
                out.append(stamp + (ERROR_MARK if mark else ""))
            out.append(part)
            self._bol = part.endswith("\n")
        self._fh.write("".join(out))
        self._fh.flush()

    def flush(self) -> None:
        self._orig.flush()
        self._fh.flush()

    def isatty(self) -> bool:
        return False

    def __getattr__(self, name):  # encoding, errors, fileno, ...
        return getattr(self._orig, name)


class _MarkingFormatter(logging.Formatter):
    """Prefix ERROR/CRITICAL records with ✗ so they stand out in the log."""

    def format(self, record: logging.LogRecord) -> str:
        text = super().format(record)
        if record.levelno >= logging.ERROR:
            text = "\n".join(ERROR_MARK + ln for ln in text.splitlines())
        return text


def _exit_code(exc: BaseException | None) -> int:
    if exc is None:
        return 0
    if isinstance(exc, SystemExit):
        code = exc.code
        return code if isinstance(code, int) else (0 if code is None else 1)
    if isinstance(exc, KeyboardInterrupt):
        return 130
    return getattr(exc, "exit_code", 1)  # click exceptions carry one


@contextmanager
def tee_step_log(step: str, session_id: str,
                 runs_root: Path | str | None = None) -> Iterator[Path]:
    """Tee output to runs/<SID>/<step>.log between a `=== <step>` header and a
    `=== end <step> … rc=N` footer; errors are written with a ✗ prefix."""
    runs_root = RUNS_ROOT if runs_root is None else runs_root
    path = log_path(runs_root, session_id, step)
    path.parent.mkdir(parents=True, exist_ok=True)
    marker = running_marker(runs_root, session_id, step)
    fh = open(path, "a", encoding="utf-8")
    t0 = datetime.now()
    fh.write(f"=== {step} {t0.isoformat(timespec='seconds')} "
             f"pid={os.getpid()} argv={' '.join(sys.argv[1:])}\n")
    fh.flush()
    marker.write_text(f"{os.getpid()}\n")
    old_out, old_err = sys.stdout, sys.stderr
    tee_out, tee_err = _Tee(old_out, fh), _Tee(old_err, fh)
    handler = logging.StreamHandler(tee_err)
    handler.setFormatter(_MarkingFormatter("%(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    sys.stdout, sys.stderr = tee_out, tee_err
    root.addHandler(handler)
    failure: BaseException | None = None
    try:
        yield path
    except BaseException as exc:
        failure = exc
        rc = _exit_code(exc)
        if rc != 0:
            # File only: the console still gets Python's / click's own report.
            if hasattr(exc, "format_message") and not isinstance(exc, SystemExit):
                tee_err.write_file(f"Error: {exc.format_message()}\n", mark=True)  # click
            elif not isinstance(exc, SystemExit):
                tee_err.write_file(traceback.format_exc(), mark=True)
        raise
    finally:
        root.removeHandler(handler)
        sys.stdout, sys.stderr = old_out, old_err
        try:
            marker.unlink()
        except FileNotFoundError:
            pass
        rc = _exit_code(failure)
        end = datetime.now()
        why = ""
        if rc != 0 and failure is not None and not isinstance(failure, SystemExit):
            msg = str(getattr(failure, "message", None) or failure).splitlines()
            why = f" {ERROR_MARK}{type(failure).__name__}: {(msg[0] if msg else '')[:200]}"
        elif rc != 0:
            why = f" {ERROR_MARK}exit code {rc}"
        if not (tee_out._bol and tee_err._bol):  # finish a dangling partial line
            fh.write("\n")
        fh.write(f"=== end {step} {end.isoformat(timespec='seconds')} rc={rc} "
                 f"· {_fmt_elapsed((end - t0).total_seconds())}{why}\n")
        fh.close()
        _register_exit_line(path, step)


_exit_lines: set[str] = set()


def _register_exit_line(path: Path, step: str) -> None:
    """At interpreter exit, append `=== exit <step> …`. atexit handlers run after
    Python has joined leftover non-daemon threads, so a gap between `=== end` and
    `=== exit` means the process hung while shutting down; a prompt exit line with
    a late next step means a child process (e.g. a browser) outlived it."""
    key = str(path)
    if key in _exit_lines:
        return
    _exit_lines.add(key)

    def _write() -> None:
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(f"=== exit {step} {datetime.now().isoformat(timespec='seconds')} "
                        f"pid={os.getpid()}\n")
        except OSError:
            pass
    atexit.register(_write)


def step_log(step: str):
    """Decorator for a step's click callback; tees output when session_id is known."""
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            sid = kwargs.get("session_id")
            if not sid:
                return fn(*args, **kwargs)
            with tee_step_log(step, sid):
                return fn(*args, **kwargs)
        return wrapper
    return deco
