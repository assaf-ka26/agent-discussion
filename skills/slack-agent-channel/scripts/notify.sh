#!/bin/bash
# Local notification for "waiting for approval" (tagging yourself in Slack doesn't ping you).
MSG="${1:-agent channel needs your attention}"
if command -v osascript >/dev/null 2>&1; then
  osascript -e "display notification \"${MSG//\"/\'}\" with title \"Agent channel\" sound name \"Glass\""
elif command -v notify-send >/dev/null 2>&1; then
  notify-send "Agent channel" "$MSG"
else
  echo "[notify] $MSG"
fi
