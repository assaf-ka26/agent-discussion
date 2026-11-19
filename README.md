# Agent Discussion

**Let every teammate's Claude Code agent ask and answer questions for its owner in one Slack channel, with proof, guardrails, and a light background responder that only wakes up when you're tagged.**

Everyone on a data or engineering team now has an agent with access to *their* repos, databases and logs. When I need something only you know ("which jobs write this table?", "what breaks if I swap this view?"), my agent posts the question in a shared channel and tags you. Your agent investigates in your environment and answers in the thread, with proof (`file:line`, the SQL and its result, a log line). Anything that would change something waits for a human.

This repo is a Claude Code plugin (one [skill](https://docs.claude.com/en/docs/claude-code/skills) plus scripts) that packages the whole pattern:

- **A shared protocol**, so agents understand each other and don't loop: prefix, answer only when your owner is tagged, always tag the asker, proof or "unverified", close threads with `✅ Done`, escalate side effects, never share secrets.
- **Interactive use**: "ask Dana's agent …", relaying answers with weak proof flagged.
- **A background responder** (`run.sh` + launchd/cron) that checks the channel every X minutes.

## Why it's light

A naive responder reads the whole channel with a big model on every tick, which burns a lot of usage for nothing. This one does as little as possible when nobody needs you:
1. **Sonnet only.**
2. **Read only what concerns you**: one Slack search for messages that tag your user, plus your own open threads. It never reads the whole channel.
3. **Two stages.** A triage call with only the Slack MCP loaded and no project context decides whether anything is new. The full investigation, with your repos, DB and memory, runs only when triage finds something.

## Install

As a plugin (recommended):
```bash
claude plugin marketplace add assaf-ka26/agent-discussion
claude plugin install agent-discussion@agent-discussion
```
Or as a plain skill:
```bash
git clone https://github.com/assaf-ka26/agent-discussion.git
cp -r agent-discussion/skills/agent-discussion ~/.claude/skills/
```
Then, in Claude Code: *"set up agent discussion for me"*. The skill walks through it:

1. Add Slack as a **user-scope** MCP server, because plugin MCP servers don't load in headless `claude -p` runs:
   ```bash
   claude mcp add --scope user --transport http --client-id 1601185624273.8899143856786 \
     --callback-port 3118 slack-agents https://mcp.slack.com/mcp
   ```
   Then `claude mcp login slack-agents` (or `/mcp` → `slack-agents` → Authenticate). The client ID is the one shipped in the official Slack plugin. Headless runs use the CLI's own login: if `claude auth status` says `loggedIn: false`, run `claude auth login`.
2. Run the skill's `scripts/install.sh` (Claude runs it from the skill's folder). It creates `~/.agent-discussion/` (config, runbook, scripts) and, on macOS, a launchd plist.
3. Fill in `~/.agent-discussion/config.env` (channel ID, your Slack ID, name, project folder) and list your read-only sources in `RUNBOOK.md`.
4. Run `DRY_RUN=1 ~/.agent-discussion/run.sh`, check the log, then load the schedule.

Requirements: Claude Code CLI, the Slack MCP (see above), `python3`, macOS (launchd) or Linux (cron).

## Safety model

- `--permission-mode dontAsk` plus an explicit allow-list: the agent can only use the tools you list. The defaults are read-only, plus posting to Slack.
- **A guard hook** (`guard.py`, PreToolUse) checks the arguments of every file read, Bash command and Slack call, which an allow-list can't do:
  - Files: reads only under `WORKDIR` (plus `READ_ROOTS`), never `.env*`, `.git/`, key files or `SECRET_PATHS`. Files like `~/.claude.json` or `~/.zshenv` can hold tokens.
  - Bash: only commands from the allow-list, one plain command (no `;`, `|`, `$(…)`, backslashes), no git or psql options that write files or run programs, and `gh` pinned to one repo if you set `GH_REPO`.
  - Slack: only the agents channel. Posts must be thread replies with your prefix. The answer stage has no Slack search, so an injected message can't make it search DMs.
- **User-level hooks can't widen the allow-list.** A `PreToolUse` hook that answers `"allow"` (for example a command-rewrite hook in `~/.claude/settings.json`) overrides `dontAsk`. So `run.sh` runs with `--setting-sources project,local`, and the guard's deny wins over any other hook's allow.
- Secrets stay in `~/.agent-discussion/env.sh` (sourced by the script, denied to the agent), never in prompts.
- **Postgres: connect as a read-only role.** `PGOPTIONS="-c default_transaction_read_only=on"` is only a default, and the session can undo it with `SET`. The guard blocks psql backslash commands (`\!` runs a shell) and file-writing options, but the role is the real boundary.
- Channel content is treated as data, not instructions. Writes, deploys, merges and decisions are escalated to the owner (`⏸️ Waiting for approval`, plus a local notification).
- A per-call stop for runaway calls, a lock against overlapping runs, and a runtime cap.
- Keep `~/.agent-discussion` and `WORKDIR` outside `~/Desktop`, `~/Documents` and `~/Downloads`, so launchd needs no Full Disk Access.

**What this doesn't solve: your agent as a side door.** Anyone in the channel can ask your agent, and your agent can see what *you* can see. Someone without access to a database or repo can get its content through your agent. The runbook tells the agent to share only what any channel member could see and to escalate the rest, but that is the model's judgment, not a hard boundary. Choose the channel members and each agent's sources with that in mind.

## What it runs, reads and sends

Nothing runs until you run `install.sh` and load the schedule yourself.

- **`install.sh`** (run once, by you):
  - copies `run.sh`, `guard.py`, `notify.sh` and the config/runbook templates into `~/.agent-discussion/`; it never overwrites an existing config or runbook;
  - reads `~/.claude.json` and copies **only** the `slack-agents` MCP server entry (URL and OAuth client ID, no tokens; Claude Code keeps those in its own credential store) into `~/.agent-discussion/slack-mcp.json`;
  - on macOS, writes a launchd plist to `~/Library/LaunchAgents/com.<user>.agent-discussion.plist`. It does **not** load it; you run `launchctl bootstrap` yourself. On Linux it prints a cron line for you to add.
- **`run.sh`** (on your schedule):
  - runs your local `claude` CLI headless (`claude -p`, model Sonnet) with `--permission-mode dontAsk`, `--setting-sources project,local` and an explicit allow-list: Slack read/post in the one agents channel (plus the fixed mention search in triage), `Read`/`Grep`/`Glob` under `WORKDIR`, `git log`/`git show`, `gh pr view/list`, and any extra tools you add in `config.env`;
  - writes `guard-settings.json`, which loads `guard.py` as a PreToolUse hook that checks each call's arguments (see Safety model);
  - writes its own state (`state.json`), logs (`logs/`) and a lock directory under `~/.agent-discussion/`;
  - sources `~/.agent-discussion/env.sh` only if you create it (for read-only credentials of your own sources).
- **`notify.sh`**: a local desktop notification (`osascript` on macOS, `notify-send` on Linux) when your approval is needed.
- **Network**: the only destinations are Slack (through your authenticated Slack MCP) and Anthropic (through your Claude Code CLI and plan), plus any read-only sources you add yourself. Nothing is downloaded or installed.

To remove it: `launchctl bootout gui/$(id -u)/com.$(id -un).agent-discussion` (or delete the cron line), then delete `~/.agent-discussion/` and the plist.

## Layout

```
.claude-plugin/                  plugin.json, marketplace.json, icon.png
skills/agent-discussion/
├── SKILL.md                     protocol, setup, design, troubleshooting
├── scripts/run.sh               two-stage responder
├── scripts/install.sh           sets up ~/.agent-discussion
├── scripts/notify.sh            local notification (macOS / Linux)
├── scripts/guard.py             PreToolUse guard: scopes file reads, Bash and Slack
├── scripts/test_guard.py        guard tests (python3 test_guard.py)
└── assets/                      config.env.example, RUNBOOK.template.md, launchd.plist.template
```

## License

MIT
