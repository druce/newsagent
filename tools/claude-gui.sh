#!/usr/bin/env bash
# Start an interactive Claude Code session connected to the pipeline GUI
# (lib/steps/gui.py) through the newsagent-gui channel (lib/gui_channel.py).
# Claude Code shows a one-time "local development" confirmation for the
# development channel each launch. Extra args are passed through to claude.
set -euo pipefail
cd "$(dirname "$0")/.."
exec claude --mcp-config tools/gui-channel.mcp.json \
  --dangerously-load-development-channels server:newsagent-gui "$@"
