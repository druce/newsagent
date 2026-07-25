#!/usr/bin/env python3
"""build_image_clipboard.py — stage ALL of the bsky digest's images on the OS
clipboard as a single base64 JSON map, so the whole set uploads in ONE paste (no
per-image loop, no model-token cost — the bytes ride the clipboard as text).

Reads `out/latest-bsky.html`, pulls EVERY `<img src=...>` IN DOCUMENT ORDER (de-duped,
first occurrence wins), gets the raw bytes for each, and writes a JSON map keyed by the
image's **exact digest `src` string** to a file + the clipboard (pbcopy):

    { "<src>": { "n": "<filename>", "b": "<base64 bytes>" }, ... }

Two kinds of `src` appear in the digest and both are handled:
  - local thumbnails: `file:///…/download/bsky-images/<hash>.<ext>` (jpg/png/webp) — read
    straight off disk.
  - already-remote images the digest embeds directly: `https://…` (e.g. thenextweb `.avif`)
    — downloaded here. Non-(jpeg|png|webp|gif) bytes (notably AVIF) are transcoded to JPEG
    with Pillow, because beehiiv's email renderer can't be relied on to handle AVIF. A
    manual paste would hand the editor decoded PNG/JPEG pixels anyway, so this matches the
    "exactly as if I pasted it" intent.

The map is keyed by the literal `src` (not the basename) so the page-side builder can map
each digest `<img>` straight to its uploaded asset. Pairs with:
  - scripts/batch_image_uploader.js — decodes each entry and POSTs it to the editor's
    **asset** endpoint (`POST /publications/<pub>/assets`, field `asset[file]`), the same
    call the editor itself makes on a paste, stashing `window.__assets[src] = {id, src}`.
  - scripts/build_doc.js — builds a TipTap doc with real `imageBlock` nodes from that map.

Usage:
  python3 build_image_clipboard.py [DIGEST_HTML] [OUT_JSON] [LIMIT]
Defaults match the beehiiv skill's fixed inputs. LIMIT (optional) caps the number of
images (smoke test); omit for the full set.

Prints a one-line JSON summary {count, local, remote, bytes, names} to stdout — never the
base64 (keep it out of the model context).
"""
import sys, os, re, io, json, base64, subprocess, urllib.request, urllib.parse

REPO   = '/Users/drucev/projects/newsagent'
DIGEST = sys.argv[1] if len(sys.argv) > 1 else os.path.join(REPO, 'out/latest-bsky.html')
OUT    = sys.argv[2] if len(sys.argv) > 2 else '/tmp/bh_imgs.json'
LIMIT  = int(sys.argv[3]) if len(sys.argv) > 3 else None

_RASTER = {'jpg': 'image/jpeg', 'jpeg': 'image/jpeg', 'png': 'image/png',
           'webp': 'image/webp', 'gif': 'image/gif'}


def _ext(name):
    return (name.rsplit('.', 1)[-1] if '.' in name else '').lower()


def _get(url):
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    return urllib.request.urlopen(req, timeout=30).read()


def _fetch_remote(url):
    """Download a remote image; transcode anything that isn't a web-safe raster
    (e.g. AVIF) to JPEG. Returns (filename, bytes).

    Proxy/wrapper URLs (e.g. cardyb.bsky.app?url=<double-encoded FT url>) sometimes
    400 because the wrapped URL is over-encoded. On failure, fully unquote the URL
    and retry each embedded https URL innermost-first (with and without its query
    string) before giving up."""
    try:
        data = _get(url); fetched = url
    except Exception:
        full = fetched = url
        for _ in range(4):
            nxt = urllib.parse.unquote(full)
            if nxt == full:
                break
            full = nxt
        starts = [m.start() for m in re.finditer(r'https?://', full)]
        data = None
        for i in reversed(starts[1:]):  # innermost embedded URL first
            for cand in (full[i:], full[i:].split('?')[0]):
                try:
                    data = _get(cand); fetched = cand
                    break
                except Exception:
                    continue
            if data is not None:
                break
        if data is None:
            raise
    base = os.path.basename(urllib.parse.urlparse(fetched).path) or 'image'
    if _ext(base) in _RASTER:
        return base, data
    # unknown / AVIF / etc → decode + re-encode JPEG (matches a real paste's pixels)
    from PIL import Image
    im = Image.open(io.BytesIO(data)); im.load()
    buf = io.BytesIO()
    im.convert('RGB').save(buf, 'JPEG', quality=85)
    stem = base.rsplit('.', 1)[0] if '.' in base else base
    return stem + '.jpg', buf.getvalue()


def _read_local(src):
    """Resolve a file:// src to a path and read it. Returns (filename, bytes)."""
    path = urllib.parse.unquote(urllib.parse.urlparse(src).path)
    with open(path, 'rb') as f:
        return os.path.basename(path), f.read()


# `out/latest-bsky.html` is a symlink to the dated digest — follow it.
html = open(os.path.realpath(DIGEST), encoding='utf-8').read()

# every <img src='...'> / src="..." in document order, de-duped (first wins).
srcs, seen = [], set()
for m in re.finditer(r'<img\b[^>]*?\bsrc=([\'"])(.*?)\1', html, re.I | re.S):
    s = m.group(2)
    if s not in seen:
        seen.add(s); srcs.append(s)
if LIMIT:
    srcs = srcs[:LIMIT]

out, names, failed, n_local, n_remote = {}, [], [], 0, 0
for s in srcs:
    try:
        if s.startswith('file:'):
            fn, raw = _read_local(s); n_local += 1
        elif s.startswith('http:') or s.startswith('https:'):
            fn, raw = _fetch_remote(s); n_remote += 1
        else:
            continue  # data: or unknown scheme — skip
    except Exception as e:
        failed.append(s)
        print(f'WARN: skipping unfetchable image {s[:120]} ({e})', file=sys.stderr)
        continue
    out[s] = {'n': fn, 'b': base64.b64encode(raw).decode('ascii')}
    names.append(fn)

payload = json.dumps(out)  # dict preserves insertion order; JSON.parse keeps it
open(OUT, 'w').write(payload)
subprocess.run(['pbcopy'], input=payload.encode('utf-8'), check=True)

print(json.dumps({'count': len(out), 'local': n_local, 'remote': n_remote,
                  'bytes': len(payload), 'failed': failed, 'names': names}))
