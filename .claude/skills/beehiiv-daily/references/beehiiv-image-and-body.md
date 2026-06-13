# beehiiv images + body — reference

How to upload images and populate a post body via beehiiv's internal API + editor,
discovered empirically against the live app (May 2026). Pairs with
`references/beehiiv-internal-api.md` (auth model, posts endpoints).

## Image upload endpoint

`POST https://app.beehiiv.com/api/v2/publications/<pub>/images`
(note: `<pub>` is in the **path** here, not the `?publication_id=` query param the
posts endpoints use).

- Multipart body: `file` = the image blob, `url_type` = `"landscape"`.
- Headers: `Authorization: Bearer <token>`, `x-csrf-token: <csrf>`, `accept: application/json`.
  Do **not** set `Content-Type` — let the browser set the multipart boundary.
- Response: `{ "url": "https://media.beehiiv.com/cdn-cgi/image/fit=scale-down,format=auto,onerror=redirect,quality=80/uploads/publication/file/<pub>/landscape_<filename>" }`
- The hosted path uses the **filename** you send, so prefix it (e.g. `20260529-`) to avoid
  collisions across runs.

## Getting image bytes into the page (the transport)

The page can't read local files, and several obvious routes are blocked (see guardrails).
Two clipboard-based routes work; **prefer the batch one**.

### Batch (preferred): all images in ONE paste, as base64 text

The clipboard transport doesn't have to carry *bitmaps* — it can carry **text**. Base64
every referenced image into one `{basename: b64}` JSON map, `pbcopy` it, and deliver the
whole map with a **single** `Cmd+V` into a `<textarea>` catcher that `JSON.parse`s the
pasted text and, per entry, decodes b64→`Blob`→multipart POST to the upload endpoint
(keyed by basename). See `scripts/build_image_clipboard.py` (builder + `pbcopy`) and
`scripts/batch_image_uploader.js` (page-side decoder/uploader). Why this is the default:
- **~3 tool calls for the whole set** instead of ~3 *per image* (the per-image loop
  dominated wall-clock — ~57 calls for a 19-image digest).
- **No index-desync failure mode** — uploads are keyed by filename, so the order in which
  the concurrent POSTs finish is irrelevant (the per-image loop relied on a running paste
  index that a single missed paste would shift).
- **Zero model tokens for the bytes** — the base64 lives on the OS clipboard, never in the
  model context (unlike base64-injecting through `execute_javascript`).
- A single large text paste (tens of KB to a few MB) delivers fine via `clipboardData
  .getData('text/plain')`. Verified with 3-image and full-set runs.

### Per-image (fallback): one bitmap per paste

Kept for when the batch catcher breaks (e.g. a paste-size limit). **OS clipboard → native
paste event**, one image at a time:

1. macOS: `osascript -e 'set the clipboard to (read (POSIX file "<abs>.jpg") as «class JPEG»)'`
   stages the image on the clipboard. (Pre-authorize `Bash(osascript:*)` to avoid a prompt
   per image. Run it with **no `cd` prefix** so the permission rule matches.)
2. A `contentEditable` catcher with a `paste` listener calls `preventDefault()` and reads
   `e.clipboardData.items` → the image `File` (Chrome usually hands over `image/png` even
   for a JPEG on the pasteboard — fine, beehiiv re-encodes; keep the `.jpg` filename).
3. Trigger the paste with the **Chrome extension computer tool `key cmd+v`** — it targets
   the tab via CDP, so it works even if the OS window isn't frontmost, and needs no
   clipboard-read permission. (AppleScript `System Events` keystrokes need macOS
   Accessibility permission and silently no-op without it.)
4. The listener POSTs the blob to the upload endpoint and stashes `{url}`. Because it
   `preventDefault`s, focus stays in the catcher — no re-click between images. Keep an
   ordered basename list + an index so each paste is named correctly; checkpoint the index
   periodically (a missed paste would desync the mapping).

## Populating the body: paste into the editor (REST does NOT work)

`PATCH /posts/<id>` with `tiptap_state` **or** `draft_tiptap_state` (object **or**
JSON-string) returns `200 {timestamp}` but the body is **silently unchanged**. Only
whitelisted fields (`title`, `web_title`, …) persist via PATCH. The body is authored
exclusively through the editor's collaborative (ydoc) sync.

So: assemble the digest as **HTML with hosted image URLs** and hand it to the editor.
The robust transport is a **synthetic paste event** — not the OS clipboard.

1. Replace every `file://…/download/bsky-images/<hash>.<ext>` src in the digest HTML with its
   hosted `media.beehiiv.com` URL from the upload step (regex-replace the **whole** `file://`
   URL, not just the basename, or the `file://…/` prefix is left dangling); apply `\$`→`$`
   text cleanup. Stash it in `localStorage['__bh_hosted_html']` (survives the navigation to
   `/edit`; `window.*` does not).
2. `navigate` to `/posts/<id>/edit`, re-inject `build_hosted_html.js`, and **clear the
   template stub with real keystrokes**: computer-tool click the single `.ProseMirror` body
   (the title is a separate input), then `Cmd+A` (optionally `Backspace`). Real keystrokes
   update ProseMirror's *internal* selection; a programmatic DOM `Range` does not, so the
   paste would land in the wrong place without this.
3. Call `window.bh.pasteHostedHtml()` — it builds a `DataTransfer` with the `text/html`,
   constructs `new ClipboardEvent('paste', {clipboardData})`, and dispatches it at the
   `.ProseMirror` node. TipTap's paste handler consumes it (returns `defaultPrevented:true`)
   and imports the whole body. **No OS clipboard is touched.**
4. The editor parses `<h2>` → section headings, `<a>` → links, `<p>` → paragraphs
   (summary + `og-desc` lines), `<img>` → images — and **re-hosts** the pasted
   `media.beehiiv.com` images onto `beehiiv-images-production.s3.amazonaws.com` (the same host
   real published posts use). The `og-link`/`og-desc` class attributes are dropped on import
   (only the content survives). It auto-syncs ("Synced"); `has_ydoc` becomes `true`. Allow a
   few seconds for re-hosting.

### Why not the OS clipboard (native Cmd+C / Cmd+V)?

The old route rendered the HTML into a `contentEditable`, selected it with a `Range`, did a
native **Cmd+C** (the computer tool — `document.execCommand('copy')` returns false without a
user gesture), then `Cmd+A`+`Cmd+V` into the editor. Two failure modes made it fragile:
- A native **Cmd+C on a programmatic selection often does not overwrite the OS clipboard**,
  so the subsequent `Cmd+V` pastes whatever was copied *earlier* — e.g. the digest base64
  from `build_image_clipboard.py`. Observed: the body filled with base64 text instead of the
  digest.
- A page-level **`Cmd+A` can escape the `contentEditable`** and select the whole document, so
  the copy grabs only the first image. Observed: body pasted one image instead of the full
  digest.

The synthetic `ClipboardEvent` avoids both — it hands TipTap the exact `text/html` with no
clipboard round-trip. The Cmd+C/Cmd+V dance (`stageCopyDiv()` + the green box) is kept as a
documented fallback in `scripts/build_hosted_html.js` only.

`localStorage` is the **only** state that survives navigation — `window.*` (helpers,
`__urls`, the built HTML) is wiped on the page load into `/edit`. `buildHostedHtml` saves the
HTML to `localStorage['__bh_hosted_html']` before you navigate; `pasteHostedHtml` reads it
back on the `/edit` page.

## Verified TipTap `imageBlock` schema (reference)

This is the node shape beehiiv stores (read from a real published post). You do **not**
build this yourself in the working flow — the editor builds it from the pasted `<img>`.
It's kept here for the record and in case REST body-write becomes available later.

```json
{
  "type": "imageBlock",
  "attrs": {
    "id": "<uuid>", "alt": "", "src": "<hosted url>", "url": "", "align": "center",
    "title": "<filename>", "width": "100%", "captionUrl": "", "isUploaded": false,
    "borderStyle": "solid", "captionAlign": "center", "allowExternal": false,
    "borderWidthTop": 0, "borderWidthLeft": 0, "borderWidthRight": 0, "borderWidthBottom": 0,
    "borderTopLeftRadius": 0, "borderTopRightRadius": 0, "borderBottomLeftRadius": 0,
    "borderBottomRightRadius": 0, "useIndividualBorderWidth": false, "useIndividualBorderRadius": false
  },
  "content": [ { "type": "figcaption" } ]
}
```

Link paragraph: `paragraph` with `attrs.id`; content =
`[ {text, marks:[{type:"link", attrs:{rel:"noopener noreferrer nofollow", href, class:null, color:null, target:"_blank"}}]}, {text:"  - "}, {text:source, marks:[{type:"italic", attrs:{}}]} ]`.
Divider: `{ "type":"horizontalRule" }`. Root: `{ "type":"doc", "content":[…] }`.

## Approaches that do NOT work (guardrails)

- **Shell `curl`** → Cloudflare 403 "Just a moment" challenge, even with a valid Bearer
  token. Browser same-origin only.
- **`fetch('http://127.0.0.1:…')` from the https page** → silently hangs (Chrome
  private-network protection). Can't pull local files over a local HTTP server.
- **`navigator.clipboard.readText()/read()`** → rejects "Document is not focused", then
  hangs on a permission prompt you can't grant headlessly. Use a native **paste event**
  instead (no permission needed).
- **`document.execCommand('copy')`** outside a user gesture → returns `false`. Use the
  computer tool's native **Cmd+C** on a selection.
- **AppleScript `System Events` keystroke "v"`** → needs macOS Accessibility permission;
  silently does nothing without it. Use the computer tool's `key cmd+v`.
- **`PATCH tiptap_state` / `draft_tiptap_state`** → 200 but ignored. Body must be pasted
  into the editor.
- **`data:` (base64) image `src` in the body** → rejected by the editor. Only real hosted
  URLs (`media.beehiiv.com`, which the editor re-hosts to S3).
- **Hooking `window.fetch`** to discover endpoints → the SPA captured `fetch` at load, so
  your wrapper won't see its traffic. Use
  `performance.getEntriesByType('resource').map(e=>e.name)` instead.
- **Base64-injecting image bytes through `execute_javascript`** → works but is very
  token-expensive (~250k+ tokens for ~35 thumbnails). The clipboard-paste route avoids it.
