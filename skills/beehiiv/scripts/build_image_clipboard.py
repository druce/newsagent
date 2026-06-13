#!/usr/bin/env python3
"""build_image_clipboard.py — stage ALL of the bsky digest's images on the OS
clipboard as a single base64 JSON map, so the whole set uploads in ONE paste (no
per-image loop, no model-token cost — the bytes ride the clipboard as text).

Reads `out/latest-bsky.html`, pulls the referenced `download/bsky-images/<hash>.<ext>`
basenames IN DOCUMENT ORDER (de-duped, first occurrence wins), base64-encodes each
file, writes {basename: b64} to a JSON file, and copies that JSON onto the clipboard
via pbcopy.

The bsky digest references images as absolute file:// URLs with hash filenames and
mixed extensions, e.g.
  <img src='file:///Users/drucev/projects/newsagent/download/bsky-images/50159b4dd7985e0d.jpg' alt='post image'>
so we match on the `bsky-images/<basename>` portion regardless of the file:// prefix.

Pairs with scripts/batch_image_uploader.js (the page-side decoder/uploader); the
uploader derives the upload MIME type from each basename's extension.

Usage:
  python3 build_image_clipboard.py [DIGEST_HTML] [IMAGES_DIR] [OUT_JSON] [LIMIT]
Defaults match the beehiiv skill's fixed inputs. LIMIT (optional) caps the number
of images (used for a smoke test); omit for the full set.

Prints a one-line JSON summary {count, names, bytes} to stdout — does NOT print
the base64 (keep it out of the model context).
"""
import sys, os, re, json, base64, subprocess

REPO   = '/Users/drucev/projects/newsagent'
DIGEST = sys.argv[1] if len(sys.argv) > 1 else os.path.join(REPO, 'out/latest-bsky.html')
IMGDIR = sys.argv[2] if len(sys.argv) > 2 else os.path.join(REPO, 'download/bsky-images')
OUT    = sys.argv[3] if len(sys.argv) > 3 else '/tmp/bh_imgs.json'
LIMIT  = int(sys.argv[4]) if len(sys.argv) > 4 else None

# `out/latest-bsky.html` is a symlink to the dated digest — follow it.
html = open(os.path.realpath(DIGEST), encoding='utf-8').read()
# referenced basenames in document order, de-duped keeping first occurrence.
# hashes are hex; extensions vary (jpg/jpeg/png/webp).
seen, order = set(), []
for n in re.findall(r'bsky-images/([A-Za-z0-9]+\.(?:jpe?g|png|webp))', html):
    if n not in seen:
        seen.add(n); order.append(n)
if LIMIT:
    order = order[:LIMIT]

m = {}
for n in order:
    with open(os.path.join(IMGDIR, n), 'rb') as f:
        m[n] = base64.b64encode(f.read()).decode('ascii')

payload = json.dumps(m)  # dict preserves insertion order; JSON.parse keeps it
open(OUT, 'w').write(payload)
subprocess.run(['pbcopy'], input=payload.encode('utf-8'), check=True)

print(json.dumps({'count': len(order), 'names': order, 'bytes': len(payload)}))
