#!/usr/bin/env python3
"""Tests for slack_check.py with Slack mocked. Run: python3 test_slack_check.py"""
import contextlib
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import slack_check as sc  # noqa: E402

CH, OTHER, OWNER = "C111", "C222", "U999"
SINCE = 1700000000.0
PREFIXES = ["🤖 Dana's agent", ":robot_face: Dana's agent"]
results = []


def ok(name, cond):
    results.append(cond)
    print(("PASS " if cond else "FAIL ") + name)


def fake(search=(), replies=None, error=None, pages=1):
    replies = replies or {}

    def api(method, token, **p):
        if error and method in error:
            raise sc.SlackError(method + ": " + error[method])
        if method == "search.messages":
            return {"ok": True, "messages": {"matches": list(search), "paging": {"pages": pages}}}
        if method == "conversations.replies":
            return {"ok": True, "messages": replies.get(p["ts"], [])}
        raise AssertionError(method)
    return api


def hit(ts, text="<@U999> question?", ch=CH, thread=None):
    link = "https://x.slack.com/archives/%s/p%s" % (ch, ts.replace(".", ""))
    if thread:
        link += "?thread_ts=%s&cid=%s" % (thread, ch)
    return {"ts": ts, "text": text, "channel": {"id": ch}, "permalink": link}


def run(api, open_threads=(), token="xoxp-test"):
    sc.api = api
    os.environ["SLACK_TOKEN"] = token
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        sc.main(["--channel", CH, "--owner", OWNER, "--since", str(SINCE), "--open", ",".join(open_threads)]
                + [x for p in PREFIXES for x in ("--prefix", p)])
    return out.getvalue().strip()


new, old = "1700000100.000100", "1699999000.000100"

ok("nothing at all -> none", run(fake()) == "NEW: none")
ok("top-level mention -> its own ts", run(fake([hit(new)])) == "NEW: " + new)
ok("mention in a thread -> thread root from permalink",
   run(fake([hit(new, thread="1699990000.000001")])) == "NEW: 1699990000.000001")
ok("mention older than since -> none", run(fake([hit(old)])) == "NEW: none")
ok("mention in another channel or DM -> none", run(fake([hit(new, ch=OTHER)])) == "NEW: none")
ok("our own prefixed message -> none", run(fake([hit(new, text="🤖 Dana's agent: <@U999> done")])) == "NEW: none")
ok("our own message as :robot_face: -> none",
   run(fake([hit(new, text=":robot_face: Dana's agent: hi <@U999>")])) == "NEW: none")
ok("other agent's prefix still counts",
   run(fake([hit(new, text="🤖 Omer's agent: <@U999> q")])) == "NEW: " + new)
ok("open thread with a new reply -> listed",
   run(fake(replies={"1699990000.000001": [{"ts": "1699990000.000001", "text": "root"},
                                          {"ts": new, "text": "follow-up"}]}),
       ["1699990000.000001"]) == "NEW: 1699990000.000001")
ok("open thread with only our reply -> none",
   run(fake(replies={"1699990000.000001": [{"ts": new, "text": "🤖 Dana's agent: answer"}]}),
       ["1699990000.000001"]) == "NEW: none")
ok("open thread, root is old and nothing new -> none",
   run(fake(replies={"1699990000.000001": [{"ts": "1699990000.000001", "text": "root"}]}),
       ["1699990000.000001"]) == "NEW: none")
ok("open thread: other agent's reply without my tag -> none",
   run(fake(replies={"1699990000.000001": [{"ts": new, "text": "🤖 Omer's agent: thanks, noted"}]}),
       ["1699990000.000001"]) == "NEW: none")
ok("open thread: other agent's reply as :robot_face: without my tag -> none",
   run(fake(replies={"1699990000.000001": [{"ts": new, "text": ":robot_face: Omer's agent: ✅ Done"}]}),
       ["1699990000.000001"]) == "NEW: none")
ok("open thread: other agent's reply that tags me -> listed",
   run(fake(replies={"1699990000.000001": [{"ts": new, "text": "🤖 Omer's agent: <@U999> answer"}]}),
       ["1699990000.000001"]) == "NEW: 1699990000.000001")
ok("open thread: a person's reply without a tag (e.g. owner approval) -> listed",
   run(fake(replies={"1699990000.000001": [{"ts": new, "text": "approved, go ahead"}]}),
       ["1699990000.000001"]) == "NEW: 1699990000.000001")
ok("same thread from search and open list -> deduped",
   run(fake([hit(new, thread="1699990000.000001")],
            replies={"1699990000.000001": [{"ts": new, "text": "<@U999> q"}]}),
       ["1699990000.000001"]) == "NEW: 1699990000.000001")
ok("search error -> unclear", run(fake(error={"search.messages": "missing_scope"})) == "NEW: unclear")
ok("replies error -> unclear",
   run(fake(error={"conversations.replies": "channel_not_found"}), ["1699990000.000001"]) == "NEW: unclear")
ok("no token -> unclear", run(fake([hit(new)]), token="") == "NEW: unclear")
ok("root_ts without permalink thread -> ts", sc.root_ts({"ts": new, "permalink": ""}) == new)

print("%d/%d passed" % (sum(results), len(results)))
sys.exit(0 if all(results) else 1)
