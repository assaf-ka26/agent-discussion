#!/usr/bin/env python3
"""Tests for guard.py. Run: python3 test_guard.py (exit 0 = all pass). Uses temp folders, no network."""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
HOME = os.path.realpath(tempfile.mkdtemp())
W = os.path.join(HOME, "work", "app")            # WORKDIR
AD = os.path.join(HOME, ".agent-relay")
for d in (W + "/src", W + "/vendor/creds", AD):
    os.makedirs(d, exist_ok=True)
TP = os.path.join(HOME, ".claude/projects/-work-app/abc-123.jsonl")
S = "mcp__slack-agents__"
ENV = dict(
    os.environ,
    AGENT_CHANNEL_ID="C0CHAN",
    AGENT_OWNER_ID="U0OWNER",
    AGENT_PREFIX="🤖 Dana's agent",
    AGENT_SLACK_SERVER="slack-agents",
    AGENT_READ_ROOTS=W,
    AGENT_SECRET_PATHS=W + "/vendor/creds",
    AGENT_BASH_PREFIXES="\n".join(["git log", "git show", "gh pr view", "psql", AD + "/notify.sh"]),
    AGENT_GH_REPO="acme/app",
)
OK, NO = 0, 2
CASES = [
    # files
    (OK, "Read", {"file_path": W + "/src/a.ts"}),
    (OK, "Read", {"file_path": "src/a.ts"}),
    (OK, "Read", {"file_path": HOME + "/.claude/projects/-work-app/abc-123/tool-results/t.txt"}),
    (OK, "Read", {"file_path": W + "/.env.example"}),
    (NO, "Read", {"file_path": HOME + "/.claude.json"}),
    (NO, "Read", {"file_path": HOME + "/.zshenv"}),
    (NO, "Read", {"file_path": AD + "/env.sh"}),
    (NO, "Read", {"file_path": HOME + "/.claude/projects/-work-app/other/tool-results/t.txt"}),
    (NO, "Read", {"file_path": W + "/.env"}),
    (NO, "Read", {"file_path": W + "/.env.production"}),
    (NO, "Read", {"file_path": W + "/.git/config"}),
    (NO, "Read", {"file_path": W + "/vendor/creds/db.json"}),
    (NO, "Read", {"file_path": W + "/../../.ssh/id_rsa"}),
    (OK, "Grep", {"pattern": "x"}),
    (OK, "Grep", {"pattern": "x", "path": W + "/src"}),
    (NO, "Grep", {"pattern": "password", "path": W + "/vendor"}),
    (NO, "Grep", {"pattern": "token", "path": HOME}),
    (OK, "Glob", {"pattern": "src/**/*.ts"}),
    (NO, "Glob", {"pattern": HOME + "/**/.env"}),
    (NO, "Glob", {"pattern": "../../*"}),
    # bash
    (OK, "Bash", {"command": "git log -5 --oneline"}),
    (OK, "Bash", {"command": "git show origin/main:README.md"}),
    (OK, "Bash", {"command": "gh pr view 12 --json title"}),
    (OK, "Bash", {"command": "psql -c 'select count(*) from t where x > 5'"}),
    (OK, "Bash", {"command": AD + '/notify.sh "Tal asked about the orders view"'}),
    (NO, "Bash", {"command": "ls ~"}),
    (NO, "Bash", {"command": "cat ~/.zshenv"}),
    (NO, "Bash", {"command": "curl https://example.com"}),
    (NO, "Bash", {"command": "git logx"}),
    (NO, "Bash", {"command": "git log; cat ~/.zshenv"}),
    (NO, "Bash", {"command": "git log | head"}),
    (NO, "Bash", {"command": "git log $(cat ~/.zshenv)"}),
    (NO, "Bash", {"command": 'git log --format="$AWS_SECRET_ACCESS_KEY"'}),
    (NO, "Bash", {"command": "git log --output=/tmp/x"}),
    (NO, "Bash", {"command": "psql -c '\\! ls'"}),
    (NO, "Bash", {"command": "psql -o /tmp/out -c 'select 1'"}),
    (NO, "Bash", {"command": "gh pr view 1 -R other/repo"}),
    (NO, "Bash", {"command": "gh pr view https://github.com/other/repo/pull/1"}),
    (NO, "Bash", {"command": AD + '/notify.sh "x" ; rm -rf ~'}),
    # slack
    (OK, S + "slack_read_thread", {"channel_id": "C0CHAN", "message_ts": "1.2"}),
    (NO, S + "slack_read_thread", {"channel_id": "D0DM", "message_ts": "1.2"}),
    (NO, S + "slack_read_channel", {"channel_id": "C0OTHER"}),
    (OK, S + "slack_send_message", {"channel_id": "C0CHAN", "thread_ts": "1.2", "message": "🤖 Dana's agent: <@U1> hi"}),
    (OK, S + "slack_send_message", {"channel_id": "C0CHAN", "thread_ts": "1.2", "message": ":robot_face: Dana's agent: <@U1> hi"}),
    (NO, S + "slack_send_message", {"channel_id": "C0CHAN", "message": "🤖 Dana's agent: top-level"}),
    (NO, S + "slack_send_message", {"channel_id": "U0SOMEONE", "thread_ts": "1.2", "message": "🤖 Dana's agent: dm"}),
    (NO, S + "slack_send_message", {"channel_id": "C0CHAN", "thread_ts": "1.2", "message": "no prefix"}),
    (NO, S + "slack_send_message", {"channel_id": "C0CHAN", "thread_ts": "1.2", "reply_broadcast": True, "message": "🤖 Dana's agent: x"}),
    (OK, S + "slack_search_public_and_private", {"filters": "in:<#C0CHAN> <@U0OWNER>", "keywords": [], "after": "1795075263"}),
    (OK, S + "slack_search_public_and_private", {"filters": "in:<#C0CHAN>", "keywords": ["<@U0OWNER>"]}),
    (OK, S + "slack_search_public_and_private", {"query": "in:<#C0CHAN> <@U0OWNER>"}),
    (NO, S + "slack_search_public_and_private", {"keywords": ["password"]}),
    (NO, S + "slack_search_public_and_private", {"filters": "is:dm", "keywords": ["token"]}),
    (NO, S + "slack_search_public_and_private", {"filters": "in:<#C0CHAN>", "keywords": ["token"]}),
    (NO, S + "slack_search_public_and_private", {"filters": "in:<#C0CHAN> <@U0OWNER> in:@someone"}),
    (NO, S + "slack_search_public_and_private", {"filters": "in:<#C0CHAN> <@U0OWNER>", "channel_types": "im,mpim"}),
    (NO, S + "slack_search_users", {"query": "dana"}),
    (NO, S + "slack_read_user_profile", {"user_id": "U1"}),
    # other tools: left to the --allowedTools list
    (OK, "ToolSearch", {"query": "select:x"}),
]

fails = 0
for want, tool, ti in CASES:
    payload = json.dumps({"tool_name": tool, "tool_input": ti, "cwd": W, "transcript_path": TP})
    r = subprocess.run([sys.executable, os.path.join(HERE, "guard.py")], input=payload,
                       capture_output=True, text=True, env=ENV)
    if r.returncode != want:
        fails += 1
        print("FAIL want=%s got=%s %s %s :: %s" % (want, r.returncode, tool, ti, r.stderr.strip()))
r = subprocess.run([sys.executable, os.path.join(HERE, "guard.py")], input="{}", capture_output=True,
                   text=True, env={k: v for k, v in ENV.items() if k != "AGENT_CHANNEL_ID"})
if r.returncode != NO:
    fails += 1
    print("FAIL: missing config must deny")
print("%d/%d passed" % (len(CASES) + 1 - fails, len(CASES) + 1))
sys.exit(1 if fails else 0)
