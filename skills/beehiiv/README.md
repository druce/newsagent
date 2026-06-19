# newsagent:beehiiv

A Claude Code skill that turns the newsagent **Bluesky digest** (`out/latest-bsky.html`)
into a beehiiv **draft** post — uploading every image and populating the full body (`<h2>`
section headers, per-post summary lines, `og-link` headlines, `og-desc` descriptions, and
images in document order). Produces a draft only; it never publishes.

It is self-contained: it talks to beehiiv's internal API
(`https://app.beehiiv.com/api/v2`) to create the draft from a template and set its title,
then uploads images and pastes the body. The auth model and endpoint map live in
`references/beehiiv-internal-api.md`.

## Installation & invocation

This ships as part of the **newsagent plugin** at `skills/beehiiv/`, so any Claude Code
session in this repo can invoke it as a namespaced slash command:

```
/newsagent:beehiiv
```

(New skills are picked up at session start — if `/newsagent:beehiiv` doesn't appear, restart
Claude Code in this project.)

You can also just describe the task and Claude will load the skill, e.g.:

> Run /newsagent:beehiiv: import `out/latest-bsky.html` (images in `download/bsky-images/`)
> into a new "Daily" draft titled "AI Reading for Saturday June 13". Don't publish.

The digest and images always live at the same fixed paths, so you don't need to specify
them — just give today's title:

- Digest: `/Users/drucev/projects/newsagent/out/latest-bsky.html`
- Images dir: `/Users/drucev/projects/newsagent/download/bsky-images`
- Template: `Daily`

**Daily checklist:** generate the Bluesky digest (`newsagent:bluesky` → writes
`out/latest-bsky.html` + `download/bsky-images/`) → `cd /Users/drucev/projects/newsagent &&
claude --chrome` with a logged-in `app.beehiiv.com` tab → `/newsagent:beehiiv` (give it
today's title) → don't touch the keyboard/mouse during the image paste → review the returned
`draft_url` and publish from beehiiv yourself.

## What it produces

From the bsky digest (an `<h1>` title, `<h2>` section headers, and repeating
`<div class='post'>` blocks — each a summary `<p>`, an optional `<img>` thumbnail, an
`<a class='og-link'>` headline, and an optional `<p class='og-desc'>` blurb), it builds a
beehiiv draft whose body shows each section heading, every headline as a working link with
its summary/description, and each thumbnail rendered — in the original document order, with
no manual image placement.

## Prerequisites

- **Claude Chrome extension** connected (run `claude --chrome`), logged in at
  `app.beehiiv.com`. Playwright is **not** a substitute — the flow needs same-origin JS in
  the real logged-in tab plus native Cmd+C/Cmd+V via the extension's computer tool.
- **macOS** — image bytes are staged on the clipboard with `osascript … as «class JPEG»`.
- A beehiiv post **template** to clone (default `"Daily"`).

## How it works (short version)

1. Create a draft from the template via the internal API, set the dated title.
2. **Upload images:** base64 every referenced image (local thumbnails + any remote ones,
   AVIF→JPEG) into one `{src: {n, b}}` JSON map, `pbcopy` it, and deliver the whole set with a
   **single** `Cmd+V` into a hidden catcher; the catcher decodes each entry and POSTs it to the
   post **asset** endpoint `/api/v2/publications/<pub>/assets` (field `asset[file]`) — the same
   call the editor makes on a paste — collecting `{id, s3 url}` per `src`.
3. **Populate the body:** build a TipTap doc of real beehiiv nodes (`imageBlock` from the
   uploaded assets, `paragraph` with link/italic marks, `horizontalRule`), open
   `/posts/<id>/edit`, and insert it in **small chunks** (a single `setContent` of ~50 images
   crashes the editor's renderer). Strip seam blanks, verify by the editor model, then reload
   to confirm it persisted (and thus renders in web + email).

The body is authored through the editor on purpose: beehiiv **silently ignores** REST writes
to the post body, and only first-class `imageBlock` nodes (not bare `<img>`) survive to the
published page and the email.

## Files

| File | Purpose |
|------|---------|
| `SKILL.md` | The step-by-step workflow Claude follows. Start here. |
| `references/beehiiv-internal-api.md` | Internal-API auth model (token + publication id + CSRF), status codes, the create-from-template + set-title primitive, and the full endpoint map (incl. the asset upload). |
| `references/beehiiv-image-and-body.md` | The asset upload endpoint, the clipboard transport, the imageBlock + chunked-insert body method, the verified TipTap `imageBlock` schema, and the full "approaches that don't work" list. |
| `scripts/beehiiv_browser.js` | Internal-API auth helpers (`getAuth`, `bhGet/Post/Patch/Delete`, `findTemplate`, `setTitle`, `createDraftFromTemplate`). |
| `scripts/build_image_clipboard.py` | Reads `out/latest-bsky.html`, collects every `<img src>` (local + remote, AVIF→JPEG), base64s them into one `{src:{n,b}}` JSON map, and `pbcopy`s it for a single-paste upload. |
| `scripts/batch_image_uploader.js` | The textarea catcher that JSON-parses the pasted map and uploads every image to `/assets` in one in-page loop, keyed by `src`. |
| `scripts/build_doc.js` | Builds a TipTap doc of real `imageBlock`/paragraph/`hr` nodes from the digest + asset map, and inserts it into the live editor in React-safe chunks (`buildDoc`, `applyChunk`, `stripEmptyParagraphs`, `editorCounts`). |

## Key gotchas (the ones that cost the most time)

- **No REST body-write.** `PATCH` of `tiptap_state` / `draft_tiptap_state` returns `200`
  but is silently dropped. The body must be written through the live editor.
- **Images must be `imageBlock` nodes uploaded to `/assets`.** Bare `<img>` tags (and the
  `/images` publication-media endpoint) render in the editor but are **silently dropped from
  the published page and the email**. This is the bug the asset+imageBlock flow fixes.
- **Never `setContent` the whole doc.** ~50 imageBlock node-views in one transaction throw
  React #185 and the body never persists. Insert in small chunks, one per tool call, then
  reload to confirm it persisted.
- **No curl.** beehiiv is behind Cloudflare; shell `curl` gets a 403 challenge even with a
  valid token. Same-origin browser JS only.
- **Clipboard, not base64.** Image bytes ride the OS clipboard (`pbcopy`) + a native paste
  event; base64-through-the-conversation is hugely token-expensive.
