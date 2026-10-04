# Documentation index

A pointer list; nothing here moves files. The [repository README](../README.md) covers setup and the component map.
Files marked "Saturday draft" or dated are historical: they record what was true then.

## Pitch
- [pitch/](pitch/README.md): the 3-minute deck and its reading order for judges (deck, story, claims ledger, evidence, demo, Q&A, charts).
- [pitch-notes.md](pitch-notes.md): short evidence-backed paragraphs per task (strategy, how we built it, learnings).

## Run it
- [operations.md](operations.md): real game and simulator setup, shared database, agents and modes, pause, models, Railway, exact operator proposals.
- [services.md](services.md): every live URL and the HTTP, WebSocket and MCP contract each one speaks.
- [briefing.md](briefing.md): the game in one place (rules, scoring, dealers, duels, our market), with sources.
- Executable policy lives at the repo root: [GUARDRAILS.md](../GUARDRAILS.md), [STRATEGY.md](../STRATEGY.md), [RUNTIME.md](../RUNTIME.md).

## Why
- [decisions.md](decisions.md): every strategy or architecture decision, in order, with date, evidence and outcome.
- [adr/](adr/0001-agent-behavior-tracing.md): ADR 0001, one Phoenix trace per agent turn.
- [points-ledger.md](points-ledger.md): which moves earned and lost official points, Fri 2 Oct to Sat 3 Oct close.
- Strategy itself is [STRATEGY.md](../STRATEGY.md) (parsed at runtime; `uv run bazaar strategy`).

## History
- [night/](night/README.md): night shift reports, Fri 2 → Sat 3 Oct ([summary](night/SUMMARY.md), [index of PRs](night/INDEX.md)).
- [research/2026-10-04/](research/2026-10-04/): Sunday investigations (Market Test bench, card hunt, dealing and duel fixes, retry loop, egg hunter).
- [transcripts/](transcripts/): kickoff talk, Saturday and Sunday morning memos.
- [issues-archive.md](issues-archive.md): closed GitHub issues kept verbatim after the move to `.ai/specs/`.

## Reference
- [api/](api/openapi.json): OpenAPI for the game, `openapi.json` enriched by Team 1 and `openapi.server.json` as the server publishes it.
- [observability.md](observability.md): replaying a negotiation in Phoenix.
- [agent-harness.md](agent-harness.md): the Claude Code contract, skills and commands generated from `.ai/`.
- [architecture.html](architecture.html): the architecture page (generated from `architecture.status.json`; download to view).
- [architecture.excalidraw](architecture.excalidraw): hand-drawn diagram; static, may lag the code.
