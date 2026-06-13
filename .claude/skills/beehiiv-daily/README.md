# beehiiv-daily

A Claude Code skill that turns the newsagent **Bluesky digest** (`out/latest-bsky.html`)
into a beehiiv **draft** post — uploading every image and populating the full body (`<h2>`
section headers, per-post summary lines, `og-link` headlines, `og-desc` descriptions, and
images in document order). Produces a draft only; it never publishes.

It is self-contained: it talks to beehiiv's internal API
(`https://app.beehiiv.com/api/v2`) to create the draft from a template and set its title,
then uploads images and pastes the body. The auth model and endpoint map live in
`references/beehiiv-internal-api.md`.

## Installation & invocation

This is installed as a **project skill** at
`/Users/drucev/projects/newsagent/.claude/skills/beehiiv-daily/`, so any Claude Code session
started inside `/Users/drucev/projects/newsagent` can invoke it as a slash command:

```
/beehiiv-daily
```

(New skills are picked up at session start — if `/beehiiv-daily` doesn't appear, restart
Claude Code in this project. To make it available in *every* project instead of just this
one, copy the folder to `~/.claude/skills/beehiiv-daily/`.)

You can also just describe the task and Claude will load the skill, e.g.:

> Run beehiiv-daily: import `out/latest-bsky.html` (images in `download/bsky-images/`) into a
> new "Daily" draft titled "AI Reading for Saturday June 13". Don't publish.

The digest and images always live at the same fixed paths, so you don't need to specify
them — just give today's title:

- Digest: `/Users/drucev/projects/newsagent/out/latest-bsky.html`
- Images dir: `/Users/drucev/projects/newsagent/download/bsky-images`
- Template: `Daily`

**Daily checklist:** generate the Bluesky digest (`newsagent:bluesky` → writes
`out/latest-bsky.html` + `download/bsky-images/`) → `cd /Users/drucev/projects/newsagent &&
claude --chrome` with a logged-in `app.beehiiv.com` tab → `/beehiiv-daily` (give it today's
title) → don't touch the keyboard/mouse during the image paste → review the returned
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
2. **Upload images:** base64 every referenced image into one `{basename: b64}` JSON map,
   `pbcopy` it, and deliver the whole set with a **single** `Cmd+V` into a hidden catcher;
   the catcher decodes each entry and POSTs it (with the right MIME for jpg/png/webp) to
   `/api/v2/publications/<pub>/images`, collecting the hosted `media.beehiiv.com` URL per
   basename.
3. **Populate the body:** swap the digest's `file://…/bsky-images/…` paths for the hosted
   URLs, open `/posts/<id>/edit`, and feed the rich HTML to the editor via a **synthetic
   paste event** (no OS clipboard). The editor imports sections/links/descriptions/images
   and re-hosts the images onto its own S3, auto-syncing the draft.

The body is authored through the editor on purpose: beehiiv **silently ignores** REST
writes to the post body (see below).

## Files

| File | Purpose |
|------|---------|
| `SKILL.md` | The step-by-step workflow Claude follows. Start here. |
| `references/beehiiv-internal-api.md` | Internal-API auth model (token + publication id + CSRF), status codes, the create-from-template + set-title primitive, and the full endpoint map. |
| `references/beehiiv-image-and-body.md` | Image-upload endpoint, the clipboard/paste transport, the editor-paste body method, the verified TipTap `imageBlock` schema, and the full "approaches that don't work" list. |
| `scripts/beehiiv_browser.js` | Internal-API auth helpers (`getAuth`, `bhGet/Post/Patch/Delete`, `findTemplate`, `setTitle`, `createDraftFromTemplate`). |
| `scripts/build_image_clipboard.py` | Reads `out/latest-bsky.html`, base64s every referenced `bsky-images/` thumbnail into one `{basename: b64}` JSON map, and `pbcopy`s it for a single-paste upload. |
| `scripts/batch_image_uploader.js` | The textarea catcher that JSON-parses the pasted map and uploads every image in one in-page loop (MIME per extension). |
| `scripts/paste_image_uploader.js` | Fallback: contentEditable catcher that uploads pasted images one at a time, in order. |
| `scripts/build_hosted_html.js` | Swaps the digest's `file://…/bsky-images/…` srcs for hosted URLs and dispatches the synthetic body paste. |

## Key gotchas (the ones that cost the most time)

- **No REST body-write.** `PATCH` of `tiptap_state` / `draft_tiptap_state` returns `200`
  but is silently dropped. The body must be pasted into the editor.
- **No curl.** beehiiv is behind Cloudflare; shell `curl` gets a 403 challenge even with a
  valid token. Same-origin browser JS only.
- **Clipboard, not base64.** Image bytes ride the OS clipboard + a native paste event;
  `localhost` fetch is blocked, `clipboard.read()` is permission-gated, and
  base64-through-the-conversation is hugely token-expensive.
- Pre-authorize `Bash(osascript:*)` in `.claude/settings.local.json` to avoid one
  permission prompt per image.
