export const meta = {
  name: 'daily-roundup',
  description: 'Generate the daily AI newsletter end-to-end (12-step newsagent pipeline) via subagents — no claude -p',
  whenToUse: 'Run the daily "Skynet And Chill" AI roundup. Pass args.session (a date-ish SID) and args.email ("on"|"off").',
  phases: [
    { title: 'start' }, { title: 'gather' }, { title: 'filter', model: 'haiku' },
    { title: 'download' }, { title: 'dedupe' }, { title: 'summarize', model: 'sonnet' },
    { title: 'rate' }, { title: 'cluster', model: 'haiku' }, { title: 'select', model: 'haiku' },
    { title: 'draft', model: 'sonnet' }, { title: 'rewrite', model: 'sonnet' }, { title: 'send' },
  ],
}

// ---- inputs (the script cannot call Date.now(), so the SID is passed in) ----
const SID = (args && args.session) || 'SESSION_ID_REQUIRED'
const EMAIL_ON = !!(args && (args.email === 'on' || args.email === true))
const CWD = '/Users/drucev/projects/newsagent'
const PY = '.venv/bin/python'

if (SID === 'SESSION_ID_REQUIRED') {
  throw new Error('Pass args.session (e.g. {"session":"2026-05-29","email":"off"}) — workflows cannot generate timestamps.')
}

// ---- schemas: agents return raw data, validated at the tool layer ----
const STEP_RESULT = {
  type: 'object', additionalProperties: false,
  required: ['ok', 'summary'],
  properties: {
    ok: { type: 'boolean', description: 'true iff the CLI exited 0 with no fatal error' },
    summary: { type: 'string', description: 'one-line summary of stdout (counts, paths)' },
    error: { type: 'string', description: 'stderr / failure detail if ok is false' },
  },
}
const BATCH_LIST = {
  type: 'object', additionalProperties: false,
  required: ['ok', 'batchFiles'],
  properties: {
    ok: { type: 'boolean' },
    batchFiles: { type: 'array', items: { type: 'string' }, description: 'absolute paths of every batch-*.json that exists' },
    summary: { type: 'string' },
  },
}
const APPLY_RESULT = {
  type: 'object', additionalProperties: false,
  required: ['ok', 'summary'],
  properties: {
    ok: { type: 'boolean', description: 'true iff apply succeeded with no missing/mismatched batches' },
    summary: { type: 'string' },
    failedBatches: { type: 'array', items: { type: 'string' }, description: 'paths of batch files apply flagged as missing/mismatched' },
  },
}

// rewrite loop: one fresh agent per critique / improve / title pass
const CRITIQUE_SCHEMA = {
  type: 'object', additionalProperties: false,
  required: ['ok', 'score', 'accept'],
  properties: {
    ok: { type: 'boolean', description: 'true iff the critique was produced and the feedback file written' },
    score: { type: 'number', description: 'overall newsletter score, 0-10' },
    accept: { type: 'boolean', description: 'true iff the draft is ready to publish as-is' },
    summary: { type: 'string', description: 'one-line gist of the critique' },
  },
}
const IMPROVE_SCHEMA = {
  type: 'object', additionalProperties: false,
  required: ['ok', 'draftPath'],
  properties: {
    ok: { type: 'boolean' },
    draftPath: { type: 'string', description: 'path of the improved draft body just written' },
    summary: { type: 'string', description: 'one-line gist of what changed' },
  },
}
const TITLE_SCHEMA = {
  type: 'object', additionalProperties: false,
  required: ['ok', 'title'],
  properties: {
    ok: { type: 'boolean' },
    title: { type: 'string', description: 'the generated newsletter title (no leading # )' },
  },
}

// ---- helpers ----
function bash(cmd) {
  return `Working directory: ${CWD}\nRun exactly this command (and nothing else):\n\n    ${cmd}\n\n` +
    `Report the final stdout and stderr verbatim and the exit code. Do not edit files, ` +
    `do not retry, do not run other commands.`
}

// no-LLM step: one agent runs the Python CLI
async function runStep(step, extra = '') {
  phase(step)
  const r = await agent(
    bash(`${PY} -m lib.steps.${step} --session ${SID} ${extra}`.trim()),
    { label: step, phase: step, schema: STEP_RESULT }
  )
  if (!r || !r.ok) throw new Error(`step ${step} failed: ${r ? r.error || r.summary : 'no result'}`)
  log(`✓ ${step}: ${r.summary}`)
  return r
}

// per-batch worker prompt (mirrors skills/<step>/SKILL.md)
function batchPrompt(step, batchFile) {
  const resultFile = batchFile.replace(`${step}-batches`, `${step}-results`)
    .replace(`${step}-assign-batches`, `${step}-assign-results`)
    .replace(`${step}-merge-batches`, `${step}-merge-results`)
  return `Working directory: ${CWD}\n` +
    `Read ${batchFile}. It contains system_prompt, user_prompt, the ids you MUST cover, ` +
    `and output_schema.\n` +
    `Follow system_prompt + user_prompt. Produce ONLY a JSON object matching output_schema, ` +
    `exactly one entry per id (no extras, no duplicates), and Write it to:\n    ${resultFile}\n` +
    `Then validate:\n    ${PY} tools/check_batch.py ${resultFile}\n` +
    `If it prints FAIL, fix and re-Write until it prints OK, then report the path. ` +
    `Use ONLY Read, Write, and that one Bash validator call — no sed/grep/inline python.`
}

// ---- rewrite loop workers (each is a distinct LLM call, fresh context) ----
// Shared preamble: every worker reasons in its OWN context and never shells out.
const REWRITE_GUARD =
  `Do the reasoning YOURSELF, in your own context — do NOT shell out to a script, ` +
  `subprocess, or any LLM-call tool (no bash, no \`python -c\`, no call_prompt). ` +
  `Use ONLY the Read and Write tools`

// the newsletter title is generated in its own step, so the body is titleless by
// design — workers must not add or penalize a missing H1.
const NO_H1_NOTE =
  `Note: the newsletter title / H1 is produced in a SEPARATE step, so the body is ` +
  `titleless by design (it starts with a "## " section, no leading "# " H1).`

// the "current draft" is always a file: draft-init.md on the first pass (written
// by prepare), or a draft-<i>.md written by a prior improve pass.
function draftClause(draftPath) {
  return `The CURRENT newsletter draft is the markdown in file ${draftPath}. Read it.`
}

function critiquePrompt(batchFile, draftSrc, critiquePath) {
  return `Working directory: ${CWD}\n` +
    `You are the CRITIC. Read ${batchFile} and use ONLY its critique_system_prompt ` +
    `(your role/rubric) and critique_user_prompt (your task template).\n` +
    `${draftClause(draftSrc)}\n${NO_H1_NOTE} Do NOT flag a missing title/H1 or penalize for it.\n` +
    `Substitute {newsletter_markdown}=the current draft into critique_user_prompt and ` +
    `critique it as an adversarial editor seeing it for the first time. Score what is ` +
    `actually on the page (0-10), not what you intend to fix. Be genuinely critical.\n` +
    `Write your FULL critique feedback text (the specific, actionable issues) to:\n    ${critiquePath}\n` +
    `Return {ok, score (float 0-10), accept (bool: true iff ready to publish as-is), summary}. ` +
    `${REWRITE_GUARD}; nothing else.`
}

function improvePrompt(batchFile, draftSrc, critiquePath, draftPath) {
  return `Working directory: ${CWD}\n` +
    `You are the IMPROVER. Read ${batchFile} and use ONLY its improve_system_prompt ` +
    `(your role) and improve_user_prompt (your task template).\n` +
    `${draftClause(draftSrc)}\nRead the critique feedback in ${critiquePath}.\n` +
    `Substitute {newsletter_markdown}=the current draft and {critique}=that feedback into ` +
    `improve_user_prompt, and produce a NEW, complete newsletter that addresses every ` +
    `appropriate issue.\nIMPORTANT: output the newsletter BODY only — keep "## " section ` +
    `headings but do NOT include a leading "# " H1 title line. ${NO_H1_NOTE} If the critique ` +
    `asks for an H1/title, IGNORE that one point — it is handled separately.\n` +
    `Write the improved body to:\n    ${draftPath}\n` +
    `Return {ok, draftPath: "${draftPath}", summary}. ${REWRITE_GUARD}; nothing else.`
}

function titlePrompt(batchFile, draftPath, titlePath) {
  return `Working directory: ${CWD}\n` +
    `You are the TITLE EDITOR. Read ${batchFile} and use ONLY its title_system_prompt ` +
    `(your role) and title_user_prompt (your task template).\n` +
    `The FINAL newsletter draft is the markdown in file ${draftPath}. Read it.\n` +
    `Substitute {newsletter_markdown}=the final draft into title_user_prompt and produce ` +
    `the title. Write the bare title (one line, no leading "# ") to:\n    ${titlePath}\n` +
    `Return {ok, title} with that same title. ${REWRITE_GUARD}; nothing else.`
}

// rewrite step: orchestrator-driven critic loop (critique/improve/title as
// SEPARATE agent dispatches with fresh context), then assemble + apply.
async function rewriteLoop(model) {
  const step = 'rewrite'
  phase(step)
  const batchFile = `runs/${SID}/rewrite-batches/batch-000.json`
  const work = `runs/${SID}/rewrite-work`
  const MAX = (args && args.maxEdits) || 2
  const THR = 8.0

  // 1. prepare — emits batch-000.json with templates + initial_draft
  log(`rewrite: preparing batch (max-edits ${MAX})`)
  const prep = await agent(
    bash(`${PY} -m lib.steps.rewrite --session ${SID} --prepare-batches --max-edits ${MAX}`),
    { label: 'rewrite:prepare', phase: step, schema: STEP_RESULT }
  )
  if (!prep || !prep.ok) throw new Error(`rewrite:prepare failed: ${prep ? prep.error || prep.summary : 'no result'}`)

  // 2. critic loop — one fresh critique (and improve) agent per pass.
  // The current draft is always a file: draft-init.md (written by prepare),
  // then draft-<i>.md after each improve.
  let scores = [], critPaths = [], accepted = false, iters = 0
  let src = `${work}/draft-init.md`
  for (let i = 0; i < MAX; i++) {
    iters++
    const cp = `${work}/critique-${i}.md`
    log(`rewrite: dispatching critique agent (iteration ${i})`)
    const c = await agent(critiquePrompt(batchFile, src, cp),
      { label: `rewrite:critique:${i}`, phase: step, model, schema: CRITIQUE_SCHEMA })
    if (!c || !c.ok) throw new Error(`rewrite:critique:${i} failed`)
    scores.push(c.score); critPaths.push(cp)
    log(`rewrite: critique ${i} → score ${c.score}, accept=${c.accept}${c.summary ? ` (${c.summary})` : ''}`)
    if (c.accept || c.score >= THR) { accepted = true; break }
    const dp = `${work}/draft-${i}.md`
    log(`rewrite: dispatching improve agent (iteration ${i})`)
    const m = await agent(improvePrompt(batchFile, src, cp, dp),
      { label: `rewrite:improve:${i}`, phase: step, model, schema: IMPROVE_SCHEMA })
    if (!m || !m.ok) throw new Error(`rewrite:improve:${i} failed`)
    log(`rewrite: improve ${i} → wrote ${m.draftPath}${m.summary ? ` (${m.summary})` : ''}`)
    src = `${work}/draft-${i}.md`
  }

  // 3. title — distinct final pass (writes the title to a file; titles contain
  // ';' and '$' which are unsafe to thread through a shell command).
  const titlePath = `${work}/title.txt`
  log(`rewrite: dispatching title agent`)
  const t = await agent(titlePrompt(batchFile, src, titlePath),
    { label: 'rewrite:title', phase: step, model, schema: TITLE_SCHEMA })
  if (!t || !t.ok) throw new Error('rewrite:title failed')
  log(`rewrite: title → "${t.title}"`)

  // 4. finalize — mechanical assemble + apply from the work files (no LLM).
  // Renders HTML and emails iff EMAIL_ON.
  log(`rewrite: finalizing (iterations=${iters}, accepted=${accepted})`)
  const applied = await agent(
    bash(`${PY} -m lib.steps.rewrite --session ${SID} --finalize ` +
      `--body ${src} --title-file ${titlePath} ` +
      `--scores ${scores.join(',')} --feedbacks ${critPaths.join(',')} ` +
      `${accepted ? '--accepted' : '--not-accepted'} ${EMAIL_ON ? '' : '--no-email'}`.trim()),
    { label: 'rewrite:finalize', phase: step, schema: STEP_RESULT })
  if (!applied || !applied.ok) throw new Error(`rewrite:finalize failed: ${applied ? applied.error || applied.summary : 'no result'}`)
  log(`✓ rewrite: ${applied.summary}`)
  return applied
}

// ---- draft loop workers (per section: write/critique/improve as separate calls) ----
function writeSectionPrompt(batchFile, draftPath) {
  return `Working directory: ${CWD}\n` +
    `You are the SECTION WRITER. Read ${batchFile} and use ONLY its write_system_prompt ` +
    `(your role) and write_user_prompt (your fully-rendered task — the stories are already in it).\n` +
    `Produce the section markdown (a "## " section title followed by bullet headlines with links) ` +
    `per those prompts. Write it to:\n    ${draftPath}\n` +
    `Return {ok, draftPath: "${draftPath}", summary}. ${REWRITE_GUARD}; nothing else.`
}

function critiqueSectionPrompt(batchFile, draftPath, critiquePath) {
  return `Working directory: ${CWD}\n` +
    `You are the SECTION CRITIC. Read ${batchFile} and use ONLY its critique_system_prompt ` +
    `(your role/rubric) and critique_user_prompt (task template with a {section_markdown} placeholder).\n` +
    `The CURRENT section draft is the markdown in file ${draftPath}. Read it.\n` +
    `Substitute {section_markdown}=the current draft into critique_user_prompt and critique it as an ` +
    `adversarial editor seeing it for the first time. Score what is actually on the page (0-10), not ` +
    `what you intend to fix. Be genuinely critical.\n` +
    `Write your FULL critique feedback (specific, actionable issues) to:\n    ${critiquePath}\n` +
    `Return {ok, score (float 0-10), accept (bool: true iff ready to publish as-is), summary}. ` +
    `${REWRITE_GUARD}; nothing else.`
}

function improveSectionPrompt(batchFile, draftPath, critiquePath, outDraftPath) {
  return `Working directory: ${CWD}\n` +
    `You are the SECTION IMPROVER. Read ${batchFile} and use ONLY its improve_system_prompt ` +
    `(your role) and improve_user_prompt (task template with {section_markdown} and {critique}).\n` +
    `The CURRENT section draft is the markdown in file ${draftPath}. Read it.\n` +
    `Read the critique feedback in ${critiquePath}.\n` +
    `Substitute {section_markdown}=the current draft and {critique}=that feedback into improve_user_prompt ` +
    `and produce a NEW, improved section addressing every appropriate issue. Keep the "## " section title ` +
    `heading.\nWrite the improved section to:\n    ${outDraftPath}\n` +
    `Return {ok, draftPath: "${outDraftPath}", summary}. ${REWRITE_GUARD}; nothing else.`
}

// one section's critic loop: write → (critique → improve)* → mechanical assemble.
// Each step is a separate dispatch with fresh context; many sectionLoops run
// concurrently (via parallel) so cross-section parallelism is preserved.
async function sectionLoop(batchFile, model, MAX, THR) {
  const nnn = (batchFile.match(/batch-(\d+)\.json/) || [])[1]
  const wdir = `runs/${SID}/draft-work/sec-${nnn}`
  const resultFile = batchFile.replace('draft-batches', 'draft-results')

  // WRITE — initial section draft
  const initPath = `${wdir}/draft-init.md`
  const w = await agent(writeSectionPrompt(batchFile, initPath),
    { label: `draft:write:${nnn}`, phase: 'draft', model, schema: IMPROVE_SCHEMA })
  if (!w || !w.ok) throw new Error(`draft:write:${nnn} failed`)

  // critique → improve loop
  let scores = [], critPaths = [], accepted = false, iters = 0
  let src = initPath
  for (let i = 0; i < MAX; i++) {
    iters++
    const cp = `${wdir}/critique-${i}.md`
    const c = await agent(critiqueSectionPrompt(batchFile, src, cp),
      { label: `draft:critique:${nnn}:${i}`, phase: 'draft', model, schema: CRITIQUE_SCHEMA })
    if (!c || !c.ok) throw new Error(`draft:critique:${nnn}:${i} failed`)
    scores.push(c.score); critPaths.push(cp)
    if (c.accept || c.score >= THR) { accepted = true; break }
    const dp = `${wdir}/draft-${i}.md`
    const m = await agent(improveSectionPrompt(batchFile, src, cp, dp),
      { label: `draft:improve:${nnn}:${i}`, phase: 'draft', model, schema: IMPROVE_SCHEMA })
    if (!m || !m.ok) throw new Error(`draft:improve:${nnn}:${i} failed`)
    src = dp
  }

  // mechanical per-section assemble (no LLM) → writes the DraftSectionResult file
  const asm = await agent(
    bash(`${PY} -m lib.steps.draft --session ${SID} --assemble-section ` +
      `--batch ${batchFile} --body ${src} --out ${resultFile} ` +
      `--scores ${scores.join(',')} --feedbacks ${critPaths.join(',')} ` +
      `${accepted ? '--accepted' : '--not-accepted'}`.trim()),
    { label: `draft:assemble:${nnn}`, phase: 'draft', schema: STEP_RESULT })
  if (!asm || !asm.ok) throw new Error(`draft:assemble:${nnn} failed`)
  log(`draft: section ${nnn} done (iters=${iters}, accepted=${accepted}, scores=[${scores.join(', ')}])`)
  return { nnn, iters, accepted }
}

// draft step: prepare per-section batches, run all section critic loops
// concurrently (true critique/improve separation), then apply.
async function draftLoop(model) {
  const step = 'draft'
  phase(step)
  const MAX = (args && args.maxEdits) || 2
  const THR = 8.0

  const prep = await agent(
    `Working directory: ${CWD}\n` +
    `Step 1 — run:\n    ${PY} -m lib.steps.draft --session ${SID} --prepare-batches --max-edits ${MAX}\n` +
    `Step 2 — enumerate every batch file that now exists:\n      ls runs/${SID}/draft-batches/batch-*.json\n` +
    `Return the ABSOLUTE path of each batch-*.json found (zero-indexed; the count varies — ` +
    `list the directory, do not infer it).`,
    { label: 'draft:prepare', phase: step, schema: BATCH_LIST }
  )
  if (!prep || !prep.ok || !prep.batchFiles.length) throw new Error('draft:prepare produced no batches')
  log(`draft: ${prep.batchFiles.length} sections — running per-section write→critique→improve loops (${model})`)

  const done = await parallel(prep.batchFiles.map(bf => () => sectionLoop(bf, model, MAX, THR)))
  const ok = done.filter(Boolean)
  if (ok.length < prep.batchFiles.length) {
    log(`draft: WARNING ${prep.batchFiles.length - ok.length} section loop(s) failed; apply will flag missing sections`)
  }

  const applied = await agent(
    bash(`${PY} -m lib.steps.draft --session ${SID} --apply-results runs/${SID}/draft-results`),
    { label: 'draft:apply', phase: step, schema: APPLY_RESULT })
  if (!applied || !applied.ok) throw new Error(`draft:apply failed: ${applied ? applied.summary : 'no result'}`)
  log(`✓ draft: ${applied.summary}`)
  return applied
}

// LLM step: prepare batches -> fan out one agent per batch -> apply
async function llmStep(step, model, opts = {}) {
  const batchDirs = opts.batchDirs || [`${step}-batches`]
  const applyArg = opts.applyArg || `runs/${SID}/${step}-results`
  phase(step)

  // 1. prepare + enumerate (script can't ls; the agent reports the file list)
  const lsLines = batchDirs.map(d => `      ls runs/${SID}/${d}/batch-*.json`).join('\n')
  const prep = await agent(
    `Working directory: ${CWD}\n` +
    `Step 1 — run:\n    ${PY} -m lib.steps.${step} --session ${SID} --prepare-batches\n` +
    `Step 2 — enumerate every batch file that now exists:\n${lsLines}\n` +
    `Return the ABSOLUTE path of each batch-*.json found (zero-indexed; the count varies — ` +
    `do not infer it from the "Prepared N" line, list the directory).`,
    { label: `${step}:prepare`, phase: step, schema: BATCH_LIST }
  )
  if (!prep || !prep.ok || !prep.batchFiles.length) {
    throw new Error(`${step}:prepare produced no batches`)
  }
  log(`${step}: dispatching ${prep.batchFiles.length} batch agents (${model})`)

  // 2. fan out — all batches run concurrently (capped by the runtime)
  await parallel(prep.batchFiles.map(bf => () =>
    agent(batchPrompt(step, bf), {
      label: `${step}:${bf.split('/').pop()}`, phase: step, model,
    })
  ))

  // 3. apply
  const applied = await agent(
    bash(`${PY} -m lib.steps.${step} --session ${SID} --apply-results ${applyArg}`),
    { label: `${step}:apply`, phase: step, schema: APPLY_RESULT }
  )
  if (!applied || !applied.ok) {
    throw new Error(`${step}:apply failed: ${applied ? applied.summary : 'no result'} ` +
      `(failed batches: ${applied && applied.failedBatches ? applied.failedBatches.join(', ') : 'unknown'})`)
  }
  log(`✓ ${step}: ${applied.summary}`)
  return applied
}

// ---- the 12-step pipeline, in order ----
log(`Daily roundup — session ${SID}, email ${EMAIL_ON ? 'ON' : 'OFF'}`)

await runStep('start', `--sources sources.yaml`)
await runStep('gather')
await llmStep('filter', 'haiku')
await runStep('download')
await runStep('dedupe')
await llmStep('summarize', 'sonnet')
await runStep('rate')
await llmStep('cluster', 'haiku')
await llmStep('select', 'haiku', {
  batchDirs: ['select-assign-batches', 'select-merge-batches'],
  applyArg: `runs/${SID}`,
})
await draftLoop('sonnet')    // per-section write→critique→improve as separate dispatches
await rewriteLoop('sonnet')  // orchestrator-driven critique→improve→title (separate calls)
await runStep('send', EMAIL_ON ? '' : '--no-email')

log(`Done. Newsletter at out/latest.html` + (EMAIL_ON ? ' (emailed)' : ' (not emailed)'))
return { session: SID, emailed: EMAIL_ON, output: 'out/latest.html' }
