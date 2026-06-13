---
name: rewrite
description: Assemble section drafts into a whole newsletter, run a whole-newsletter critic-optimizer pass, generate the title, and store state.final_newsletter. Iterates up to --max-edits times with early exit at score >= 8.0.
---

# newsagent:rewrite

Step 11 of /newsagent:pipeline. Two execution paths.

## Interactive mode — orchestrator-driven critic loop

**Parent Claude drives the loop**, dispatching a SEPARATE Sonnet Agent for each
critique / improve / title pass. Each dispatch is a fresh context, so the
critic genuinely re-reads the draft cold and its score is independent of the
improver — real call separation, not one subagent simulating the loop and
back-filling its own scores. Parent Claude owns the loop state (`scores[]`,
`iterations`, `accepted`, and which draft is "current"); the large text (drafts,
critique feedback) threads through **work files** so the parent only carries
small scalars and file paths.

### Step 1: prepare batch

```bash
python -m lib.steps.rewrite --session SID --prepare-batches [--max-edits 2]
```

Writes a single `runs/<SID>/rewrite-batches/batch-000.json` with the initial
draft (concatenated section markdowns) and three pre-rendered prompt pairs
(critique / improve / title `_system_prompt` + `_user_prompt`) plus `max_edits`,
`accept_threshold` (8.0), and the `RewriteResult` schema.

### Step 2: run the loop (parent Claude dispatches separate Agents)

Work files written under `runs/<SID>/`:
- `rewrite-work/draft-init.md` — the initial body (written by `--prepare-batches`)
- `rewrite-work/critique-<i>.md` — full critique feedback (one per critique pass)
- `rewrite-work/draft-<i>.md` — improved newsletter body after improve pass `i`
- `rewrite-work/title.txt` — the generated title (written by the TITLE Agent)

The **current draft source** is always a file: `runs/<SID>/rewrite-work/draft-init.md`
on the first pass, then `runs/<SID>/rewrite-work/draft-<i>.md` after improve pass `i`.

There is no LLM "assemble" step — the final result is packaged mechanically by
`--finalize` (Step 3), which reads these files directly. So only **three** worker
Agents are dispatched (critique, improve, title).

Parent loop (narrate each stage as you go — "dispatching critique agent
(iteration i)…", "critique i → score X.X, accept=…", "dispatching improve
agent…", "improve i → wrote draft-i.md", "dispatching title agent…", "title →
'…'", "finalizing"):

```
scores = []; critiquePaths = []; iterations = 0; accepted = false
src = runs/<SID>/rewrite-work/draft-init.md
for i in 0 .. max_edits-1:
    iterations += 1
    dispatch CRITIQUE Agent (writes rewrite-work/critique-i.md) → read score, accept
    scores.append(score); critiquePaths.append("runs/<SID>/rewrite-work/critique-i.md")
    if accept OR score >= 8.0: accepted = true; break
    dispatch IMPROVE Agent (writes rewrite-work/draft-i.md)
    src = runs/<SID>/rewrite-work/draft-i.md
dispatch TITLE Agent (writes rewrite-work/title.txt) → read title
# then Step 3: finalize (a plain CLI call, no Agent)
```

The three worker Agents use `subagent_type: "general-purpose"`, `model: "sonnet"`,
Read + Write only, and must reason in their own context — **no bash/subprocess/
LLM-call tool** for the reasoning. Prompt skeletons (fill in `<SID>`, `<i>`, and
the current draft source path):

**CRITIQUE Agent** — `description: "Critique newsletter iter <i>"`
  ```
  Working directory: <repo root>
  You are the CRITIC. Read runs/<SID>/rewrite-batches/batch-000.json and use ONLY
  its critique_system_prompt (your role/rubric) and critique_user_prompt (task).
  The CURRENT newsletter draft is the markdown in file <draft source>. Read it.
  Note: the newsletter title / H1 is produced in a SEPARATE step, so the body is
  titleless by design (it starts with a "## " section). Do NOT flag a missing
  title/H1 or penalize the score for it.
  Substitute {newsletter_markdown}=the current draft into critique_user_prompt and
  critique it as an adversarial editor seeing it for the first time. Score what is
  actually on the page (0-10), not what you intend to fix. Be genuinely critical.
  Write your FULL critique feedback (specific, actionable issues) to:
    runs/<SID>/rewrite-work/critique-<i>.md
  Report your score (float 0-10) and accept (bool: true iff ready to publish as-is).
  Do the reasoning YOURSELF — no bash, no subprocess, no LLM-call tool. Use ONLY
  Read and Write.
  ```

**IMPROVE Agent** — `description: "Improve newsletter iter <i>"`
  ```
  Working directory: <repo root>
  You are the IMPROVER. Read runs/<SID>/rewrite-batches/batch-000.json and use ONLY
  its improve_system_prompt (your role) and improve_user_prompt (task).
  The CURRENT newsletter draft is the markdown in file <draft source>. Read it.
  Read the critique feedback in runs/<SID>/rewrite-work/critique-<i>.md.
  Substitute {newsletter_markdown}=the current draft and {critique}=that feedback
  into improve_user_prompt and produce a NEW complete newsletter addressing every
  appropriate issue. Output the newsletter BODY only — keep "## " section headings
  but do NOT include a leading "# " H1 title line (the title is generated separately
  and prepended at finalize time). If the critique asks for an H1/title, IGNORE that
  one point — it is handled separately. Write the improved body to:
    runs/<SID>/rewrite-work/draft-<i>.md
  Report the path you wrote. Reason YOURSELF — no bash/subprocess/LLM-call tool;
  Read and Write only.
  ```

**TITLE Agent** — `description: "Generate newsletter title"`
  ```
  Working directory: <repo root>
  You are the TITLE EDITOR. Read runs/<SID>/rewrite-batches/batch-000.json and use
  ONLY its title_system_prompt (your role) and title_user_prompt (task).
  The FINAL newsletter draft is the markdown in file <final draft source>. Read it.
  Substitute {newsletter_markdown}=the final draft into title_user_prompt and
  produce the title. Write the bare title (one line, no leading "# ") to:
    runs/<SID>/rewrite-work/title.txt
  Report that same title. Reason YOURSELF — no bash/subprocess/LLM-call tool;
  Read and Write only.
  ```

### Step 3: finalize (mechanical — no Agent, no LLM)

Once the loop and title are done, package + apply in one CLI call. It reads the
final body, the critique feedback files, and the title file directly, builds the
`RewriteResult`, sets state, renders `out/<date>.html`, and emails (unless
`--no-email`). `<body>` is the final draft source; `<csv-of-critique-paths>` is
`critiquePaths` in order; `<csv-of-scores>` is `scores` in order:

```bash
python -m lib.steps.rewrite --session SID --finalize \
  --body runs/<SID>/rewrite-work/draft-<last>.md \
  --title-file runs/<SID>/rewrite-work/title.txt \
  --scores <csv-of-scores> \
  --feedbacks <csv-of-critique-paths> \
  --accepted|--not-accepted [--no-email]
```

(If the loop accepted on iteration 0 with no improve pass, `--body` is
`runs/<SID>/rewrite-work/draft-init.md`.)

### Step 4: retry on failure

Each pass is its own file, so retries are surgical: re-dispatch only the failing
worker Agent (overwriting its work file) and continue. If `--finalize` errors
(e.g. a scores/feedbacks count mismatch), fix the offending file or argument and
re-run the single `--finalize` command — no Agent re-dispatch needed.

## Classic mode (non-interactive)

```bash
python -m lib.steps.rewrite --session SID --engine openai:gpt-4o-mini --max-edits 2
```

In-process critic loop + title call. Do not use `--engine subagent`.

## Output contract

- `state.final_newsletter = "# {title}\n\n{body}"` set.
- `state.newsletter_title` set.
- `runs/<SID>/rewrite.json` written with transcript.
- `rewrite` step marked COMPLETE.
- **Auto-send:** at the end of both `--apply-results` and classic mode, rewrite
  invokes `lib.steps.send.deliver_newsletter(...)` which renders
  `out/<date>.html`, inserts a row in `newsletters`, and emails the result via
  Gmail (defaults to `GMAIL_USER`). Pass `--no-email` to suppress the send,
  `--to ADDR` to override the recipient. The send step (step 12) is idempotent
  and will not re-email when invoked again for the same session.
