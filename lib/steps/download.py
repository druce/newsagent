"""news:download — fetch full article HTML and extract main text."""
from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

import click
import trafilatura

from lib.fetch.playwright_runner import fetch_url_html
from lib.state import NewsletterAgentState


_DOWNLOAD_DIR = Path("download")


def _safe_filename(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:24] + ".txt"


def _extract_text(html: str) -> Optional[str]:
    return trafilatura.extract(html, include_comments=False, include_tables=False)


@click.command()
@click.option("--db", "db_path", default="newsletter_agent.db")
@click.option("--session", "session_id", required=True)
@click.option("--max", "max_urls", type=int, default=None,
              help="Cap on number of URLs downloaded this run.")
def cli(db_path: str, session_id: str, max_urls: int | None) -> None:
    state = NewsletterAgentState(session_id=session_id, db_path=db_path).load_latest_from_db()
    if state is None:
        raise click.ClickException(f"No state found for session {session_id}")

    state.start_step("download")
    state.save_checkpoint("download")
    _DOWNLOAD_DIR.mkdir(exist_ok=True)

    pending = [h for h in state.headline_data if not h.get("text_path")]
    if max_urls:
        pending = pending[:max_urls]

    downloaded = 0
    failures: list[dict] = []
    for h in pending:
        url = h["url"]
        try:
            html = fetch_url_html(url)
            text = _extract_text(html)
            if not text:
                failures.append({"url": url, "error": "no text extracted"})
                continue
            target = _DOWNLOAD_DIR / _safe_filename(url)
            target.write_text(text)
            h["text_path"] = str(target)
            downloaded += 1
        except Exception as exc:
            failures.append({"url": url, "error": str(exc)[:300]})

    state.complete_step(
        "download",
        message=f"downloaded {downloaded}/{len(pending)} articles",
    )
    state.save_checkpoint("download")

    runs_dir = Path("runs") / session_id
    runs_dir.mkdir(parents=True, exist_ok=True)
    (runs_dir / "download.json").write_text(json.dumps({
        "session_id": session_id,
        "completed_at": datetime.now().isoformat(),
        "downloaded": downloaded,
        "failures": failures,
    }, indent=2))

    click.echo(f"Downloaded {downloaded}/{len(pending)} articles.")
    if failures:
        click.echo(f"Failures: {len(failures)} (see runs/{session_id}/download.json)")


if __name__ == "__main__":  # pragma: no cover
    sys.exit(cli())
