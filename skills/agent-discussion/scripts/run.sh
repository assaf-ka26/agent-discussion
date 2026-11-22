#!/bin/bash
# Slack agent-discussion responder. Two stages:
#   1. TRIAGE (cheap): slack_check.py if SLACK_TOKEN is set (no model), else TRIAGE_MODEL (Haiku) with only
#      the Slack MCP and no built-in tools, run from /tmp (no project CLAUDE.md / memory).
#      One mention search + re-read of our open threads → "NEW: <thread ts…>" or "NEW: none".
#   2. ANSWER (full): only when triage found something. MODEL (Sonnet), project context + read-only tools.
# After an answer, triage re-runs every POLL seconds until IDLE_LIMIT seconds of quiet (default: once, after 60s).
# Optional WORK_DAYS / WORK_HOURS: outside them the run exits at once (FORCE=1 overrides).
# Config: $AGENT_DISCUSSION_HOME/config.env (default ~/.agent-discussion). DRY_RUN=1 → no posting, no state.
set -uo pipefail

AD="${AGENT_DISCUSSION_HOME:-$HOME/.agent-discussion}"
# shellcheck source=/dev/null
source "$AD/config.env" 2>/dev/null || { echo "missing $AD/config.env (run install.sh)"; exit 1; }
: "${CHANNEL_ID:?set in config.env}" "${OWNER_ID:?set in config.env}" "${OWNER_NAME:?set in config.env}" "${WORKDIR:?set in config.env}"
MODEL=${MODEL:-sonnet}                 # answer stage
TRIAGE_MODEL=${TRIAGE_MODEL:-haiku}    # LLM triage is a yes/no check; Haiku finds the same threads as Sonnet
ANSWER_BUILTIN_TOOLS=${ANSWER_BUILTIN_TOOLS:-Read,Grep,Glob,Bash}   # built-in tools loaded in the answer stage
INTERVAL_MIN=${INTERVAL_MIN:-30}
POLL=${POLL:-60}
IDLE_LIMIT=${IDLE_LIMIT:-60}
OPEN_TTL_HOURS=${OPEN_TTL_HOURS:-24}   # stop watching an open thread after this long without activity
WORK_DAYS=${WORK_DAYS:-}               # e.g. "1 2 3 4 5" (date +%u: Mon=1 … Sun=7); empty = every day
WORK_HOURS=${WORK_HOURS:-}             # e.g. "8-19" (runs from 08:00 until 18:59); empty = all day
MAX_RUNTIME=$(( INTERVAL_MIN > 6 ? (INTERVAL_MIN - 5) * 60 : 60 ))   # never overlap the next run
# safety stop for a runaway call (passed to --max-budget-usd); a normal call stays far below it
TRIAGE_CAP=${TRIAGE_CAP:-0.50}
ANSWER_CAP=${ANSWER_CAP:-5}
SLACK_SERVER=${SLACK_SERVER:-slack-agents}
DRY_RUN=${DRY_RUN:-0}
DRAFT=${DRAFT:-0}                      # 1 = never post: save each reply to drafts/ and notify the owner
PREFIX="🤖 ${OWNER_NAME}'s agent"

# The CLI moves on updates/reinstalls: take the first one that exists
CLAUDE="${CLAUDE_BIN:-}"
if [ -z "$CLAUDE" ]; then
  for c in $(ls -1d "$HOME"/.nvm/versions/node/*/bin/claude 2>/dev/null | sort -rV) \
           "$HOME/.local/bin/claude" /opt/homebrew/bin/claude /usr/local/bin/claude; do
    [ -x "$c" ] && { CLAUDE="$c"; break; }
  done
fi

mkdir -p "$AD/logs"
exec >>"$AD/logs/$(date +%F).log" 2>&1
[ -z "$CLAUDE" ] && { echo "$(date '+%F %T') claude CLI not found (set CLAUDE_BIN in config.env)"; exit 1; }
# Outside work days/hours: exit quietly. The checkpoint is untouched, so the first run in hours catches up.
if [ "${FORCE:-0}" != 1 ]; then
  if [ -n "$WORK_DAYS" ]; then case " $WORK_DAYS " in *" $(date +%u) "*) ;; *) exit 0;; esac; fi
  if [ -n "$WORK_HOURS" ]; then
    h=$((10#$(date +%H)))
    (( h < 10#${WORK_HOURS%-*} || h >= 10#${WORK_HOURS#*-} )) && exit 0
  fi
fi
mkdir "$AD/.lock" 2>/dev/null || { echo "$(date '+%T') previous run still active, skipping"; exit 0; }
trap 'rmdir "$AD/.lock"' EXIT

export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
# Optional: export read-only credentials for your sources (never put them in prompts)
# shellcheck source=/dev/null
[ -f "$AD/env.sh" ] && source "$AD/env.sh"
# Slack user token for the free script triage (slack_check.py). Kept in a non-exported variable,
# so the claude processes (and anything they run) never inherit it.
SLACK_CHECK_TOKEN="${SLACK_TOKEN:-}"; unset SLACK_TOKEN
TRIAGE_MODE=$([ -n "$SLACK_CHECK_TOKEN" ] && echo script || echo llm)

S="mcp__${SLACK_SERVER}__slack"
TRIAGE_TOOLS="${S}_search_public_and_private,${S}_read_thread"
# No Slack search or user lookup in the answer stage: an injected message must not make it search DMs.
ANSWER_TOOLS="Read,Grep,Glob,${S}_read_thread,${S}_read_channel,Bash(git log:*),Bash(git show:*),Bash(gh pr view:*),Bash(gh pr list:*),Bash($AD/notify.sh:*)"
[ -n "${EXTRA_TOOLS:-}" ] && ANSWER_TOOLS="$ANSWER_TOOLS,$EXTRA_TOOLS"
DENIED="Read(.env),Read(**/.env),Read(**/.env.*),Read(~/.aws/**),Read(~/.ssh/**),Read(~/.pgpass),Read($AD/env.sh)"
[ -n "${EXTRA_DENIED:-}" ] && DENIED="$DENIED,$EXTRA_DENIED"
NOTE=""
if [ "$DRY_RUN" = 1 ]; then
  NOTE=$'\n\nDRY RUN: do not post or notify. Print each reply you would post, with its thread_ts.'
elif [ "$DRAFT" = 1 ]; then
  NOTE=$'\n\nDRAFT MODE: you cannot post, and do not call notify.sh. For each reply you would post, print a line "--- DRAFT for thread <thread_ts>" followed by the full reply text exactly as you would post it. The owner reviews it and posts it.'
else
  ANSWER_TOOLS="$ANSWER_TOOLS,${S}_send_message"
fi

# Guard (guard.py): a PreToolUse hook that scopes file reads, Bash and Slack (see its header). Its deny
# wins over any other hook's "allow". --setting-sources project,local skips ~/.claude/settings.json, so a
# user-level hook that answers "allow" (e.g. a command-rewrite hook) can't bypass --allowedTools. That
# flag also drops user-scope MCP servers, so Slack (and EXTRA_MCP_CONFIGS) are passed explicitly.
export AGENT_CHANNEL_ID="$CHANNEL_ID" AGENT_OWNER_ID="$OWNER_ID" AGENT_PREFIX="$PREFIX" AGENT_SLACK_SERVER="$SLACK_SERVER"
export AGENT_READ_ROOTS="$WORKDIR${READ_ROOTS:+:$READ_ROOTS}" AGENT_SECRET_PATHS="${SECRET_PATHS:-}" AGENT_GH_REPO="${GH_REPO:-}"
AGENT_BASH_PREFIXES=$(tr ',' '\n' <<<"$ANSWER_TOOLS" | sed -n 's/^Bash(\(.*\))$/\1/p' | sed 's/:\*$//')
export AGENT_BASH_PREFIXES
printf '{"hooks":{"PreToolUse":[{"matcher":"Read|Grep|Glob|Bash|mcp__%s__.*","hooks":[{"type":"command","command":"python3 \\"%s/guard.py\\""}]}]}}\n' \
  "$SLACK_SERVER" "$AD" > "$AD/guard-settings.json"
LOCKDOWN="--setting-sources project,local --settings $AD/guard-settings.json --mcp-config $AD/slack-mcp.json"

state() {  # state <key> → value (lists as csv)
  python3 -c 'import json,sys
try: d=json.load(open(sys.argv[1]))
except Exception: d={}
v=d.get(sys.argv[2]); print(",".join(v) if isinstance(v,list) else (v or ""))' "$AD/state.json" "$1"
}
save_state() {  # save_state <last_ts or ""> <open_threads csv or "-" to keep> [threads active now, csv]
  [ "$DRY_RUN" = 1 ] && return
  python3 - "$AD/state.json" "$1" "$2" "${3:-}" "$OPEN_TTL_HOURS" <<'EOF'
import json,sys,time
p,ts,ot,active,ttl=sys.argv[1:6]
try: d=json.load(open(p))
except Exception: d={}
now=time.time(); seen=d.get("thread_activity",{}); active=set(t for t in active.split(",") if t)
if ts: d["last_ts"]=ts
threads=[t for t in ot.split(",") if t][-20:] if ot!="-" else d.get("open_threads",[])
# last activity per open thread: now if it just had activity or is new, else keep; drop after the TTL
seen={t:(now if t in active or t not in seen else seen[t]) for t in threads}
d["open_threads"]=[t for t in threads if now-seen[t] < float(ttl)*3600]
d["thread_activity"]={t:seen[t] for t in d["open_threads"]}
dropped=[t for t in threads if t not in d["open_threads"]]
if dropped: print("open threads idle >%sh, no longer watched: %s" % (ttl, ",".join(dropped)), file=sys.stderr)
open(p,"w").write(json.dumps(d)+"\n")
EOF
}

# call <prompt> <tools> <cap> [resume-session] → "<session_id>\t<result on one line>"; extra flags via $EXTRA
call() {
  local tl=(); [ -n "${TOOLSET+x}" ] && tl=(--tools "$TOOLSET")   # TOOLSET="" → no built-in tools at all
  "$CLAUDE" -p "$1" ${4:+--resume "$4"} --model "${CALL_MODEL:-$MODEL}" --output-format json \
    --permission-mode dontAsk --max-budget-usd "$3" "${tl[@]}" \
    --allowedTools "$2" --disallowedTools "$DENIED" ${EXTRA:-} </dev/null |
  python3 -c 'import json,sys
try: d=json.load(sys.stdin)
except Exception as e: print("\t(no json: %s)" % e); sys.exit()
r=d.get("result") or ""
if sys.argv[1]: open(sys.argv[1],"w").write(r+"\n")
print(r, "\n[turns %s, model %s]" % (d.get("num_turns", "?"), ",".join(d.get("modelUsage", {})) or "?"), file=sys.stderr)
print(d.get("session_id",""), r.replace("\n"," || "), sep="\t")' "${RESULT_FILE:-}"
}

triage() {  # prints "none", "unclear", or a csv of thread-root ts
  local since open start out res new
  save_state "" "-"   # expire open threads idle longer than OPEN_TTL_HOURS before reading them
  since=$(state last_ts); since=${since:-$(( $(date +%s) - 86400 ))}
  open=$(state open_threads)
  start=$(date +%s)
  if [ "$TRIAGE_MODE" = script ]; then
    # free: plain Slack API calls, no model. Same one-line contract as the LLM triage below.
    res=$(SLACK_TOKEN="$SLACK_CHECK_TOKEN" python3 "$AD/slack_check.py" --channel "$CHANNEL_ID" --owner "$OWNER_ID" \
      --since "$since" --open "$open" --prefix "$PREFIX" --prefix ":robot_face: ${OWNER_NAME}'s agent")
    # a broken token must not stall the responder: fall back to the LLM triage for this pass
    grep -q 'NEW: unclear' <<<"$res" && { echo "script triage failed (see slack_check line), using LLM triage" >&2; res=""; }
  fi
  if [ -z "${res:-}" ]; then
    out=$(cd /tmp && CALL_MODEL="$TRIAGE_MODEL" TOOLSET="" EXTRA="--strict-mcp-config $LOCKDOWN" call "Triage only. Make exactly these Slack calls, nothing else:
1. ${S}_search_public_and_private with exactly: filters \"in:<#$CHANNEL_ID> <@$OWNER_ID>\", keywords [], natural_language_query \"\", after \"${since%.*}\", sort timestamp, response_format detailed, include_context false.
2. For each thread ts in [${open}]: ${S}_read_thread channel_id $CHANNEL_ID, message_ts <ts>, oldest \"$since\", response_format detailed.
Keep only messages newer than $since that do NOT start with \"$PREFIX\" (also written \":robot_face: ${OWNER_NAME}'s agent\") — those are ours.
In the open threads (step 2), also drop other agents' messages (text starts with \"🤖\" or \":robot_face:\") unless they contain <@$OWNER_ID>. Keep every message from a person (no robot prefix), including the owner's own replies.
Thread-root ts = the thread_ts in the permalink, or Message_ts when there is none.
If any call fails or is blocked, do not retry it another way: reply exactly NEW: unclear.
Otherwise reply with exactly one line: NEW: <comma-separated thread-root ts of the kept messages> or NEW: none." "$TRIAGE_TOOLS" "$TRIAGE_CAP")
    res=${out#*$'\t'}
  fi
  new=$(grep -oE 'NEW: *[0-9][0-9., ]*' <<<"$res" | head -1 | sed 's/NEW: *//; s/ //g')
  # only an explicit "NEW: none" means nothing new; "unclear" keeps the checkpoint, so the next pass retries
  [ -z "$new" ] && { grep -qE 'NEW: *none' <<<"$res" && new=none || new=unclear; }
  [ "$new" = none ] && save_state "$(( start - 120 )).000000" "-"   # 2-min overlap for search indexing
  echo "$new"
}

answer() {  # answer <ts csv> [resume-session] → prints session id
  local astart out ot
  astart=$(date +%s)
  local draft=""
  [ "$DRAFT" = 1 ] && [ "$DRY_RUN" != 1 ] && { mkdir -p "$AD/drafts"; chmod 700 "$AD/drafts"; draft="$AD/drafts/$(date +%F_%H%M%S).md"; }
  out=$(cd "$WORKDIR" && RESULT_FILE="$draft" TOOLSET="$ANSWER_BUILTIN_TOOLS" EXTRA="$LOCKDOWN ${EXTRA_MCP_CONFIGS:-}" call "$(cat "$AD/RUNBOOK.md")

Channel: $CHANNEL_ID. You are ${OWNER_NAME}'s agent (owner Slack ID $OWNER_ID); prefix every message with \"$PREFIX:\".
Triage found new messages in these threads (root ts): $1.
Read only those threads (slack_read_thread). Do not scan the rest of the channel.
Handle them per the runbook. Last line: OPEN_THREADS: <comma-separated thread-root ts still waiting on something>.$NOTE" "$ANSWER_TOOLS" "$ANSWER_CAP" "${2:-}")
  if grep -q 'OPEN_THREADS:' <<<"${out#*$'\t'}"; then
    ot=$(grep -oE 'OPEN_THREADS: *[0-9., ]*' <<<"${out#*$'\t'}" | tail -1 | sed 's/OPEN_THREADS: *//; s/ //g')
    # checkpoint = when this stage started reading; anything posted later is caught by the next triage.
    # An empty list clears the open threads.
    save_state "$astart.000000" "${ot:-,}" "$1"
    # draft mode: the reply is in the file, not in Slack. Tell the owner (a notification, the text stays local).
    [ -n "$draft" ] && grep -q -- '--- DRAFT' "$draft" && { echo "draft saved: $draft" >&2; "$AD/notify.sh" "Draft reply ready: $draft"; }
  else  # the stage did not finish (budget stop, auth, Slack down): keep the checkpoint, retry next pass
    echo "answer stage did not finish: checkpoint kept"
  fi
  echo "${out%%$'\t'*}"
}

echo "=== $(date '+%F %T') pass (triage=$TRIAGE_MODE/$TRIAGE_MODEL, answer=$MODEL, dry_run=$DRY_RUN)"
NEW=$(triage)
echo "triage: NEW=$NEW"
[ "$NEW" = none ] && { echo "done (nothing new)"; exit 0; }
[ "$NEW" = unclear ] && { echo "triage unclear: checkpoint kept, next pass retries"; exit 0; }
SID=$(answer "$NEW")
[ "$DRY_RUN" = 1 ] && { echo "dry run, done"; exit 0; }
[ "$DRAFT" = 1 ] && { echo "draft mode, done (nothing posted)"; exit 0; }

start=$(date +%s); last=$start
while :; do
  now=$(date +%s)
  (( now - last >= IDLE_LIMIT )) && { echo "idle ${IDLE_LIMIT}s, done"; break; }
  (( now - start >= MAX_RUNTIME )) && { echo "runtime cap hit, done"; break; }
  sleep "$POLL"
  NEW=$(triage); echo "--- $(date '+%T') re-check: NEW=$NEW"
  [ "$NEW" = none ] || [ "$NEW" = unclear ] && continue
  SID=$(answer "$NEW" "$SID"); last=$(date +%s)
done
