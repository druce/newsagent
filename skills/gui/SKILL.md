---
name: gui
description: Launch the pipeline GUI — a local web page (http://127.0.0.1:8767/) that shows a session's 14-step workflow as a live diagram (with the draft/rewrite critic loop), a per-step pop-up with Doc / Code / Log / State tabs, an output pane that follows the running step's log, and a chat pane + ▶ / ▶▶ / ■ / ⟲ buttons that drive an interactive Claude Code session through the newsagent-gui channel. Use when the user wants to "open the GUI", "watch the pipeline", or drive/inspect a run visually.
---

# newsagent:gui

Live viewer over `agent_state` checkpoints and `runs/<SID>/`, plus a chat/control
bridge into an interactive Claude Code session.

## How to invoke

```bash
.venv/bin/python -m lib.steps.gui [--session SID] [--port 8767] [--db newsletter_agent.db]
```

Run it in the background; it serves until Ctrl-C. `--session` picks the session
shown first (default: most recently updated); the page has a session picker.
`$GUI_PORT` overrides the default port.

## What it shows

- **Header** — session id, status (Not started / In progress / Complete / Error),
  "N of 14 complete · through <step> · running <step>", 14-segment progress bar.
- **Diagram** — one node per step with its status, `status_message`, start time
  and duration; draft and rewrite are the two tabs of the writing-loop box
  (entry → write → critic gate → exit, improve back-edge), whose footer shows
  per-section draft acceptance and rewrite critic scores from
  `runs/<SID>/draft.json` / `rewrite.json`.
- **Pop-up** (click a node) — **Doc** (`skills/<step>/SKILL.md`), **Code**
  (`lib/steps/<step>.py` + the `lib/prompts/*` it references), **Log**
  (`runs/<SID>/<step>.log`, live), **State** (that step's checkpoint: stat tiles,
  sortable/filterable headline table, `runs/<SID>/<step>.json`, newsletter text
  for rewrite/send).
- **Output pane** — the running step's log (or the last step with a log), following
  as it grows; drag its top edge to resize.

## Step logs and the running state

Every step CLI is wrapped with `lib.steplog.step_log`, which tees stdout/stderr
(and root logging) to `runs/<SID>/<step>.log` with HH:MM:SS prefixes and a
`=== <step> ...` header per invocation, and holds a `runs/<SID>/<step>.running`
PID marker while it runs. Checkpoints are only written when a step completes, so
the marker is how the GUI shows a step as running. Sessions run before the GUI
existed have no logs. Subagent work dispatched by the parent session is not in
the step log, only the step's prepare/apply invocations.

## Chat and controls (channel into a Claude Code session)

Start the GUI server first, then a connected session:

```bash
.venv/bin/python -m lib.steps.gui        # writes .gui-bridge.json (url + token)
tools/claude-gui.sh                      # claude --mcp-config tools/gui-channel.mcp.json \
                                         #   --dangerously-load-development-channels server:newsagent-gui
```

Claude Code shows a "local development" confirmation for the channel each launch.
Channels only work in an interactive session on claude.ai login (not `claude -p`,
not the Agent SDK). Allowlist `mcp__newsagent-gui__reply` in `.claude/settings.json`
or every reply prompts for permission.

- `lib/gui_channel.py` is a stdlib stdio MCP server Claude Code spawns. It long-polls
  the GUI server's `/bridge/poll` and pushes each event as
  `notifications/claude/channel`; the model sees
  `<channel source="newsagent-gui" chat_id kind sid step>` and answers with the
  `reply` tool, which POSTs back to `/bridge/reply`. It answers `initialize` with a
  pre-2026-07-28 protocol revision (channels don't register on that revision).
- `lib/gui_bridge.py:Hub` holds the chat log (in memory, lost when the GUI server
  restarts) and the event queue. The most recently started channel process owns
  the queue; an older session's channel idles.
- **Chat** — sent with the viewed `sid` and the attached step chip. Messages show
  *queued* until the channel picks them up; if Claude is mid-turn they arrive
  together on its next turn (channels can't interrupt a turn).
- **▶ Run next / ▶▶ Run all** — enabled when a session is connected and no step is
  running; they ask the session to invoke `newsagent:<next step> --session SID` or
  `newsagent:pipeline --resume SID`.
- **■ Stop** — SIGTERMs the PID in `runs/<SID>/<step>.running` and tells the agent
  not to start further steps. Agents already dispatched finish; Esc in the terminal
  is still the hard stop.
- **⟲ Reset** — runs `lib.steps.start` directly (no LLM) and switches to the new
  session; the old one is untouched.
- **Permission prompts** — the channel declares `claude/channel/permission`, so the
  session's tool approvals also appear in the chat pane with Allow / Deny (first
  answer, terminal or page, wins). Page routes refuse cross-origin requests and the
  server binds 127.0.0.1 only; `/bridge/*` requires the token from `.gui-bridge.json`.
