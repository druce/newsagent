#!/usr/bin/osascript
tell application "Terminal"
  do script "cd /Users/drucev/projects/newsagent && claudeyolo"
end tell
delay 6

tell application "System Events"
    tell process "Terminal"
        keystroke "Run the newsagent pipeline"
        key code 36
    end tell
end tell
	
