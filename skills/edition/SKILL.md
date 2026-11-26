---
name: edition
description: Draft today's newsletter issue in house style from the stories curated in the curate page (published_articles rows with selected=1, sessions since noon yesterday). One Opus subagent first merges the sessions' overlapping categories into ~15–25 clusters and flags likely duplicate stories, then one Opus subagent per cluster writes the items (kicker + one-sentence summary + source label + N alternative kickers, default 3), then one Opus subagent does the final edit (issue order, copy edit against docs/style/, kicker de-duplication, a funny title + alts per cluster, subtitle), and a candidate og:image is fetched for every item. Writes out/edition-<stamp>.md, then imports it into a beehiiv draft via newsagent:finalize edition, where the editor edits it. Use after curating, when the user wants "the edition", "today's draft", or a house-style draft from their picks.
---

# newsagent:edition

Turns the curated picks into an issue draft. The docs that define the style live in
`docs/style/`:
- `style-guide.md`: voice, stance, joke targets, the be-funny rule
- `gold-headlines.md`: kicker and subtitle calibration, the repeat list
- `house-style.md`: issue anatomy, item format, mechanics, selection, the pre-send checklist

Edit those docs to change the output; the prompts are `lib/prompts/merge_edition_cats.py`,
`lib/prompts/write_edition_section.py` and `lib/prompts/edit_edition.py`.

**Arguments:** `--kickers N` sets the number of alternative kickers under each item (default 3).
`--since SID` overrides the window. Pass both through to `--prepare`.

Driver rules: use only the `.venv/bin/python -m lib.steps.edition ...` commands below,
plus the Agent tool. Take batch paths from prepare's stdout; batches are zero-indexed
(`batch-000`). Never run any step in-process with `--engine`. Don't use ScheduleWakeup.

## Step 1: prepare the category merge

The window spans two or more pipeline runs, and each run names its own categories, so
the same topic arrives under several names. This step merges them before any writing.

```bash
.venv/bin/python -m lib.steps.edition --prepare [--kickers N] [--since SID]
```

Prints `Run: <RUN>` and the merge batch path. If it reports no selected articles, stop and
tell the user to curate first (`.venv/bin/python -m lib.steps.curate`).

Dispatch ONE agent: `subagent_type: "general-purpose"`, `model: "opus"`,
`description: "Edition merge"`, with the agent prompt below, using
`edition-merge-batches/batch-000.md` → `edition-merge-results/batch-000.json`.

## Step 2: apply the merge (writes the section batches)

```bash
.venv/bin/python -m lib.steps.edition --apply-merge <RUN>
```

Prints `N categories → M sections`, then one line per section with its batch-NNN.json
path and prompt `.md`, then any `duplicate?` groups and notes. Tell the user the section
count and every duplicate group (they are only flagged; the draft keeps both stories,
marked `- Possible duplicate: …`). If it fails validation, re-dispatch the merge
agent and run apply again.

Fallback: `--prepare-sections` skips the merge and writes one section batch per session
category directly (the old behavior); continue at Step 3.

## Step 3: dispatch ALL section agents in ONE message

For each section batch, one Agent:
- `subagent_type: "general-purpose"`, `model: "opus"`, `description: "Edition section NNN"`
- prompt (substitute RUN, STEP and NNN; STEP is `edition-sections` here,
  `edition-merge` in Step 1, `edition-edit` in Step 5):

  ```
  Read runs/<RUN>/<STEP>-batches/batch-NNN.md. It is a complete prompt:
  the SYSTEM part is your role and the USER part is your task. Read the whole file;
  if Read says it is too large, read it in consecutive chunks with offset/limit
  until you have seen every line. Do the task by reasoning in your own context.

  Write ONLY the JSON object the prompt asks for to
  runs/<RUN>/<STEP>-results/batch-NNN.json using the Write tool.

  Then run:
    .venv/bin/python tools/check_batch.py runs/<RUN>/<STEP>-results/batch-NNN.json
  If it prints FAIL, fix exactly what it lists and Write again. When it prints OK,
  reply with the path.

  Use ONLY Read (the one batch .md file), Write (the one result file) and that
  check_batch command. No other files, no other shell commands, no subprocesses
  or HTTP calls.
  ```

## Step 4: apply sections (writes the final-edit batch)

```bash
.venv/bin/python -m lib.steps.edition --apply-sections <RUN>
```

If it lists batches that are not ready, re-dispatch only those agents and run apply again.
If a batch died on a safety cutoff (a `[cyber]` flag, or a biosecurity cutoff), re-dispatch
it on Opus with "policy-level wording only, no technical detail" added.

## Step 5: dispatch ONE final-edit agent

Same Agent config (`description: "Edition final edit"`), and the same prompt with
`edition-edit-batches/batch-000.md` → `edition-edit-results/batch-000.json`. This
prompt is about 85KB, so the chunked-Read line matters.

## Step 6: render

```bash
.venv/bin/python -m lib.steps.edition --apply-edit <RUN>
```

This writes `out/edition-<stamp>.md` and the `out/latest-edition.md` symlink. URLs come from the
database by id, with tracking params stripped (house-style §3); the model never writes links.
It also fetches a candidate image for every item (og:image, CardyB fallback, resized to 600px
wide, cached in `download/edition-images/`), drawn as `![](../download/edition-images/…)` above
the item. Missing ones are `- No image: reason`. This takes about 30 seconds; add
`--no-images` to re-render without it. Each cluster opens with `## <title>` and
`- Alt titles: …`. Report to the user:
- the path
- the subtitle
- the `Images: N/M` line
- the cluster, item and flag counts
- every `note:` and `warning:` line

Editor notes are plain bullet lines, `- Label: text`: `Alt subtitles`, `Stories` (item count) and `Note` under the
subtitle, `Alt titles` under each heading, and `No image`, `Alt kickers`, `Flag`,
`Possible duplicate` with each item. They go into the beehiiv draft as bulleted lists, so
the editor reads them there and deletes them before sending.

## Step 7: make the beehiiv draft (automatic)

Right after Step 6, without asking, invoke `newsagent:finalize` with args `edition` and
follow it end to end: Chrome pre-flight, then newsagent:beehiiv's edition-markdown source
against `out/latest-edition.md`. It creates a **draft only** and never publishes. Editing
now happens in beehiiv, so import the md exactly as rendered.

If pre-flight fails, report the Step 6 results plus the failed check and its fix, and stop:
the md is complete, and `/newsagent:finalize edition` imports it later. Add to the report:
- the beehiiv `draft_url`, title and `email_subject_line` readback
- the subtitle/preview readback
- the persisted `editorCounts()` against the converter's counts
