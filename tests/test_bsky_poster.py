"""Tests for lib/bsky_queue/poster.py — post_item orchestration."""
from pathlib import Path

import lib.bsky_queue.poster as poster


def test_post_item_builds_card_with_thumbnail(monkeypatch, tmp_path):
    calls = {}

    monkeypatch.setattr(poster, "get_og_tags", lambda url: {
        "title": "OG Title", "description": "OG Desc",
        "image": "https://img.com/p.png",
    })

    def fake_download(url, dest_dir):
        p = Path(dest_dir) / "img.png"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"data")
        return p
    monkeypatch.setattr(poster, "download_image", fake_download)
    monkeypatch.setattr(poster, "resize_image", lambda p, desired_height=240: p)

    def fake_upload(session, path, mime):
        calls["upload"] = (str(path), mime)
        return {"$type": "blob", "ref": {"$link": "bafy"}}
    monkeypatch.setattr(poster, "bsky_upload_blob", fake_upload)

    def fake_create(session, *, text, uri, title, description, created_at, thumb):
        calls["create"] = dict(text=text, uri=uri, title=title,
                               description=description, thumb=thumb)
        return {"uri": "at://x/post/1"}
    monkeypatch.setattr(poster, "bsky_create_external_post", fake_create)

    out = poster.post_item({"accessJwt": "t", "did": "d"},
                           {"url": "https://news.com/s", "title": "Headline"},
                           tmp_path)

    assert out == "at://x/post/1"
    assert calls["upload"][1] == "image/png"
    assert calls["create"]["text"] == "Headline"
    assert calls["create"]["title"] == "OG Title"
    assert calls["create"]["thumb"] == {"$type": "blob", "ref": {"$link": "bafy"}}


def test_post_item_posts_without_thumb_when_image_download_fails(monkeypatch, tmp_path):
    captured = {}
    monkeypatch.setattr(poster, "get_og_tags", lambda url: {
        "title": "T", "description": "D", "image": "https://img.com/x.png",
    })
    monkeypatch.setattr(poster, "download_image", lambda url, dest_dir: None)

    def fail_resize(*a, **k):  # should never be reached
        raise AssertionError("resize should not run without a downloaded image")
    monkeypatch.setattr(poster, "resize_image", fail_resize)

    def fake_create(session, *, text, uri, title, description, created_at, thumb):
        captured["thumb"] = thumb
        return {"uri": "at://x/post/2"}
    monkeypatch.setattr(poster, "bsky_create_external_post", fake_create)

    out = poster.post_item({"accessJwt": "t", "did": "d"},
                           {"url": "https://news.com/s", "title": "H"}, tmp_path)
    assert out == "at://x/post/2"
    assert captured["thumb"] is None


def test_post_item_falls_back_to_item_title_when_no_og(monkeypatch, tmp_path):
    captured = {}
    monkeypatch.setattr(poster, "get_og_tags", lambda url: {})

    def fake_create(session, *, text, uri, title, description, created_at, thumb):
        captured.update(text=text, title=title, description=description, thumb=thumb)
        return {"uri": "at://x/post/3"}
    monkeypatch.setattr(poster, "bsky_create_external_post", fake_create)

    poster.post_item({"accessJwt": "t", "did": "d"},
                     {"url": "https://news.com/s", "title": "Fallback Title"}, tmp_path)
    assert captured["title"] == "Fallback Title"
    assert captured["description"] == ""
    assert captured["thumb"] is None
