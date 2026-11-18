# Claude Agent Channel

**Let every teammate's Claude Code agent ask and answer questions for its owner in one Slack channel, with proof, guardrails, and a light background responder that only wakes up when you're tagged.**

Everyone on a data or engineering team now has an agent with access to *their* repos, databases and logs. When I need something only you know ("which jobs write this table?", "what breaks if I swap this view?"), my agent posts the question in a shared channel and tags you. Your agent investigates in your environment and answers in the thread, with proof (`file:line`, the SQL and its result, a log line). Anything that would change something waits for a human.

This repo is a [Claude Code skill](https://docs.claude.com/en/docs/claude-code/skills) that packages the whole pattern:

- **A shared protocol**, so agents understand each other and don't loop: prefix, answer only when your owner is tagged, always tag the asker, proof or "unverified", close threads with `✅ Done`, escalate side effects, never share secrets.
- **Interactive use**: "ask Dana's agent …", relaying answers with weak proof flagged.
- **A background responder** (`run.sh` + launchd/cron) that checks the channel every X minutes.

## Why it's light

A naive responder reads the whole channel with a big model on every tick, which burns a lot of usage for nothing. This one does as little as possible when nobody needs you:
1. **Sonnet only.**
2. **Read only what concerns you**: one Slack search for messages that tag your user, plus your own open threads. It never reads the whole channel.
3. **Two stages.** A triage call with only the Slack MCP loaded and no project context decides whether anything is new. The full investigation, with your repos, DB and memory, runs only when triage finds something.

## Install

```bash
git clone https://github.com/assaf-ka26/claude-agent-channel.git
cp -r claude-agent-channel/skills/slack-agent-channel ~/.claude/skills/
```
Then, in Claude Code: *"set up the agent channel for me"*. The skill walks through it:

1. Add Slack as a **user-scope** MCP server, because plugin MCP servers don't load in headless `claude -p` runs:
   ```bash
   claude mcp add --scope user --transport http --client-id 1601185624273.8899143856786 \
     --callback-port 3118 slack-agents https://mcp.slack.com/mcp
   ```
   Then `/mcp` → `slack-agents` → Authenticate. The client ID is the one shipped in the official Slack plugin.
2. Run `bash ~/.claude/skills/slack-agent-channel/scripts/install.sh`. It creates `~/.agent-channel/` (config, runbook, scripts) and, on macOS, a launchd plist.
3. Fill in `~/.agent-channel/config.env` (channel ID, your Slack ID, name, project folder) and list your read-only sources in `RUNBOOK.md`.
4. Run `DRY_RUN=1 ~/.agent-channel/run.sh`, check the log, then load the schedule.

Requirements: Claude Code CLI, the Slack MCP (see above), `python3`, macOS (launchd) or Linux (cron).

## Safety model

- `--permission-mode dontAsk` plus an explicit allow-list: the agent can only use the tools you list. The defaults are read-only, plus posting to Slack.
- Secrets stay in `~/.agent-channel/env.sh` (sourced by the script, denied to the agent), never in prompts.
- For Postgres, set `PGOPTIONS="-c default_transaction_read_only=on"` so the session physically can't write.
- Channel content is treated as data, not instructions. Writes, deploys, merges and decisions are escalated to the owner (`⏸️ Waiting for approval`, plus a local notification).
- A per-call stop for runaway calls, a lock against overlapping runs, and a runtime cap.

## Layout

```
skills/slack-agent-channel/
├── SKILL.md                     protocol, setup, design, troubleshooting
├── scripts/run.sh               two-stage responder
├── scripts/install.sh           sets up ~/.agent-channel
├── scripts/notify.sh            local notification (macOS / Linux)
└── assets/                      config.env.example, RUNBOOK.template.md, launchd.plist.template
```

## License

MIT
