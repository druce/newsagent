"""newsagent:bluesky — fetch a Bluesky account's recent posts and render an HTML digest.

Execution modes
---------------
Classic (default, all-in-one — needs a non-subagent ``--engine`` to avoid
``claude -p``):
    python -m lib.steps.bluesky --user <handle> [--engine ENGINE]

Staged (in-session Agent dispatch — no ``claude -p``, no API engine):
    python -m lib.steps.bluesky --user <handle> --fetch           # 1. python: fetch + images
    # 2. dispatch a reorder Agent: reads reorder-request.json → writes reorder-result.json
    python -m lib.steps.bluesky --user <handle> --apply-reorder   # 3. python: ordered.json + HTML
    # 4. dispatch a headlines Agent: reads headlines-request.json → writes headlines-result.json
    python -m lib.steps.bluesky --user <handle> --apply-headlines # 5. python: save punny-headline artifact

The staged flow keeps the two prompts (bsky_reorder, bsky_headlines) as
materialized request files the parent Claude session fulfils by dispatching
Agents under the current session. The digest HTML is a flat list of posts in
importance order (legacy skynet.html format — no section headers). The punny
headline rewrites are a SEPARATE artifact and are not merged into the HTML.

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
from lib.prompts.bsky_headlines import BskyHeadlinesInput, BskyHeadlinesOutput
from lib.sources import pretty_source

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


def _ordered_headlines(groups: list[tuple[str, list[dict]]]) -> list[str]:
    """Flatten groups into the final ordered list of post headlines (post text)."""
    return [_extract_text(item) for _label, items in groups for item in items]


def _render_headlines_request(groups: list[tuple[str, list[dict]]]) -> dict:
    headlines = _ordered_headlines(groups)
    cfg = get_prompt("bsky_headlines")
    inp = BskyHeadlinesInput(headlines=headlines)
    return {
        "prompt": "bsky_headlines",
        "system_prompt": cfg.system_prompt,
        "user_prompt": cfg.user_prompt.format(headlines_json=inp.headlines_json),
        "output_schema": BskyHeadlinesOutput.model_json_schema(),
        "headlines": headlines,
    }


def _site_name(url: str, og: dict, db_path: str = "newsletter_agent.db") -> str:
    """Source label for a post (mirrors the legacy BlueSky notebook):
    og:site_name, else the curated ``sites`` table name for the URL's domain
    (with subdomain stripping), else the bare domain (sans www.).

    The sites-table lookup is what turns e.g. ``lemonde.fr`` into "Le Monde";
    without it, posts whose target page exposes no ``og:site_name`` (common on
    the Playwright/redirect fetch path) fall through to a raw domain.
    """
    name = og.get("site_name")
    if name:
        return name
    return pretty_source(url, db_path=db_path)


def _render_html(
    grouped_posts: list[tuple[str, list[dict]]],
    og_cache: dict[str, dict],
    image_cache: dict[str, Path | None],
    today: str,
    handle: str,
) -> str:
    """Render the digest as a flat list of posts (legacy skynet.html format).

    Posts come pre-ordered by importance (related items adjacent); the group
    labels are NOT rendered — there are no section headers. Each post is an
    optional image paragraph, then either a linked headline ending with the
    source name in italics, or — for link-less posts — the bare post text.
    """
    lines: list[str] = [
        "<!DOCTYPE html>",
        "<html lang='en'>",
        "<head>",
        "  <meta charset='utf-8'>",
        f"  <title>Bluesky Digest — {today}</title>",
        "  <style>",
        "    body { font-family: sans-serif; max-width: 800px; margin: 0 auto; padding: 20px; }",
        "    img { height: 240px; width: auto; display: block; margin: 8px 0; }",
        "    a { color: #0066cc; }",
        "  </style>",
        "</head>",
        "<body>",
    ]

    for _label, posts in grouped_posts:
        for item in posts:
            text = _extract_text(item)
            url = _extract_url(item)
            og = og_cache.get(url, {}) if url else {}
            img_path = image_cache.get(url) if url else None

            if img_path and img_path.exists():
                rel_path = img_path.resolve().as_posix()
                lines.append(f"  <p><img alt='image' src='file://{rel_path}'></p>")
            elif og.get("image"):
                lines.append(f"  <p><img alt='image' src='{og['image']}'></p>")

            if url:
                lines.append(
                    f"  <p><a href='{url}'>{text}</a>  - <em>{_site_name(url, og)}</em></p>"
                )
            else:
                lines.append(f"  <p>{text}</p>")

            lines.append("  <hr />")

    lines.append(
        f"  <p>Follow the latest AI headlines via "
        f"<a href='https://bsky.app/profile/{handle}'>{handle} on Bluesky</a></p>"
    )
    lines += ["</body>", "</html>"]
    return "\n".join(lines)


def _write_digest(grouped_posts, og_cache, image_cache, handle) -> tuple[Path, Path]:
    """Render + write out/bsky-<date>.html and the latest-bsky.html symlink."""
    today = date.today().isoformat()
    html = _render_html(grouped_posts, og_cache, image_cache, today, handle)
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

    out_path, symlink_path = _write_digest(groups, og_cache, image_cache, handle)

    # Persist the ordered structure for the headlines stage + as a record.
    (wd / "ordered.json").write_text(json.dumps({
        "handle": handle,
        "date": date.today().isoformat(),
        "groups": [{"label": label, "posts": items} for label, items in groups],
    }, indent=2))

    (wd / "headlines-request.json").write_text(
        json.dumps(_render_headlines_request(groups), indent=2)
    )

    _advance_marker(handle, fetch.get("newest_uri"))

    n_posts = sum(len(items) for _, items in groups)
    click.echo(f"Written: {out_path}")
    click.echo(f"Symlink: {symlink_path}")
    click.echo(f"Ordered {n_posts} post(s) → {wd / 'ordered.json'}.")
    click.echo("Next: dispatch a headlines Agent that reads headlines-request.json and writes")
    click.echo(f"  {wd / 'headlines-result.json'}")
    click.echo(f"Then run: python -m lib.steps.bluesky --user {handle} --apply-headlines")


def _stage_apply_headlines(handle: str) -> None:
    wd = _workdir(handle)
    ordered_path = wd / "ordered.json"
    request_path = wd / "headlines-request.json"
    result_path = wd / "headlines-result.json"
    if not ordered_path.exists():
        raise click.ClickException(f"missing {ordered_path}; run --apply-reorder first")
    if not result_path.exists():
        raise click.ClickException(
            f"missing {result_path}; dispatch the headlines Agent first"
        )

    # The original headlines, in final order, come from the request file.
    request = json.loads(request_path.read_text()) if request_path.exists() else {}
    originals = request.get("headlines", [])

    try:
        parsed = BskyHeadlinesOutput.model_validate_json(result_path.read_text())
    except ValidationError as exc:
        raise click.ClickException(
            f"headlines-result.json failed schema validation ({exc.error_count()} errors); "
            f"redispatch the headlines Agent"
        )

    rewrites = list(parsed.headlines)
    # Fall back to the original headline if the Agent returned too few rewrites.
    while len(rewrites) < len(originals):
        rewrites.append(originals[len(rewrites)])
    if originals:
        rewrites = rewrites[: len(originals)]

    pairs = [
        {"headline": originals[i] if i < len(originals) else "", "rewrite": r}
        for i, r in enumerate(rewrites)
    ]
    (wd / "headlines.json").write_text(json.dumps({
        "handle": handle,
        "date": json.loads(ordered_path.read_text()).get("date"),
        "headlines": rewrites,
        "pairs": pairs,
    }, indent=2))
    # Plain-text list of just the punny rewrites, one per line, for easy copy-paste.
    (wd / "headlines.txt").write_text("\n".join(rewrites) + "\n")

    click.echo(
        f"Saved {len(rewrites)} punny headline rewrite(s) → "
        f"{wd / 'headlines.json'} (+ headlines.txt):"
    )
    for r in rewrites:
        click.echo(f"  • {r}")


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

    # The digest itself is the flat, plain-headline format (legacy skynet.html).
    out_path, symlink_path = _write_digest(groups, og_cache, image_cache, handle)

    # Punny headline rewrites are a SEPARATE artifact (not merged into the HTML).
    headlines = _ordered_headlines(groups)
    rewrites_result = call_prompt(
        "bsky_headlines", BskyHeadlinesInput(headlines=headlines),
        **({"engine": engine} if engine else {}),
    )
    rewrites = list(rewrites_result.headlines)
    while len(rewrites) < len(headlines):
        rewrites.append(headlines[len(rewrites)])
    rewrites = rewrites[: len(headlines)]
    wd = _workdir(handle)
    wd.mkdir(parents=True, exist_ok=True)
    (wd / "headlines.txt").write_text("\n".join(rewrites) + "\n")

    _advance_marker(handle, newest_uri)
    click.echo(f"Written: {out_path}")
    click.echo(f"Symlink: {symlink_path}")
    click.echo(f"Punny headline rewrites → {wd / 'headlines.txt'}")


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
              help="Staged stage 3: read reorder-result.json → ordered.json + HTML + headlines-request.json")
@click.option("--apply-headlines", "apply_headlines", is_flag=True, default=False,
              help="Staged stage 5: read headlines-result.json → save punny headlines.json/.txt artifact")
def cli(
    handle: str,
    limit: int,
    no_dedup: bool,
    engine: str | None,
    fetch_mode: bool,
    apply_reorder: bool,
    apply_headlines: bool,
) -> None:
    """Fetch Bluesky posts and render a daily digest HTML."""
    if sum([fetch_mode, apply_reorder, apply_headlines]) > 1:
        raise click.UsageError(
            "--fetch, --apply-reorder, and --apply-headlines are mutually exclusive"
        )

    if fetch_mode:
        _stage_fetch(handle, limit, no_dedup)
    elif apply_reorder:
        _stage_apply_reorder(handle)
    elif apply_headlines:
        _stage_apply_headlines(handle)
    else:
        _run_classic(handle, limit, no_dedup, engine)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(cli())
