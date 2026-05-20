"""Generic JSON REST API fetcher (NewsAPI-style)."""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import httpx

from lib.fetch.types import Article, FetchResult


_TIMEOUT = httpx.Timeout(15.0, connect=5.0)


def fetch_rest(source_name: str, source_cfg: dict) -> FetchResult:
    url = source_cfg.get("url")
    if not url:
        return FetchResult(source=source_name, ok=False, error="No URL in source config")

    headers: dict[str, str] = {}
    params: dict[str, str] = dict(source_cfg.get("params") or {})

    api_key_env = source_cfg.get("api_key_env")
    if api_key_env:
        key = os.environ.get(api_key_env)
        if not key:
            return FetchResult(source=source_name, ok=False,
                               error=f"Env var {api_key_env} not set")
        api_key_param = source_cfg.get("api_key_param")
        if api_key_param:
            params[api_key_param] = key
        else:
            headers[source_cfg.get("api_key_header", "Authorization")] = key

    from_hours_ago = source_cfg.get("from_hours_ago")
    if from_hours_ago is not None:
        ts = datetime.now(timezone.utc) - timedelta(hours=int(from_hours_ago))
        params[source_cfg.get("from_param", "from")] = ts.strftime("%Y-%m-%dT%H:%M:%S")

    try:
        with httpx.Client(timeout=_TIMEOUT, follow_redirects=True) as client:
            resp = client.get(url, headers=headers, params=params or None)
    except httpx.HTTPError as exc:
        return FetchResult(source=source_name, ok=False, error=f"HTTP error: {exc}")

    if resp.status_code != 200:
        return FetchResult(source=source_name, ok=False,
                           error=f"HTTP {resp.status_code}: {resp.text[:200]}")

    try:
        payload = resp.json()
    except Exception as exc:
        return FetchResult(source=source_name, ok=False, error=f"Non-JSON response: {exc}")

    items_path = source_cfg.get("items_path", "articles")
    items = payload.get(items_path, [])
    title_field = source_cfg.get("title_field", "title")
    url_field = source_cfg.get("url_field", "url")
    published_field = source_cfg.get("published_field", "publishedAt")
    summary_field = source_cfg.get("summary_field", "description")

    articles: list[Article] = []
    for it in items:
        url_val = it.get(url_field) or ""
        title_val = it.get(title_field) or ""
        if not url_val or not title_val:
            continue
        articles.append(Article(
            source=source_name,
            title=title_val,
            url=url_val,
            published=it.get(published_field),
            rss_summary=it.get(summary_field),
        ))

    return FetchResult(source=source_name, articles=articles, ok=True)
