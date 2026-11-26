---
name: fix
description: Apply a pasted list of copy-edit fixes directly to the current beehiiv draft (the edition imported by newsagent:finalize edition). Each fix is a find → replace on the live editor's model that keeps the replaced text's link/italic/bold marks, so edited link text stays one hyperlink. Also runs consistency passes such as curly → straight quotes. Use when the user pastes edits like `#12 Foo…: "old" → "new"` or `Old: "…" New: "…"` (an ellipsis on either side marks elided text, not literal text). Edits beehiiv only (not the local .md) and never publishes.
compatibility: Requires the Claude Chrome extension and a logged-in app.beehiiv.com session, the same hard requirement as newsagent:beehiiv. No headless fallback.
---

# newsagent:fix

**Argument:** the fix list, pasted as-is. Typical lines:

| Line | Meaning |
|---|---|
| `#N Lead words…: "old" → "new"` | replace `old` with `new` |
| `#N …: Old: "x" New: "y"` (labels on one line or on separate lines, any case) | the same as `"x" → "y"`. `->`, `=>` and `→` are all accepted as the arrow |
| `#N …: "long old text" → "…new tail"` | an ellipsis (`…` or `...`) at the start or end of either side marks elided text the user didn't retype. It is **not** literal. See "Ellipses" below |
| `#N …: "old"  means X` | rewrite `old` so it says X. You choose the wording and show it in the report. |
| `#N …: "x" → "y" (reason)` | the reason is for you; apply `x` → `y` |
| `#N kicker: "A: B:" → "A (B):"` | kickers are part of the link text, so they're edited the same way |
| `Curly apostrophes and quotes → straight in: …` | a consistency pass over the whole draft (`fx.straighten()`) |
| `#N: "- The Guardian" → "- the Guardian"` | a source label: italic and outside the link. Edit just the name (`The Guardian` → `the Guardian`), not the ` - ` |

`#N` item numbers and "Line N aside" notes are the user's way of pointing at an item. They are
not document positions and won't line up with any index. Find each fix by its quoted text.
Use the lead words (`Nvidia says its board…`) as `near` only when the text appears more than once.

**The beehiiv draft is the source of truth.** The user has usually edited it by hand since import
(deleted notes, rewritten kickers), so it no longer matches `out/latest-edition.md`. Read the
live text. Never re-import, never touch the local .md, never publish or schedule.

## Rules

- Drive Chrome only through `mcp__claude-in-chrome__*`, as in newsagent:beehiiv.
- Edit by the editor **model**, through `skills/beehiiv/scripts/fix_text.js` (`window.fx`). Don't
  click, type or paste into the editor: those can leave new words outside the link mark.
  `fx.apply` replaces `[from,to)` with a text node carrying the old range's marks, so edited
  link text stays linked.
- A replacement's old text must match **exactly once** and carry the **same marks throughout**.
  If a fix crosses a mark boundary (say, linked headline into the plain ` - ` before the
  source), split it into separate fixes, one per side. Never merge or retype across the
  boundary.
- `fx.apply` is all-or-nothing. If any edit fails its check, it changes nothing and returns
  the failures.
- Results must be small JSON. The harness redacts anything that looks like a token or the
  publication UUID.

## Steps

### 0. Open the draft

`tabs_context_mcp{createIfEmpty:true}`, then `navigate` to the draft's `/edit` URL. Use the one
the user gives; otherwise, from any `app.beehiiv.com` page, run
`location.href = '/posts/' + localStorage.getItem('__bh_draft') + '/edit'`.
The editor takes several seconds to mount, so poll until
`document.querySelector('.ProseMirror').editor` exists. If there's no tab group, or no
`.editor` after about 20s, reload once. If it's still missing, stop and report.

### 1. Inject the helper

Paste the whole of `skills/beehiiv/scripts/fix_text.js` into `javascript_tool`. It returns
`{fx:'ready', editor:true}`. Every navigation or reload wipes `window.fx`, so inject it again
after each one.

### 2. Turn the list into edits

Build an `edits` array of `[old, new]` or `[old, new, near]`:

- Use the shortest `old` that is unique, while keeping enough context for a reader to see what
  changed. For `means X` fixes, write the new wording yourself.
- If the user's quoted text isn't in the draft, run `fx.show('<a distinctive word>', 60)` to
  see what the draft says now (the user may have changed it), and adapt. If the intent is
  unclear, ask.
- Leave the quote conversion out of `edits`: step 4 does it.
- **Ellipses.** A leading or trailing `…`/`...` on either side of a fix is shorthand, never
  text to insert. Rebuild the full new string from the old one before you build the edit:
  - A leading ellipsis on the **new** side means the user retyped only the end. Line it up
    with the end of `old` and keep the unretyped start of `old` as is. For example,
    `"his own financial and political interest over national security and rule of law"` →
    `"…interests over national security and the rule of law"` becomes the edit
    `["political interest over national security and rule of law",
    "political interests over national security and the rule of law"]`.
  - A trailing ellipsis on the new side works the same way, from the start of `old`.
  - An ellipsis on the **old** side means the quote is cut short: search only the visible
    part.
  - Only an ellipsis in the middle of a string, or one that is clearly part of the
    sentence, is literal. If it's unclear where the retyped part lines up, run `fx.show`
    and ask. Never put a `…` into the draft that the user didn't mean.
  - Show the rebuilt full new text in the report, so the user can check how you lined it up.

### 3. Dry run, then apply

`JSON.stringify(fx.plan(edits))` returns one row per edit. Every row needs `ok:true`. For the
others:

- `n:0, already:true`: the new text is already there (applied earlier). Drop the edit.
- `n:0`: not found. Use `fx.show` to find the current wording.
- `n>1`: add a `near` from the same item, or lengthen `old`.
- `uniform:false`: the edit crosses a mark boundary. Split it.

Once every row is `ok:true`, run `JSON.stringify(fx.apply(edits))`. Expect `applied` to equal the
number of edits, with each result's `marks` showing what it kept (`link`, `italic`, …).

### 4. Consistency passes

Run these only if the list asks for them.

- **Quotes:** `JSON.stringify(fx.straighten())` returns `{converted, where, left}`, where `left`
  should be 0. It converts every curly character in the draft. Compare `where` with the
  user's list and report any extra ones (such as an apostrophe the user didn't list).
- **Other global patterns** (for example `The Guardian` → `the Guardian` in every source
  label): use `fx.count('…')` to see how many there are, then one `[old, new, near]` edit per
  occurrence.

### 5. Check that it saved

Wait about 6s for "Synced", reload `/edit`, wait for the editor, and inject `fix_text.js`
again. Then:

- `fx.verify([<each new string>])`. Each must be `found:true`. Strings that replaced link
  text must have `inLink:true` and `links:1` (the headline isn't split), and `tail` should
  be only ` - Source`.
- `fx.count(...)` for anything a pass was meant to remove; the target is 0. Use
  `/[‘’“”]/` for curly quotes.

If something was lost (a reload shows the old text), run steps 1, 3 and 5 again for just those
edits.

### 6. Report

- A table with one row per fix: `#N` · old → new.
- Any wording you chose for a `means X` fix, so the user can approve it.
- Any extra changes from a consistency pass, beyond the user's list.
- Fixes that were skipped or already applied, and why.
- The result of the reload check: everything found, links whole, nothing left over.
- A statement that the draft is still unpublished.
