---
name: edition
description: Draft today's newsletter issue in house style from the stories curated in the curate page (published_articles rows with selected=1, sessions since noon yesterday). One Opus subagent per category writes the items (kicker + one-sentence summary + source label + N alternative kickers, default 3), then one Opus subagent does the final edit (issue order, copy edit against docs/style/, kicker de-duplication, subtitle). Writes out/edition-<stamp>.md for the editor to edit. Use after curating, when the user wants "the edition", "today's draft", or a house-style draft from their picks.
---

# newsagent:edition

Turns the curated picks into an issue draft. The docs that define the style live in
`docs/style/`:
- `style-guide.md`: voice, stance, joke targets, the be-funny rule
- `gold-headlines.md`: kicker and subtitle calibration, the repeat list
- `house-style.md`: issue anatomy, item format, mechanics, selection, the pre-send checklist

Edit those docs to change the output; the prompts are `lib/prompts/write_edition_section.py`
and `lib/prompts/edit_edition.py`.

**Arguments:** `--kickers N` sets the number of alternative kickers under each item (default 3).
`--since SID` overrides the window. Pass both through to `--prepare-sections`.

Driver rules: use only the `.venv/bin/python -m lib.steps.edition ...` commands below,
plus the Agent tool. Take batch paths from prepare's stdout; batches are zero-indexed
(`batch-000`). Never run any step in-process with `--engine`. Don't use ScheduleWakeup.

## Step 1: prepare section batches

```bash
.venv/bin/python -m lib.steps.edition --prepare-sections [--kickers N] [--since SID]
```

Prints `Run: <RUN>`, then one line per category with its batch-NNN.json path and its
prompt `.md`. If it reports no selected articles, stop and tell the user to curate first
(`.venv/bin/python -m lib.steps.curate`).

## Step 2: dispatch ALL section agents in ONE message

For each batch, one Agent:
- `subagent_type: "general-purpose"`, `model: "opus"`, `description: "Edition section NNN"`
- prompt (substitute RUN and NNN):

  ```
  Read runs/<RUN>/edition-sections-batches/batch-NNN.md. It is a complete prompt:
  the SYSTEM part is your role and the USER part is your task. Read the whole file;
  if Read says it is too large, read it in consecutive chunks with offset/limit
  until you have seen every line. Do the task by reasoning in your own context.

  Write ONLY the JSON object the prompt asks for to
  runs/<RUN>/edition-sections-results/batch-NNN.json using the Write tool.

  Then run:
    .venv/bin/python tools/check_batch.py runs/<RUN>/edition-sections-results/batch-NNN.json
  If it prints FAIL, fix exactly what it lists and Write again. When it prints OK,
  reply with the path.

  Use ONLY Read (the one batch .md file), Write (the one result file) and that
  check_batch command. No other files, no other shell commands, no subprocesses
  or HTTP calls.
  ```

## Step 3: apply sections (writes the final-edit batch)

```bash
.venv/bin/python -m lib.steps.edition --apply-sections <RUN>
```

If it lists batches that are not ready, re-dispatch only those agents and run apply again.
If a batch died on a safety cutoff (a `[cyber]` flag, or a biosecurity cutoff), re-dispatch
it on Opus with "policy-level wording only, no technical detail" added.

## Step 4: dispatch ONE final-edit agent

Same Agent config (`description: "Edition final edit"`), and the same prompt with
`edition-edit-batches/batch-000.md` → `edition-edit-results/batch-000.json`. This
prompt is about 85KB, so the chunked-Read line matters.

## Step 5: render

```bash
.venv/bin/python -m lib.steps.edition --apply-edit <RUN>
```

This writes `out/edition-<stamp>.md` and the `out/latest-edition.md` symlink. URLs come from the
database by id, with tracking params stripped (house-style §3); the model never writes links.
Report to the user:
- the path
- the subtitle
- the cluster, item and flag counts
- every `note:` and `warning:` line

The `<!-- alts: ... -->`, `<!-- flag: ... -->` and `<!-- note: ... -->` comments are
for the editor. They stay visible in an editor and never render.
