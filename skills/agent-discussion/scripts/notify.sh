#!/bin/bash
# Local notification for "waiting for approval" (tagging yourself in Slack doesn't ping you).
MSG="${1:-agent channel needs your attention}"
if command -v osascript >/dev/null 2>&1; then
  # the text goes in as an argument, never into the AppleScript source: a quote in Slack-derived
  # text can't inject AppleScript
  osascript -e 'on run argv' -e 'display notification (item 1 of argv) with title "Agent channel" sound name "Glass"' \
    -e 'end run' "$MSG"
elif command -v notify-send >/dev/null 2>&1; then
  notify-send "Agent channel" "$MSG"
else
  echo "[notify] $MSG"
fi
