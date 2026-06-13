"""newsagent:bluesky — fetch a Bluesky account's recent posts and render an HTML digest.

Execution modes
---------------
Classic (default, all-in-one — needs a non-subagent ``--engine`` to avoid
``claude -p``):
    python -m lib.steps.bluesky --user <handle> [--engine ENGINE]

Staged (in-session Agent dispatch — no ``claude -p``, no API engine):
    python -m lib.steps.bluesky --user <handle> --fetch          # 1. python: fetch + images
    # 2. dispatch a reorder Agent: reads reorder-request.json → writes reorder-result.json
    python -m lib.steps.bluesky --user <handle> --apply-reorder  # 3. python: ordered.json + HTML
    # 4. dispatch a titles Agent: reads titles-request.json → writes titles-result.json
    python -m lib.steps.bluesky --user <handle> --apply-titles   # 5. python: save titles artifact

The staged flow keeps the two prompts (bsky_reorder, bsky_section_titles) as
materialized request files the parent Claude session fulfils by dispatching
Agents under the current session. Per legacy parity the punny titles are a
SEPARATE artifact and are not merged back into the ordered HTML.

Env vars required (fetch + classic modes only):
    BSKY_USERNAME — Bluesky login identifier
    BSKY_SECRET   — Bluesky app password
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import date
from pathlib import Path
from textwrap import shorten

import click
from pydantic import ValidationError

from lib.bluesky.api import bsky_login, bsky_get_author_feed
from lib.bluesky.og_tags import get_og_tags
from lib.bluesky.images import download_image, resize_image
from lib.llm import call_prompt, get_prompt
from lib.prompts.bsky_reorder import BskyPost, BskyReorderInput, BskyReorderOutput
from lib.prompts.bsky_section_titles import (
    BskySectionGroup,
    BskySectionTitlesInput,
    BskySectionTitlesOutput,
)

# Strip a trailing inline-URL abbreviation Bluesky appends to truncated posts,
# e.g. "...run all the AI chips newatlas.com/technology/e..." (legacy notebook
# truncate_last_occurrence).
_TRAILING_URL_RE = re.compile(r"\s+\S+\.{3}$")

# Per-account dedup marker (the URI of the newest post seen on the last run).
_STATE_DIR = Path("download/bsky-state")
_IMAGE_DIR = Path("download/bsky-images")


def _clean_text(text: str) -> str:
    """Remove a trailing abbreviated-URL tail and surrounding whitespace."""
    return _TRAILING_URL_RE.sub("", text).strip()


def _extract_url(post: dict) -> str | None:
    """Extract first embedded external URL from a post item, if any."""
    embed = post.get("post", {}).get("embed", {}) or {}
    ext = embed.get("external", {}) or {}
    return ext.get("uri") or None


def _extract_text(post: dict) -> str:
    """Extract (and clean) post text from a feed item."""
    raw = post.get("post", {}).get("record", {}).get("text", "") or ""
    return _clean_text(raw)


def _post_uri(post: dict) -> str:
    """Stable identifier for a feed item (AT URI, falling back to text)."""
    return post.get("post", {}).get("uri") or _extract_text(post)


def _workdir(handle: str) -> Path:
    return Path("runs") / f"bsky-{handle}"


def _marker_path(handle: str) -> Path:
    return _STATE_DIR / f"{handle}.txt"


def _advance_marker(handle: str, newest_uri: str | None) -> None:
    if newest_uri:
        _STATE_DIR.mkdir(parents=True, exist_ok=True)
        _marker_path(handle).write_text(newest_uri, encoding="utf-8")


# ── shared fetch/enrich/group/render helpers ──────────────────────────────────

def _do_fetch(
    session: dict, handle: str, limit: int, no_dedup: bool
) -> tuple[list[dict], dict[str, dict], dict[str, Path | None], str | None]:
    """Fetch the feed (newest first), apply cross-run dedup, enrich OG + images.

    Returns (feed_items, og_cache, image_cache, newest_uri). The dedup marker is
    NOT advanced here — callers advance it once a deliverable is produced.
    """
    click.echo(f"Fetching posts for @{handle}...")
    feed_items = bsky_get_author_feed(session, handle, limit=limit)
    click.echo(f"Fetched {len(feed_items)} posts.")

    newest_uri = _post_uri(feed_items[0]) if feed_items else None

    marker_path = _marker_path(handle)
    if not no_dedup and marker_path.exists():
        marker = marker_path.read_text(encoding="utf-8").strip()
        kept: list[dict] = []
        for item in feed_items:
            if _post_uri(item) == marker:
                break
            kept.append(item)
        feed_items = kept

    # OG enrichment (best-effort, deduped by URL)
    og_cache: dict[str, dict] = {}
    for item in feed_items:
        url = _extract_url(item)
        if url and url not in og_cache:
            og_cache[url] = get_og_tags(url)

    # Image download + resize
    image_cache: dict[str, Path | None] = {}
    for url, og in og_cache.items():
        img_url = og.get("image")
        if img_url:
            path = download_image(img_url, _IMAGE_DIR)
            if path:
                try:
                    resize_image(path, desired_height=240)
                except Exception:
                    # A resize failure must never abort the run; the CSS caps
                    # display height to 240px regardless, so keep the original.
                    pass
            image_cache[url] = path

    return feed_items, og_cache, image_cache, newest_uri


def _build_reorder_posts(feed_items: list[dict], og_cache: dict[str, dict]) -> list[BskyPost]:
    posts = []
    for i, item in enumerate(feed_items):
        url = _extract_url(item) or ""
        og = og_cache.get(url, {})
        posts.append(
            BskyPost(
                index=i,
                text=shorten(_extract_text(item), width=300, placeholder="..."),
                og_title=og.get("title"),
                og_description=og.get("description"),
            )
        )
    return posts


def _groups_from_reorder(
    feed_items: list[dict], groups_data: list[dict]
) -> list[tuple[str, list[dict]]]:
    """Materialize (label, items) groups from the reorder output.

    Defensive: drop out-of-bounds and duplicate indexes; any index the model
    omitted is swept into a trailing "More headlines" group so nothing is lost.
    """
    n = len(feed_items)
    seen: set[int] = set()
    groups: list[tuple[str, list[dict]]] = []
    for grp in groups_data:
        label = grp.get("label") or "Untitled"
        idxs = grp.get("indexes", [])
        items = [feed_items[i] for i in idxs if 0 <= i < n and i not in seen]
        seen.update(i for i in idxs if 0 <= i < n)
        if items:
            groups.append((label, items))
    missing = [feed_items[i] for i in range(n) if i not in seen]
    if missing:
        groups.append(("More headlines", missing))
    return groups


def _render_reorder_request(posts: list[BskyPost]) -> dict:
    cfg = get_prompt("bsky_reorder")
    inp = BskyReorderInput(posts=posts)
    return {
        "prompt": "bsky_reorder",
        "system_prompt": cfg.system_prompt,
        "user_prompt": cfg.user_prompt.format(posts_json=inp.posts_json),
        "output_schema": BskyReorderOutput.model_json_schema(),
    }


def _render_titles_request(groups: list[tuple[str, list[dict]]]) -> dict:
    section_groups = [
        BskySectionGroup(
            label=label,
            sample_texts=[_extract_text(item) for item in items[:5]],
        )
        for label, items in groups
    ]
    cfg = get_prompt("bsky_section_titles")
    inp = BskySectionTitlesInput(groups=section_groups)
    return {
        "prompt": "bsky_section_titles",
        "system_prompt": cfg.system_prompt,
        "user_prompt": cfg.user_prompt.format(groups_json=inp.groups_json),
        "output_schema": BskySectionTitlesOutput.model_json_schema(),
        "labels": [label for label, _ in groups],
    }


def _render_html(
    grouped_posts: list[tuple[str, list[dict]]],
    og_cache: dict[str, dict],
    image_cache: dict[str, Path | None],
    today: str,
) -> str:
    """Render full HTML digest."""
    lines: list[str] = [
        "<!DOCTYPE html>",
        "<html lang='en'>",
        "<head>",
        "  <meta charset='utf-8'>",
        f"  <title>Bluesky Digest — {today}</title>",
        "  <style>",
        "    body { font-family: sans-serif; max-width: 800px; margin: 0 auto; padding: 20px; }",
        "    .post { border: 1px solid #ddd; border-radius: 6px; padding: 12px; margin: 12px 0; }",
        "    .post img { height: 240px; width: auto; display: block; margin: 8px 0; }",
        "    .og-link { color: #0066cc; text-decoration: none; font-weight: bold; }",
        "    .og-desc { color: #555; font-size: 0.9em; }",
        "    h1 { color: #333; }",
        "    h2 { color: #555; border-bottom: 1px solid #eee; padding-bottom: 4px; }",
        "  </style>",
        "</head>",
        "<body>",
        f"  <h1>Bluesky Digest — {today}</h1>",
    ]

    for section_title, posts in grouped_posts:
        lines.append(f"  <h2>{section_title}</h2>")
        for item in posts:
            text = _extract_text(item)
            url = _extract_url(item)
            og = og_cache.get(url, {}) if url else {}
            img_path = image_cache.get(url) if url else None

            lines.append("  <div class='post'>")
            lines.append(f"    <p>{text}</p>")

            if img_path and img_path.exists():
                rel_path = img_path.resolve().as_posix()
                lines.append(f"    <img src='file://{rel_path}' alt='post image'>")
            elif og.get("image"):
                lines.append(f"    <img src='{og['image']}' alt='post image'>")

            if url:
                og_title = og.get("title") or url
                og_desc = og.get("description", "")
                lines.append(f"    <a class='og-link' href='{url}'>{og_title}</a>")
                if og_desc:
                    lines.append(f"    <p class='og-desc'>{og_desc}</p>")

            lines.append("  </div>")

    lines += ["</body>", "</html>"]
    return "\n".join(lines)


def _write_digest(grouped_posts, og_cache, image_cache) -> tuple[Path, Path]:
    """Render + write out/bsky-<date>.html and the latest-bsky.html symlink."""
    today = date.today().isoformat()
    html = _render_html(grouped_posts, og_cache, image_cache, today)
    out_dir = Path("out")
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / f"bsky-{today}.html"
    out_path.write_text(html, encoding="utf-8")
    symlink_path = out_dir / "latest-bsky.html"
    if symlink_path.exists() or symlink_path.is_symlink():
        symlink_path.unlink()
    symlink_path.symlink_to(out_path.name)
    return out_path, symlink_path


# ── staged-mode stages ────────────────────────────────────────────────────────

def _stage_fetch(handle: str, limit: int, no_dedup: bool) -> None:
    username = os.environ.get("BSKY_USERNAME")
    secret = os.environ.get("BSKY_SECRET")
    if not username or not secret:
        raise click.ClickException("BSKY_USERNAME and BSKY_SECRET env vars are required.")
    click.echo(f"Logging in as {username}...")
    session = bsky_login(username, secret)

    feed_items, og_cache, image_cache, newest_uri = _do_fetch(session, handle, limit, no_dedup)

    if not feed_items:
        click.echo("No new posts since last run.")
        _advance_marker(handle, newest_uri)
        return

    wd = _workdir(handle)
    wd.mkdir(parents=True, exist_ok=True)
    (wd / "fetch.json").write_text(json.dumps({
        "handle": handle,
        "newest_uri": newest_uri,
        "no_dedup": no_dedup,
        "feed_items": feed_items,
        "og_cache": og_cache,
        "image_cache": {u: (str(p) if p else None) for u, p in image_cache.items()},
    }, indent=2))

    posts = _build_reorder_posts(feed_items, og_cache)
    (wd / "reorder-request.json").write_text(json.dumps(_render_reorder_request(posts), indent=2))

    click.echo(f"Wrote {wd / 'fetch.json'} ({len(feed_items)} posts) and {wd / 'reorder-request.json'}.")
    click.echo("Next: dispatch a reorder Agent that reads reorder-request.json and writes")
    click.echo(f"  {wd / 'reorder-result.json'}")
    click.echo(f"Then run: python -m lib.steps.bluesky --user {handle} --apply-reorder")


def _stage_apply_reorder(handle: str) -> None:
    wd = _workdir(handle)
    fetch_path = wd / "fetch.json"
    result_path = wd / "reorder-result.json"
    if not fetch_path.exists():
        raise click.ClickException(f"missing {fetch_path}; run --fetch first")
    if not result_path.exists():
        raise click.ClickException(f"missing {result_path}; dispatch the reorder Agent first")

    fetch = json.loads(fetch_path.read_text())
    feed_items = fetch["feed_items"]
    og_cache = fetch["og_cache"]
    image_cache = {u: (Path(p) if p else None) for u, p in fetch["image_cache"].items()}

    try:
        reorder = BskyReorderOutput.model_validate_json(result_path.read_text())
    except ValidationError as exc:
        raise click.ClickException(
            f"reorder-result.json failed schema validation ({exc.error_count()} errors); "
            f"redispatch the reorder Agent"
        )

    groups = _groups_from_reorder(
        feed_items, [g.model_dump() for g in reorder.groups]
    )

    out_path, symlink_path = _write_digest(groups, og_cache, image_cache)

    # Persist the ordered structure for the titles stage + as a record.
    (wd / "ordered.json").write_text(json.dumps({
        "handle": handle,
        "date": date.today().isoformat(),
        "groups": [{"label": label, "posts": items} for label, items in groups],
    }, indent=2))

    (wd / "titles-request.json").write_text(json.dumps(_render_titles_request(groups), indent=2))

    _advance_marker(handle, fetch.get("newest_uri"))

    click.echo(f"Written: {out_path}")
    click.echo(f"Symlink: {symlink_path}")
    click.echo(f"Ordered {len(groups)} topical section(s) → {wd / 'ordered.json'}.")
    click.echo("Next: dispatch a titles Agent that reads titles-request.json and writes")
    click.echo(f"  {wd / 'titles-result.json'}")
    click.echo(f"Then run: python -m lib.steps.bluesky --user {handle} --apply-titles")


def _stage_apply_titles(handle: str) -> None:
    wd = _workdir(handle)
    ordered_path = wd / "ordered.json"
    result_path = wd / "titles-result.json"
    if not ordered_path.exists():
        raise click.ClickException(f"missing {ordered_path}; run --apply-reorder first")
    if not result_path.exists():
        raise click.ClickException(f"missing {result_path}; dispatch the titles Agent first")

    ordered = json.loads(ordered_path.read_text())
    labels = [g["label"] for g in ordered["groups"]]

    try:
        parsed = BskySectionTitlesOutput.model_validate_json(result_path.read_text())
    except ValidationError as exc:
        raise click.ClickException(
            f"titles-result.json failed schema validation ({exc.error_count()} errors); "
            f"redispatch the titles Agent"
        )

    titles = list(parsed.titles)
    # Fall back to the neutral topic label if the Agent returned too few titles.
    while len(titles) < len(labels):
        titles.append(labels[len(titles)])
    titles = titles[: len(labels)]

    sections = [{"label": label, "title": title} for label, title in zip(labels, titles)]
    (wd / "titles.json").write_text(json.dumps({
        "handle": handle,
        "date": ordered.get("date"),
        "titles": titles,
        "sections": sections,
    }, indent=2))
    # Plain-text list of just the suggested titles, one per line, for easy copy-paste.
    (wd / "titles.txt").write_text("\n".join(titles) + "\n")

    click.echo(f"Saved {len(titles)} section title(s) → {wd / 'titles.json'} (+ titles.txt):")
    for s in sections:
        click.echo(f"  • {s['title']}   ({s['label']})")


def _run_classic(handle: str, limit: int, no_dedup: bool, engine: str | None) -> None:
    username = os.environ.get("BSKY_USERNAME")
    secret = os.environ.get("BSKY_SECRET")
    if not username or not secret:
        raise click.ClickException("BSKY_USERNAME and BSKY_SECRET env vars are required.")
    click.echo(f"Logging in as {username}...")
    session = bsky_login(username, secret)

    feed_items, og_cache, image_cache, newest_uri = _do_fetch(session, handle, limit, no_dedup)
    if not feed_items:
        click.echo("No new posts since last run.")
        _advance_marker(handle, newest_uri)
        return

    posts = _build_reorder_posts(feed_items, og_cache)
    reorder_result = call_prompt(
        "bsky_reorder", BskyReorderInput(posts=posts),
        **({"engine": engine} if engine else {}),
    )
    groups = _groups_from_reorder(feed_items, [g.model_dump() for g in reorder_result.groups])

    section_groups = [
        BskySectionGroup(label=label, sample_texts=[_extract_text(it) for it in items[:5]])
        for label, items in groups
    ]
    titles_result = call_prompt(
        "bsky_section_titles", BskySectionTitlesInput(groups=section_groups),
        **({"engine": engine} if engine else {}),
    )
    titles = list(titles_result.titles)
    while len(titles) < len(groups):
        titles.append(groups[len(titles)][0])

    grouped_posts = list(zip(titles, [items for _, items in groups]))
    out_path, symlink_path = _write_digest(grouped_posts, og_cache, image_cache)
    _advance_marker(handle, newest_uri)
    click.echo(f"Written: {out_path}")
    click.echo(f"Symlink: {symlink_path}")


@click.command()
@click.option("--user", "handle", required=True, help="Bluesky handle to fetch")
@click.option("--limit", default=80, show_default=True, help="Max posts to fetch")
@click.option(
    "--no-dedup",
    is_flag=True,
    default=False,
    help="Process the full feed, ignoring the cross-run dedup marker",
)
@click.option("--engine", default=None, help="Override LLM engine (classic mode)")
@click.option("--fetch", "fetch_mode", is_flag=True, default=False,
              help="Staged stage 1: fetch + images, write fetch.json + reorder-request.json")
@click.option("--apply-reorder", "apply_reorder", is_flag=True, default=False,
              help="Staged stage 3: read reorder-result.json → ordered.json + HTML + titles-request.json")
@click.option("--apply-titles", "apply_titles", is_flag=True, default=False,
              help="Staged stage 5: read titles-result.json → save titles.json artifact")
def cli(
    handle: str,
    limit: int,
    no_dedup: bool,
    engine: str | None,
    fetch_mode: bool,
    apply_reorder: bool,
    apply_titles: bool,
) -> None:
    """Fetch Bluesky posts and render a daily digest HTML."""
    if sum([fetch_mode, apply_reorder, apply_titles]) > 1:
        raise click.UsageError(
            "--fetch, --apply-reorder, and --apply-titles are mutually exclusive"
        )

    if fetch_mode:
        _stage_fetch(handle, limit, no_dedup)
    elif apply_reorder:
        _stage_apply_reorder(handle)
    elif apply_titles:
        _stage_apply_titles(handle)
    else:
        _run_classic(handle, limit, no_dedup, engine)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(cli())
