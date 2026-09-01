# Draft — upstream issue (NousResearch/hermes-agent)

**Title:** Proposal: bundled "Agents" dashboard plugin — agent roster + session timeline

## What

A dashboard plugin that presents Hermes activity **by agent** instead of by
session: identity cards (avatar, tagline from SOUL.md, skills, runs / active
time / cost per period, observed model fallback chain, system prompt) and a
chronological timeline where each session carries its agent's avatar, child
sessions nest under their parent, running sessions are flagged live, and a
click opens the transcript inline with tokens in/out and per-session cost.

Working implementation: https://github.com/dyvan/hermes-agent-roster
(screenshots below). Installable today as a user plugin; this issue asks
whether you'd take it as a bundled plugin, and in what shape.

## Why

- Hermes already *has* the data (state DB: tokens, costs, prompts,
  parent/child; profiles are de facto agents) but no surface presents it
  per-agent. The Sessions page is a flat list; Analytics aggregates globally.
- Multi-profile setups (personas, cron bots, platform gateways, subagents)
  currently have zero cross-profile visibility: each profile's sessions live
  in its own state.db, invisible from the dashboard.
- Related interest: #23462 (token counts on the Sessions page).

## How (design notes)

- Read-only over the product's own data: main `state.db` + every
  `profiles/*/state.db` (sqlite `mode=ro`), same session query as
  `/api/analytics/usage`. No schema changes, no new core code.
- Follows the plugin conventions: no-build-step IIFE on
  `__HERMES_PLUGIN_SDK__`, FastAPI router, hermetic unittest suite,
  theme tokens (works with custom dashboard themes).
- Optional and separable: an `ingest.jsonl` contract lets work done outside
  the session engine (no-agent cron scripts, external pipelines) appear in
  the same timeline. Happy to split this into a follow-up PR if you'd rather
  review the read-only core first.

## Proposed PR slices

1. `plugins/agent-roster/` — roster + timeline + transcripts over main DB and
   profiles (pure read).
2. External ingestion (`ingest.jsonl` + `POST /ingest`, live updates,
   grouping, image gallery).
3. Docs + example adapter.

Screenshots: [roster] [timeline with a live multi-agent run] [transcript]
