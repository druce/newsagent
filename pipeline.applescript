#!/usr/bin/osascript
tell application "Terminal"
  -- Invoke the canonical slash command directly as the initial prompt to interactive
  -- claude (no -p/print mode, so it stays on the Max-covered Agent-dispatch flow).
  -- Using '/newsagent:pipeline' (not a natural-language prompt) means:
  --   * it deterministically runs the pipeline skill, not the daily-roundup skill
  --     (which interactively asks whether to send email and would block an
  --     unattended run);
  --   * that skill sends the digest email by default (--no-email is OFF) and asks
  --     no questions — exactly "run as designed and send the email".
  -- Passing the prompt as an argument (vs System Events keystrokes) also means the
  -- run works even while the screen is locked.
  do script "cd /Users/drucev/projects/newsagent && claudeyolo '/newsagent:pipeline'"
end tell
	
