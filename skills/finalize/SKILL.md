---
name: finalize
description: End-to-end daily Bluesky roundup — runs newsagent:bluesky to build the digest, then newsagent:beehiiv to import it into a beehiiv draft via the logged-in Chrome session. Pre-flights Chrome and aborts before doing any work if the extension or the beehiiv login is unavailable. Use when the user wants to "finalize", "do the daily roundup", "build and import today's digest", or run bluesky and beehiiv back to back.
compatibility: Requires the Claude Chrome extension AND a logged-in app.beehiiv.com session — the same hard requirement as newsagent:beehiiv. macOS (pbcopy). There is no headless fallback — if Chrome is unavailable this skill reports failure and stops.
---

# newsagent:finalize

Runs the two daily-roundup skills back to back:

```
Chrome pre-flight → newsagent:bluesky → newsagent:beehiiv → report
```

This skill is a **thin orchestrator**. It deliberately duplicates none of the
two skills' instructions — invoke each one and follow it as written. When
`bluesky` or `beehiiv` changes, this file stays correct.

No arguments. The Bluesky handle is always `druce.ai` (see
`project_bsky_default_handle` in the memory store) and both skills run with
their own defaults.

## Phase 0 — Chrome pre-flight (before ANY work)

This runs **first**, before bluesky, so a browser problem costs nothing: you
abort having produced no artifacts and having left the Bluesky dedup marker
untouched, so a later re-run is clean.

Three checks, in order. Each proves something the beehiiv phase will need:

1. **Extension tools exist.** Load them with one `ToolSearch`:
   `select:mcp__claude-in-chrome__tabs_context_mcp,mcp__claude-in-chrome__navigate,mcp__claude-in-chrome__computer,mcp__claude-in-chrome__javascript_tool,mcp__claude-in-chrome__tabs_create_mcp,mcp__claude-in-chrome__tabs_close_mcp`
   If the `claude-in-chrome` tools are not returned → **fail**.
2. **Extension is live.** `tabs_context_mcp{createIfEmpty:true}` returns a tab.
   Error, or no tab → **fail**.
3. **beehiiv is logged in.** `navigate` that tab to `https://app.beehiiv.com/`,
   then inject `getAuth()` from `skills/beehiiv/scripts/beehiiv_browser.js` and
   confirm it returns a `token` and a `pub`. A throw ("no token in localStorage")
   means logged out → **fail**. Return booleans, not the values — the harness
   redacts results containing the token or publication UUID.

**On failure: report and stop.** Name the check that failed and the fix:

| Failed check | Report to the user |
|---|---|
| 1 — tools absent | The Claude Chrome extension isn't connected to this session. Enable it (and grant `app.beehiiv.com` permission), then re-run `/newsagent:finalize`. |
| 2 — no tab / error | Chrome isn't running or the extension isn't responding. Start Chrome and re-run. |
| 3 — `getAuth()` throws | Chrome is fine but you're logged out of beehiiv. Log in at `app.beehiiv.com`, then re-run. |

Do **not** run the bluesky phase after a pre-flight failure, and do not offer a
digest-only partial run — the point of the up-front check is that the operator
gets a clean re-run.

Passing pre-flight leaves the tab on `app.beehiiv.com`; the beehiiv phase reuses
it (that is its own step 0).

### Chrome is driven ONLY by the Claude Chrome tools

Every page action in both phases — navigation, clicks, typing, key events, JS
injection, tab create/close — goes through `mcp__claude-in-chrome__*`. If Chrome
is unavailable, **report failure — do not route around it.** Do not substitute
Playwright (`mcp__plugin_playwright_playwright__*`, the `.playwright-profile`,
`.playwright-mcp/`), any other headless browser, shell `curl`, or `osascript`
Chrome control. None of them can do this job:

- Playwright has **no logged-in beehiiv session** — no cookie, no
  `localStorage['token']`, no CSRF cookie. Every internal-API call 401s.
- beehiiv is behind **Cloudflare**; `curl` gets a 403 challenge
  (`references/beehiiv-image-and-body.md`).
- The image and digest transports need a **native OS-clipboard paste event** into
  a *visible* tab, which the extension's computer tool provides and Playwright
  does not.

**The one OS-level exception:** no Claude Chrome tool can raise a window or
activate a tab, so when `document.visibilityState` is `hidden` the paste steps
must escalate via `open -a "Google Chrome"` / `osascript` → System Events. That is
`osascript` **moving a window, not driving a page** — see beehiiv gotcha 3 for the
walked list and `skills/beehiiv/CLAUDE.md` for why the working step differs
between host apps (kitty vs. the Claude desktop app hold inverted TCC grants).

This ban is scoped to **driving beehiiv**. It says nothing about the Playwright
that `gather`/`download` use for article scraping — that is unrelated and stays
as-is.

## Phase 1 — build the digest

Invoke `newsagent:bluesky` and follow it end to end (staged mode: fetch → reorder
Agent → apply-reorder → headlines Agent → apply-headlines). Handle `druce.ai`.

**Early exit:** if stage 1 prints `No new posts since last run.`, there is no new
digest. **Report that and stop.** Do not continue to beehiiv — `out/latest-bsky.html`
still holds the previous run's digest, and importing it would create a duplicate
draft of yesterday's content.

If any stage fails, stop and report; do not start phase 2 against a partial digest.

## Phase 2 — import to beehiiv

Invoke `newsagent:beehiiv` and follow it end to end, including its **mandatory
persistence reload**. It creates a **draft only — never publish.**

Its step 0 (confirm a logged-in tab) is already satisfied by pre-flight; reuse
that tab rather than creating another.

## If phase 2 fails: do NOT re-run phase 1

This is the one rule that isn't obvious from the two skills in isolation.

`bluesky` **advances its cross-run dedup marker** (`download/bsky-state/<handle>.txt`)
during phase 1. Re-running it would fetch only posts newer than that marker —
i.e. nothing — and re-render an empty or truncated digest, **destroying the input
phase 2 was working on.**

So when the beehiiv phase fails partway:

1. Fix the browser condition (usually tab visibility — see beehiiv gotcha 3).
2. Re-enter **`/newsagent:beehiiv` alone**, against the existing
   `out/latest-bsky.html`.
3. It resumes the same draft via `localStorage['__bh_draft']` rather than
   creating a second one.

Only re-run bluesky if the user explicitly wants a fresh fetch, and tell them the
marker means they likely need `--no-dedup` to reproduce the same digest.

## Report

On success, report in this order:

- **Digest** — the stamped `out/bsky-YYYY-MM-DD-HHMMSS.html` path, plus post and
  image counts.
- **Draft** — the beehiiv `draft_url`, the title, and the `email_subject_line`
  readback (they must match; a bare `"AI Reading for"` means the subject patch
  didn't take), and `display_thumbnail_on_web` (must be `false`).
- **Verification** — the persisted `editorCounts()` (`imgs`/`hrs`/`links` against
  the digest's `grep -c` targets) from beehiiv's post-reload check.
- **Left as a draft** — state plainly that nothing was published.

## Limitations / Out of scope

- Interactive Claude Code only. No cron/CI path — the beehiiv half cannot run
  headless. For an unattended digest, run `lib.steps.bluesky` classic mode
  directly and import by hand.
- One handle per run, no arguments. To use a different handle or `--no-dedup`,
  run `/newsagent:bluesky` and `/newsagent:beehiiv` separately.
- Does not touch the 14-step newsletter pipeline (`/newsagent:pipeline`) — the
  Bluesky digest is independent of it.
