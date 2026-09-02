# Agent Roster — Hermes Dashboard Plugin

Agent identity cards and a chronological session timeline for the
[Hermes](https://github.com/NousResearch/hermes-agent) dashboard:
**who worked, when, on what — and what it cost.**

## What it does

- **Agent roster** — agents are discovered automatically from your real
  session history (grouped by system prompt identity, falling back to
  source + model). Each card shows avatar, name, tagline, skills, and
  runs / active time / cost for **today, this week, or this month**.
  Flip a card to see the models the agent actually used (most used first),
  its full system prompt, and to customize its identity (name, tagline,
  skills) — stored locally in `~/.hermes/plugin-data/agent-roster/identities.json`.
- **Session timeline** — a chronological feed grouped by day. Each row
  carries the agent's avatar, a one-line title, model badge, message/tool
  counts, and cost + duration. Child sessions (delegated work) are nested
  under their parent, thread-style.
- **Inline transcripts** — click a session to open it right below as a
  readable chat view: user/assistant bubbles, tool calls folded into chips
  (click to reveal output), and a sticky header with tokens in/out, cost,
  model, and parent/child navigation. One session open at a time.

No external services, no network calls, no build step. Everything is read
from the local Hermes state DB and rendered with the dashboard plugin SDK.

## Install

```bash
git clone https://github.com/dyvan/hermes-agent-roster ~/.hermes/plugins/agent-roster
```

Then restart `hermes dashboard` (or hit `/api/dashboard/plugins/rescan`).
An **Agents** tab appears next to Sessions.

## How agents are identified

A Hermes "agent" here is a distinct system prompt: sessions sharing the
same `system_prompt_hash` belong to the same agent (sessions without a
prompt hash are grouped by source + model). This works out of the box for
any Hermes install — personas, cron bots, platform gateways and subagents
each surface as their own card — and you can rename or describe any of
them from the card itself.

## Ingesting external work (cron scripts, pipelines, standalone bots)

Work that doesn't go through the Hermes session engine (a `no-agent` cron
script, a CI pipeline, a bot calling models directly) can still appear in
the roster and timeline. Append one JSON object per line to
`~/.hermes/plugin-data/agent-roster/ingest.jsonl`:

```bash
echo '{"agent":"ql-runner","title":"nightly pipeline — card E003",
  "started_at":'"$(date +%s)"',"duration_seconds":312,"model":"gemma-4-e4b",
  "cost_usd":0.004,"input_tokens":18200,"output_tokens":2400,
  "messages":[{"role":"user","content":"brief"},{"role":"assistant","content":"result"}]}' \
  >> ~/.hermes/plugin-data/agent-roster/ingest.jsonl
```

Only `agent` and `started_at` (unix epoch seconds) are required. Optional:
`title`, `ended_at` or `duration_seconds`, `model`, `cost_usd`,
`input_tokens`, `output_tokens`, `tool_calls`, `parent_id` (nests the entry
under another ingested record), `error` (flags the row red), `id`, and
`messages` (shown as the inline transcript). An authenticated
`POST /api/plugins/agent-roster/ingest` accepts the same records.

## Development

```bash
node --check dashboard/dist/index.js
python3 -m py_compile dashboard/plugin_api.py
python3 -m unittest discover -s tests
```

The frontend is a plain IIFE (`dashboard/dist/index.js`) using
`window.__HERMES_PLUGIN_SDK__` — the same no-build-step convention as the
bundled `kanban` plugin. The backend (`dashboard/plugin_api.py`) is a
FastAPI router mounted at `/api/plugins/agent-roster/`; tests are hermetic
(no DB, no network).

## License

MIT — © Yvan Dervillier ([@dyvan](https://github.com/dyvan))
