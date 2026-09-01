# Changelog

## 0.1.0 — 2026-09-02

Initial release.

- **Agent roster** — agents discovered from real session history: the main
  Hermes state DB (grouped by system prompt identity) and every profile under
  `~/.hermes/profiles/` (one agent per profile, read-only). Cards show
  runs / active time / cost for today, this week, or this month; card details
  reveal the models actually used (observed fallback chain), the system
  prompt (from sessions, or the profile's SOUL.md), and an identity editor
  (name, tagline, skills) stored locally.
- **Session timeline** — chronological feed grouped by day: agent avatar,
  one-line title, model badge, cost and duration per session; child sessions
  nest under their parent, thread-style; running sessions show a live badge.
- **Inline transcripts** — chat view with tool calls folded into chips, and a
  sticky header (tokens in/out, cost, model, parent/child navigation). Works
  across the main DB, profile DBs, and ingested records.
- **External ingestion** — work done outside the Hermes session engine
  (no-agent cron scripts, pipelines, standalone bots) appears via
  `ingest.jsonl` records (or authenticated `POST /ingest`): stable ids update
  in place (live runs), `group_profiles` nests profile sessions under an
  orchestrating run, `images` adds a click-to-zoom gallery served exclusively
  from declared paths.
- **Example adapter** — `examples/pipeline-bridge/`: a read-only filesystem
  watcher emitting ingest records for a wave-based multi-agent pipeline,
  without touching the pipeline itself.
