#!/bin/bash
# Installs the agent-relay responder into $AGENT_RELAY_HOME (default ~/.agent-relay).
# Safe to re-run: never overwrites an existing config.env or RUNBOOK.md.
set -euo pipefail
SKILL_DIR="$(cd "$(dirname "$0")/.." && pwd)"
AD="${AGENT_RELAY_HOME:-$HOME/.agent-relay}"
SLACK_SERVER="${SLACK_SERVER:-slack-agents}"
mkdir -p "$AD/logs"

cp "$SKILL_DIR/scripts/run.sh" "$SKILL_DIR/scripts/notify.sh" "$SKILL_DIR/scripts/guard.py" "$AD/"
chmod +x "$AD/run.sh" "$AD/notify.sh"
[ -f "$AD/config.env" ] || { cp "$SKILL_DIR/assets/config.env.example" "$AD/config.env"; echo "created $AD/config.env: fill it in"; }
[ -f "$AD/RUNBOOK.md" ] || { cp "$SKILL_DIR/assets/RUNBOOK.template.md" "$AD/RUNBOOK.md"; echo "created $AD/RUNBOOK.md: add your name and sources"; }

# Slack-only MCP config for the cheap triage stage (copied from your user-scope server)
python3 - "$AD/slack-mcp.json" "$SLACK_SERVER" <<'PY'
import json, os, sys
out, name = sys.argv[1:3]
try:
    cfg = json.load(open(os.path.expanduser("~/.claude.json")))["mcpServers"][name]
except Exception:
    sys.exit(f"user-scope MCP server '{name}' not found: run `claude mcp add --scope user ...` first (see SKILL.md)")
json.dump({"mcpServers": {name: cfg}}, open(out, "w"), indent=1)
print(f"wrote {out}")
PY

# macOS: render a launchd plist (not loaded automatically)
if [ "$(uname)" = Darwin ]; then
  # shellcheck source=/dev/null
  INTERVAL_MIN=$( (source "$AD/config.env" 2>/dev/null; echo "${INTERVAL_MIN:-30}") )
  LABEL="com.$(id -un).agent-relay"
  PLIST_DIR="${PLIST_DIR:-$HOME/Library/LaunchAgents}"; mkdir -p "$PLIST_DIR"
  PLIST="$PLIST_DIR/$LABEL.plist"
  sed -e "s|__LABEL__|$LABEL|" -e "s|__HOME__|$AD|g" -e "s|__SECONDS__|$(( INTERVAL_MIN * 60 ))|" \
      "$SKILL_DIR/assets/launchd.plist.template" > "$PLIST"
  echo "wrote $PLIST (every $INTERVAL_MIN min)"
  echo
  echo "Next: DRY_RUN=1 $AD/run.sh && tail -20 $AD/logs/\$(date +%F).log"
  echo "Then: launchctl bootstrap gui/\$(id -u) $PLIST && launchctl kickstart gui/\$(id -u)/$LABEL"
else
  echo
  echo "Next: DRY_RUN=1 $AD/run.sh && tail -20 $AD/logs/\$(date +%F).log"
  echo "Then add to crontab -e:  */30 * * * * $AD/run.sh"
fi
