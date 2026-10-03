# N12 — Live-feed reader + dealer-blocker learnings in the RAG  (per-task spec)

- Task id: N12 (new, local; indexed in [`02-plan.md`](./02-plan.md))
- Status: **approved by the coordinator brief (2026-10-03)**; delivered in two PRs
- Backlog source: local (`.ai/specs`), the coordinator's brief is the source text
- Traces up to: [`01-spec.md`](./01-spec.md) §5 (`learnings`, `trader_behaviors`), §7.5 (embeddings), §7.8 (the feed as market data), §9 (untrusted text)

## Goal
Read the live feed the way a person reads the "On air · Live feed" panel of bazaar.causaprima.ai,
learn when a dealer is blocked for us (cooloff, hourly quota, sold out, locked) and what changed
(fees, levels, organiser notices, duel outcomes), store it as `learnings` the other agents recall,
and have the taker skip a blocked dealer instead of wasting a thread on it.

## Where the feed comes from
- The homepage panel is `GET /api/feed?limit=150` plus the public SSE stream
  (`/api/events/stream?scope=public`), each event rendered by type (the `EventLine` bundle). Our
  monitor already captures the same events (team stream ⊇ public stream) into `feed_events`.
- `bazaar-monitor` now runs on a laptop only. The Railway taker and maker read `feed_events` plus the
  public 500-event window every tick (`MarketFeed`), so they do not starve for recent events, but the
  archive stops growing while the laptop sleeps and the window holds only ~20 ticks. Fix inside an
  existing Railway loop: the taker writes the window it already reads into `feed_events`
  (`insert … on conflict do nothing`). No new service, no new game call.

## Scope
PR 1 (before Duels I): deterministic reader, `learnings` columns, store with `recall()`, the taker's
dealer path skips active blockers, the feed archive from the taker, `bazaar learnings` CLI.
PR 2: LLM pass over free text only (dealer words, organiser notices) through the subscription
runtime with Jev's model choice; fastembed embeddings; `trader_behaviors`; learnings into Jev's
`negotiation_move` state and the words context; maker venue choice from fee notices; MCP read tool.

Non-goals: no new Railway service, no new game route, nothing the agents send is added (blockers
only remove sends), feed text never changes behaviour beyond a validated structured learning.

## Acceptance criteria
1. Reader turns `persona.cooloff` / `persona.strike` / `thread.closed` (reason) / level / venue fee /
   clock / day / announcement / duel outcome events into validated `Learning`s (fixtures, unit tests).
2. Our own thread's `closed_reason` and an `open_thread` refusal (`cooloff` with `until_tick`,
   `persona_quota`, `sold_out`, `locked`) become blockers with an expiry tick.
3. `recall(subject, kind, tick)` returns only active, deduped learnings; Postgres when it answers,
   memory otherwise; a failure returns what memory holds (never raises into the tick).
4. The taker never opens a thread with a dealer under an active blocker for us, records a
   `dealer_skip` decision, and opens the next dealer instead; any recall error = today's behaviour.
5. The taker archives the feed window into `feed_events` (dedupe-safe, bounded by a statement timeout).
6. `bazaar learnings` prints learnings rebuilt from the captured feed and what `recall` returns.
7. Gate green: black, ruff, mypy, pytest (coverage ≥ 80 % on the new modules), sim smoke if present.

## Interfaces / data touched
- New: `src/bazaar_agent/learn/{model,reader,store,blockers}.py`, `tests/test_learn_*.py`,
  `tests/fixtures/learn/*.json`.
- Changed: `schema.sql` (learnings columns + dedupe index), `agents/taker.py` (skip + learn hooks),
  `agents/runtime.py` (`Recorder.last_error`, `MarketFeed` archive), `cli.py` (`bazaar learnings`,
  taker wiring).

## Risks & assumptions
- A wrong blocker makes the taker skip a dealer it could have used. Mitigations: blockers expire
  (cooloff at its `until_tick`, quota/sold-out at the end of the game hour, locked after a short
  recheck window), only blockers bound to OUR team id block, and any error fails open.
- "Game hour" = an integer step of `t_hours` (Friday: tick 98 ↔ t 1.633 at 60 s ticks).
