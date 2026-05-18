"""news:bluesky — fetch a Bluesky account's recent posts and render an HTML digest.

Usage:
    python -m lib.steps.bluesky --user <handle> [--limit 80] [--group-size 5] [--engine ENGINE]

Env vars required:
    BSKY_USERNAME — Bluesky login identifier
    BSKY_SECRET   — Bluesky app password
"""
from __future__ import annotations

import math
import os
import sys
from datetime import date
from pathlib import Path
from textwrap import shorten

import click

from lib.bluesky.api import bsky_login, bsky_get_author_feed
from lib.bluesky.og_tags import get_og_tags
from lib.bluesky.images import download_image, resize_image
from lib.llm import call_prompt
from lib.prompts.bsky_reorder import BskyPost, BskyReorderInput
from lib.prompts.bsky_section_titles import BskySectionGroup, BskySectionTitlesInput


def _extract_url(post: dict) -> str | None:
    """Extract first embedded external URL from a post item, if any."""
    embed = post.get("post", {}).get("embed", {}) or {}
    ext = embed.get("external", {}) or {}
    return ext.get("uri") or None


def _extract_text(post: dict) -> str:
    """Extract post text from a feed item."""
    return post.get("post", {}).get("record", {}).get("text", "") or ""


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


@click.command()
@click.option("--user", "handle", required=True, help="Bluesky handle to fetch")
@click.option("--limit", default=80, show_default=True, help="Max posts to fetch")
@click.option("--group-size", default=5, show_default=True, help="Posts per section")
@click.option("--engine", default=None, help="Override LLM engine")
def cli(handle: str, limit: int, group_size: int, engine: str | None) -> None:
    """Fetch Bluesky posts and render a daily digest HTML."""
    username = os.environ.get("BSKY_USERNAME")
    secret = os.environ.get("BSKY_SECRET")
    if not username or not secret:
        raise click.ClickException(
            "BSKY_USERNAME and BSKY_SECRET env vars are required."
        )

    # 1. Authenticate
    click.echo(f"Logging in as {username}...")
    session = bsky_login(username, secret)

    # 2. Fetch feed
    click.echo(f"Fetching posts for @{handle}...")
    feed_items = bsky_get_author_feed(session, handle, limit=limit)
    click.echo(f"Fetched {len(feed_items)} posts.")

    # 3. Extract URLs and fetch OG tags
    og_cache: dict[str, dict] = {}
    for item in feed_items:
        url = _extract_url(item)
        if url and url not in og_cache:
            og_cache[url] = get_og_tags(url)

    # 4. Download + resize images
    image_dir = Path("download/bsky-images")
    image_cache: dict[str, Path | None] = {}
    for url, og in og_cache.items():
        img_url = og.get("image")
        if img_url:
            path = download_image(img_url, image_dir)
            if path:
                resize_image(path, desired_height=240)
            image_cache[url] = path

    # 5. Build reorder input and call LLM
    bsky_posts = [
        BskyPost(
            index=i,
            text=shorten(_extract_text(item), width=300, placeholder="..."),
            og_title=og_cache.get(_extract_url(item) or "", {}).get("title"),
            og_description=og_cache.get(_extract_url(item) or "", {}).get("description"),
        )
        for i, item in enumerate(feed_items)
    ]

    reorder_input = BskyReorderInput(posts=bsky_posts)
    reorder_result = call_prompt("bsky_reorder", reorder_input, **({"engine": engine} if engine else {}))
    ordered_indexes = reorder_result.indexes

    # Reorder feed items
    ordered_items = [feed_items[i] for i in ordered_indexes if i < len(feed_items)]

    # 6. Group into chunks
    n_groups = max(1, math.ceil(len(ordered_items) / group_size))
    groups: list[list[dict]] = []
    for g in range(n_groups):
        start = g * group_size
        end = start + group_size
        groups.append(ordered_items[start:end])

    # 7. Generate section titles
    section_groups = [
        BskySectionGroup(
            index_range=f"{g * group_size + 1}-{min((g + 1) * group_size, len(ordered_items))}",
            sample_texts=[_extract_text(item) for item in chunk[:3]],
        )
        for g, chunk in enumerate(groups)
    ]
    titles_input = BskySectionTitlesInput(groups=section_groups)
    titles_result = call_prompt("bsky_section_titles", titles_input, **({"engine": engine} if engine else {}))
    titles = titles_result.titles

    # Pad titles if necessary
    while len(titles) < len(groups):
        titles.append(f"Section {len(titles) + 1}")

    grouped_posts = list(zip(titles, groups))

    # 8. Render HTML
    today = date.today().isoformat()
    html = _render_html(grouped_posts, og_cache, image_cache, today)

    # 9. Write output
    out_dir = Path("out")
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / f"bsky-{today}.html"
    out_path.write_text(html, encoding="utf-8")

    # Symlink out/latest-bsky.html
    symlink_path = out_dir / "latest-bsky.html"
    if symlink_path.exists() or symlink_path.is_symlink():
        symlink_path.unlink()
    symlink_path.symlink_to(out_path.name)

    click.echo(f"Written: {out_path}")
    click.echo(f"Symlink: {symlink_path}")


if __name__ == "__main__":  # pragma: no cover
    sys.exit(cli())
