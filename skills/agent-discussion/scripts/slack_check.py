#!/usr/bin/env python3
"""Free triage: was the owner tagged, or did one of the owner's open threads move? No LLM involved.

Calls the Slack Web API directly with a user token (SLACK_TOKEN in the environment, never argv):
  1. search.messages  "<@OWNER> after:<day before since>"  → keep hits in CHANNEL newer than since
  2. conversations.replies for each open thread, oldest=since → keep replies newer than since
Messages that start with the agent's own prefix are ignored (they're ours). In open threads, other
agents' messages (starting with 🤖 / :robot_face:) count only if they tag the owner; people's messages always count.

Prints exactly one line, the same contract as the LLM triage in run.sh:
  NEW: <comma-separated thread-root ts>   something to answer
  NEW: none                               nothing new
  NEW: unclear                            any error: run.sh keeps the checkpoint and retries next pass
Scopes needed on the user token: search:read, channels:history, groups:history.
"""
import argparse
import datetime
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

API = "https://slack.com/api/"


class SlackError(Exception):
    pass


def api(method, token, **params):
    url = API + method + "?" + urllib.parse.urlencode(params)
    for attempt in range(2):
        req = urllib.request.Request(url, headers={"Authorization": "Bearer " + token})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                data = json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt == 0:
                time.sleep(min(int(e.headers.get("Retry-After", "5")), 30))
                continue
            raise SlackError("%s: HTTP %s" % (method, e.code))
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise SlackError("%s: %s" % (method, e))
        if not data.get("ok"):
            raise SlackError("%s: %s" % (method, data.get("error", "not ok")))
        return data
    raise SlackError("%s: rate limited" % method)


def is_ours(text, prefixes):
    t = (text or "").lstrip()
    return any(t.startswith(p) for p in prefixes)


AGENT_MARKS = ("🤖", ":robot_face:")


def needs_owner(text, owner, prefixes):
    """An open-thread reply worth waking up for: not ours, and from a person or an agent that tags the owner."""
    if is_ours(text, prefixes):
        return False
    t = (text or "").lstrip()
    return not t.startswith(AGENT_MARKS) or ("<@%s>" % owner) in t


def root_ts(match):
    """Thread root of a search hit: thread_ts in the permalink, else the message itself."""
    q = urllib.parse.urlparse(match.get("permalink", "")).query
    return urllib.parse.parse_qs(q).get("thread_ts", [match.get("ts", "")])[0]


def mentions(token, channel, owner, since, prefixes, max_pages=5):
    day = (datetime.datetime.fromtimestamp(since, datetime.timezone.utc) - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
    roots = []
    for page in range(1, max_pages + 1):
        d = api("search.messages", token, query="<@%s> after:%s" % (owner, day),
                sort="timestamp", sort_dir="desc", count=100, page=page)
        msgs = d.get("messages", {})
        for m in msgs.get("matches", []):
            if (m.get("channel") or {}).get("id") != channel:
                continue  # other channels and DMs are filtered here and never reach a model
            if float(m.get("ts", 0)) <= since or is_ours(m.get("text"), prefixes):
                continue
            roots.append(root_ts(m))
        if page >= (msgs.get("paging") or {}).get("pages", 1):
            break
    return roots


def thread_moved(token, channel, owner, thread, since, prefixes):
    d = api("conversations.replies", token, channel=channel, ts=thread, oldest="%.6f" % since, limit=200)
    return any(float(m.get("ts", 0)) > since and needs_owner(m.get("text"), owner, prefixes)
               for m in d.get("messages", []))


def check(token, channel, owner, since, open_threads, prefixes):
    found = mentions(token, channel, owner, since, prefixes)
    found += [t for t in open_threads if thread_moved(token, channel, owner, t, since, prefixes)]
    return list(dict.fromkeys(t for t in found if t))  # dedupe, keep order


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--channel", required=True)
    ap.add_argument("--owner", required=True)
    ap.add_argument("--since", required=True, type=float)
    ap.add_argument("--open", default="", help="comma-separated open thread ts")
    ap.add_argument("--prefix", action="append", default=[], help="text our own messages start with")
    a = ap.parse_args(argv)
    token = os.environ.get("SLACK_TOKEN", "")
    try:
        if not token:
            raise SlackError("SLACK_TOKEN not set")
        new = check(token, a.channel, a.owner, a.since, [t for t in a.open.split(",") if t], a.prefix)
    except Exception as e:  # fail closed: never report "none" on an error
        print("slack_check: %s" % e, file=sys.stderr)
        print("NEW: unclear")
        return
    print("NEW: " + (",".join(new) if new else "none"))


if __name__ == "__main__":
    main()
