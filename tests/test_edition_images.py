"""lib/edition_images.py — og:image candidates for edition items (network mocked)."""
from __future__ import annotations

import hashlib
from pathlib import Path

from PIL import Image

import lib.edition_images as ei
from lib.bluesky.images import resize_to_width


def _png(path: Path, w: int, h: int) -> Path:
    Image.new("RGB", (w, h), "red").save(path)
    return path


def test_resize_to_width_downscales_and_keeps_ratio(tmp_path):
    p = resize_to_width(_png(tmp_path / "a.png", 1200, 600), 600)
    assert Image.open(p).size == (600, 300)


def test_resize_to_width_never_upscales(tmp_path):
    p = resize_to_width(_png(tmp_path / "a.png", 400, 200), 600)
    assert Image.open(p).size == (400, 200)


def _fake_download(sizes: dict[str, tuple[int, int] | None]):
    """download_image stand-in: writes a PNG of the given size, or returns None."""
    calls: list[str] = []

    def dl(url, dest_dir, max_bytes=5_000_000):
        calls.append(url)
        size = sizes.get(url)
        if size is None:
            return None
        dest_dir.mkdir(parents=True, exist_ok=True)
        stem = hashlib.sha256(url.encode()).hexdigest()[:16]
        return _png(dest_dir / f"{stem}.png", *size)
    dl.calls = calls
    return dl


def test_fetch_item_images_reasons(tmp_path, monkeypatch):
    og = {"https://a.com/1": {"image": "https://img/ok.jpg"},
          "https://b.com/2": {},
          "https://c.com/3": {"image": "https://img/broken.jpg"},
          "https://d.com/4": {"image": "https://img/tiny.jpg"},
          "https://e.com/5": {"image": "https://img/logo.png"},
          "https://e.com/6": {"image": "https://img/logo.png"}}
    monkeypatch.setattr(ei, "get_og_tags", lambda url: og[url])
    monkeypatch.setattr(ei, "download_image", _fake_download(
        {"https://img/ok.jpg": (1200, 800), "https://img/tiny.jpg": (100, 80),
         "https://img/logo.png": (800, 400)}))
    urls = {1: "https://a.com/1", 2: "https://b.com/2", 3: "https://c.com/3",
            4: "https://d.com/4", 5: "https://e.com/5", 6: "https://e.com/6"}
    got = ei.fetch_item_images(urls, tmp_path / "imgs", workers=2)
    path, reason = got[1]
    assert reason == "" and path.exists() and Image.open(path).size == (600, 400)
    assert got[2] == (None, "no og:image")
    assert got[3] == (None, "fetch failed")
    assert got[4] == (None, "too small")
    assert got[5] == (None, "logo") and got[6] == (None, "logo")


def test_fetch_item_images_same_article_url_not_logo(tmp_path, monkeypatch):
    seen: list[str] = []

    def og(url):
        seen.append(url)
        return {"image": "https://img/x.jpg"}
    monkeypatch.setattr(ei, "get_og_tags", og)
    monkeypatch.setattr(ei, "download_image", _fake_download({"https://img/x.jpg": (900, 450)}))
    got = ei.fetch_item_images({1: "https://a.com/1", 2: "https://a.com/1"}, tmp_path, workers=2)
    assert got[1][1] == "" and got[2][1] == "" and got[1][0] == got[2][0]
    assert seen == ["https://a.com/1"]


def test_fetch_item_images_relative_og_url(tmp_path, monkeypatch):
    monkeypatch.setattr(ei, "get_og_tags", lambda url: {"image": "/static/hero.png"})
    dl = _fake_download({"https://a.com/static/hero.png": (700, 350)})
    monkeypatch.setattr(ei, "download_image", dl)
    got = ei.fetch_item_images({1: "https://a.com/news/1"}, tmp_path)
    assert got[1][1] == "" and dl.calls == ["https://a.com/static/hero.png"]


def test_fetch_item_images_non_image_bytes(tmp_path, monkeypatch):
    def dl(url, dest_dir, max_bytes=5_000_000):
        dest_dir.mkdir(parents=True, exist_ok=True)
        p = dest_dir / "junk.jpg"
        p.write_bytes(b"not an image")
        return p
    monkeypatch.setattr(ei, "get_og_tags", lambda url: {"image": "https://img/j.jpg"})
    monkeypatch.setattr(ei, "download_image", dl)
    assert ei.fetch_item_images({1: "https://a.com/1"}, tmp_path)[1] == (None, "fetch failed")


def test_fetch_item_images_reuses_cached_file(tmp_path, monkeypatch):
    img = "https://img/c.jpg"
    stem = hashlib.sha256(img.encode()).hexdigest()[:16]
    _png(tmp_path / f"{stem}.jpg", 600, 300)
    monkeypatch.setattr(ei, "get_og_tags", lambda url: {"image": img})
    dl = _fake_download({})
    monkeypatch.setattr(ei, "download_image", dl)
    got = ei.fetch_item_images({1: "https://a.com/1"}, tmp_path)
    assert got[1] == (tmp_path / f"{stem}.jpg", "") and dl.calls == []
