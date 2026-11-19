#!/usr/bin/env python3
"""PreToolUse guard for the headless responder. run.sh loads it with --settings and sets the
AGENT_* environment variables below. A deny here wins over any other hook's "allow".

  Read/Grep/Glob  only under AGENT_READ_ROOTS (default: WORKDIR), never .env*, .git/, key files or
                  AGENT_SECRET_PATHS; a recursive Grep may not start above a secret path.
  Bash            only commands that start with an allowed prefix (AGENT_BASH_PREFIXES, built by
                  run.sh from the Bash(...) rules); no shell operators, $, backticks or backslashes;
                  no file-writing or exec options for git/psql; gh pinned to AGENT_GH_REPO if set.
  Slack           read/post only in AGENT_CHANNEL_ID; posts must be thread replies with the prefix;
                  search only the triage query (channel + owner tag); every other Slack tool denied.
Exit 2 with a reason on stderr = deny. Any error also denies (fail closed).
"""
import json
import os
import re
import sys

ENV = os.environ
CHANNEL = ENV.get("AGENT_CHANNEL_ID", "")
OWNER = ENV.get("AGENT_OWNER_ID", "")
PREFIX = ENV.get("AGENT_PREFIX", "")              # e.g. "🤖 Dana's agent"
SLACK = "mcp__%s__" % ENV.get("AGENT_SLACK_SERVER", "slack-agents")
GH_REPO = ENV.get("AGENT_GH_REPO", "")            # e.g. "acme/app"; empty = not pinned


def paths(var):
    return [os.path.realpath(os.path.expanduser(p)) for p in ENV.get(var, "").split(":") if p]


READ_ROOTS = paths("AGENT_READ_ROOTS")
SECRET_PATHS = paths("AGENT_SECRET_PATHS")
BASH_PREFIXES = [p for p in ENV.get("AGENT_BASH_PREFIXES", "").split("\n") if p]
RISKY = {  # options that write files or run programs
    "git": r"--(output|ext-diff|textconv|exec|upload-pack|receive-pack|config|no-index)\b",
    "psql": r"(^|\s)(-o|--output|-L|--log-file|-f|--file)(\s|=|$)",
}


def deny(reason):
    print("agent-channel guard: " + reason, file=sys.stderr)
    sys.exit(2)


def under(p, root):
    return p == root or p.startswith(root + os.sep)


def check_path(raw, cwd, session_dir, tool, recursive):
    p = os.path.expanduser(raw or cwd)
    p = os.path.realpath(p if os.path.isabs(p) else os.path.join(cwd, p))
    if session_dir and under(p, session_dir):     # this run's own saved tool output
        return
    if not any(under(p, r) for r in READ_ROOTS):
        deny("%s outside the allowed folders: %s" % (tool, p))
    parts = p.split(os.sep)
    name = parts[-1]
    if ".git" in parts:
        deny("%s inside .git is not allowed" % tool)
    if name.startswith(".env") and not name.endswith((".example", ".sample", ".template")):
        deny("%s of an env file is not allowed" % tool)
    if name.endswith((".pem", ".key", ".p12", ".pfx")) or name.startswith("id_rsa"):
        deny("%s of a key file is not allowed" % tool)
    for s in SECRET_PATHS:
        if under(p, s):
            deny("%s inside a secret path is not allowed" % tool)
        if recursive and under(s, p) and p not in READ_ROOTS:
            # a search from the root itself is fine when the secret path is git-ignored (ripgrep skips it)
            deny("%s from %s would reach a secret path; search a narrower folder" % (tool, p))


def plain_command(cmd):
    """True if cmd has no shell operators, expansions or escapes outside single quotes."""
    if "\\" in cmd:
        return False
    quote = None
    for ch in cmd:
        if quote == "'":
            quote = None if ch == "'" else quote
        elif ch in "$`":
            return False
        elif quote == '"':
            quote = None if ch == '"' else quote
        elif ch in "'\"":
            quote = ch
        elif ch in ";&|<>()\n\r":
            return False
    return quote is None


def check_bash(cmd):
    c = cmd.strip()
    if not plain_command(c):
        deny("one plain command only: no ; & | < > ( ) $ ` or backslashes")
    if not any(c == p or c.startswith(p + " ") for p in BASH_PREFIXES):
        deny("Bash allows only: " + ", ".join(BASH_PREFIXES))
    tool = c.split()[0]
    if tool in RISKY and re.search(RISKY[tool], c):
        deny("this %s option writes files or runs programs" % tool)
    if tool == "gh" and GH_REPO:
        if re.search(r"(^|\s)(-R|--repo)(\s|=|$)", c):
            deny("gh is pinned to " + GH_REPO)
        for url in re.findall(r"github\.com/\S+", c):
            if not url.startswith("github.com/%s/" % GH_REPO):
                deny("gh is pinned to " + GH_REPO)


def check_slack(tool, ti):
    name = tool[len(SLACK):]
    if name in ("slack_read_thread", "slack_read_channel"):
        if ti.get("channel_id") != CHANNEL:
            deny("%s is limited to the agents channel" % name)
    elif name == "slack_send_message":
        if ti.get("channel_id") != CHANNEL:
            deny("send_message is limited to the agents channel")
        if not str(ti.get("thread_ts") or "").strip():
            deny("send_message must be a thread reply (thread_ts)")
        if ti.get("reply_broadcast") in (True, "true"):
            deny("reply_broadcast is not allowed")
        msg = str(ti.get("message") or "").lstrip()
        alt = ":robot_face: " + PREFIX.split(" ", 1)[-1]  # Slack may store the emoji as its shortcode
        if not PREFIX or not (msg.startswith(PREFIX + ":") or msg.startswith(alt + ":")):
            deny('every post must start with "%s:"' % PREFIX)
    elif name in ("slack_search_public_and_private", "slack_search_public"):
        kw = ti.get("keywords") or []
        kw = [kw] if isinstance(kw, str) else kw
        tokens = " ".join([str(ti.get("query") or ""), str(ti.get("filters") or "")] + [str(k) for k in kw]).split()
        chan = r"in:<#%s(\|[^>]*)?>" % re.escape(CHANNEL)
        ok = r"%s|<@%s>|(after|before|on):\S+|is:thread" % (chan, re.escape(OWNER))
        if not any(re.fullmatch(chan, t) for t in tokens) or not all(re.fullmatch(ok, t) for t in tokens):
            deny('search allows only filters "in:<#%s> <@%s>" (plus after:/before:)' % (CHANNEL, OWNER))
        if re.search(r"(^|,)\s*(im|mpim)\s*(,|$)", str(ti.get("channel_types") or "")):
            deny("search in DMs is not allowed")
    else:
        deny(name + " is not allowed")


def main():
    if not (CHANNEL and OWNER and READ_ROOTS):
        deny("AGENT_CHANNEL_ID, AGENT_OWNER_ID and AGENT_READ_ROOTS must be set (run.sh sets them)")
    data = json.load(sys.stdin)
    tool = data.get("tool_name", "")
    ti = data.get("tool_input") or {}
    cwd = data.get("cwd") or os.getcwd()
    tp = data.get("transcript_path") or ""
    session_dir = os.path.realpath(os.path.splitext(tp)[0]) if tp else ""
    if tool == "Read":
        check_path(ti.get("file_path"), cwd, session_dir, tool, recursive=False)
    elif tool == "Grep":
        check_path(ti.get("path"), cwd, session_dir, tool, recursive=True)
    elif tool == "Glob":
        check_path(ti.get("path"), cwd, session_dir, tool, recursive=False)
        pat = str(ti.get("pattern") or "")
        if pat.startswith(("/", "~")) or ".." in pat:
            check_path(pat.split("*")[0] or "/", cwd, session_dir, tool, recursive=False)
    elif tool == "Bash":
        check_bash(str(ti.get("command") or ""))
    elif tool.startswith(SLACK):
        check_slack(tool, ti)
    sys.exit(0)  # other tools: the --allowedTools list decides


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:  # fail closed
        deny("guard error: %s" % e)
