# Draft — Discord #plugins-skills-and-skins

> **agent-roster — see your agents as a team, not a session list**
>
> I built a dashboard plugin that answers one question at a glance: **who
> worked, when, on what — and what it cost.**
>
> 🃏 **Agent cards** — agents are discovered automatically from your real
> session history: every Hermes **profile** becomes an agent (its SOUL.md is
> the default tagline, its sessions bring real tokens/costs and the observed
> model fallback chain), and so does every distinct system prompt in the main
> DB. Flip a card for the full system prompt, or customize name/tagline/skills.
>
> 🧵 **Session timeline** — a chronological feed with the agent's avatar on
> each row, child sessions nested thread-style, live `running` badges, and
> click-to-open **chat transcripts** with tokens in/out and per-session cost.
>
> 🔌 **External work too** — cron scripts in no-agent mode, CI jobs, or bots
> calling models directly can appear in the same timeline by appending one
> JSON line to `ingest.jsonl` (stable ids = live updates; declared `images`
> show as a gallery; `group_profiles` nests the profile sessions an
> orchestrator triggered). Ships with a read-only example adapter for a
> multi-agent image pipeline.
>
> No build step, no external services, no network calls — plain IIFE on the
> plugin SDK + a FastAPI router, everything read from your local Hermes data.
>
> Install:
> ```
> git clone https://github.com/dyvan/hermes-agent-roster ~/.hermes/plugins/agent-roster
> hermes plugins enable agent-roster
> ```
> Repo: https://github.com/dyvan/hermes-agent-roster — feedback very welcome!

(Attach: screenshot of the roster + timeline with a live run, GIF of opening
a transcript.)
