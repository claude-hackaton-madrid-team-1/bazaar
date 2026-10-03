# N13 — Real-time holdings + card catalog in Postgres  (per-task spec)

- Task id: N13 (`.ai/specs/02-plan.md`, task index)
- Status: **APPROVED** — coordinator brief, 2026-10-03 (Omar: "for the agent to know which cards it has,
  it is super important that we have it in the DB, so the information is always read in real time and the
  agents have the latest information from the MCP server")
- Backlog source: local (.ai/specs)
- Traces up to: [`01-spec.md`](./01-spec.md) §5 (memory schema: `cards`, `snapshots`) · AGENTS.md "Album first"
- Indexed in: [`02-plan.md`](./02-plan.md)

## Goal
Every agent (taker, maker, duels, bazaar-mcp, the CLI) decides on what we hold right now, read from
Postgres while it is provably current, and the card catalog lives in Postgres too. Our processes share one
key's 5 req/s, so the shared snapshot cuts `/api/me` calls without ever deciding on a stale view.

## Scope / non-goals
- In: `cards` filled from `/api/catalog` (on a release and at most every N ticks); a `me_snapshots`
  table keyed by (team, tick) written by every `/me` read; freshness rules (tick, epoch, no thread message
  this tick, max age) with a live read on any doubt; one reader per team per tick (advisory lock); every
  send of ours invalidates (before and after); the acting agent re-reads `/me` after a deal; MCP tools
  `status`, `holdings`, `cards`; `bazaar status` shows the snapshot's tick and age; docs.
- Not in: a new service; a new Railway variable; changes to the strategy or the guardrails' limits; the
  SSE stream as an invalidation source (the monitor runs on a laptop only); pruning old snapshots.

## Acceptance criteria (each MUST be testable)
1. `cards` holds every card of every set from the catalog, rewritten on a set release and at most every 10
   ticks; an older tick never overwrites a newer row.
2. `me_snapshots` (team, tick) holds cash, level, cards with asset ids, duplicates, sealed packs, pages,
   affinity, score and the payload; every `/me` read by an agent, the MCP server or the CLI upserts it.
3. A reader uses the stored snapshot only when it is from its current tick, no send of ours happened since,
   no thread message of ours went out this tick, and it is younger than `holdings_max_age_s`; otherwise it
   reads `/me` live (and stores it). Postgres down, unknown team or clock, unreadable row → live.
4. A deal invalidates: every send that can move cards or cash bumps the epoch before and after; after a
   live accept or a dealer thread ending in a deal, the taker re-reads `/me` and decides on it.
5. Two agents in the same tick: one `/me` call (single flight), and two writers never move a row backwards.
6. MCP: `status`, `holdings`, `strategy` and the write tools answer through the same rule, with the
   snapshot's source, tick and age; a `holdings` and a `cards` tool exist; no key ever leaves a tool.
7. `bazaar status` prints the snapshot tick/age (or "live" and why); README, `docs/services.md`,
   `docs/architecture.status.json` (`db_cards`, STATE) and `.ai/memory.md` updated.
8. Measured on the simulator: `/me` calls per tick before vs after; the gate (ruff, format, black, mypy,
   pytest, `scripts/sim_smoke.py`) is green.

## Interfaces / data touched
- New: `src/bazaar_agent/holdings.py`, `src/bazaar_agent/catalog_db.py`, `tests/test_holdings.py`,
  `tests/test_holdings_db.py`.
- Schema (`sql/schema.sql`, additive): `me_snapshots`, `holdings_state`, `cards` columns `set_name`, `page`,
  `hidden`, `flavour`; index `cards_set`.
- `sdk.TrackedBazaar` (every `team_client()`), `agents/runtime.read_snapshot`, taker and maker,
  `runtime/backend` (`holdings`, `me_now`, `status`, `holdings()`, `cards()`, `strategy`), `runtime/actions._base`,
  `runtime/tools` (`holdings`, `cards`), `cli status` and `_run_agent`, `pgconn.connect(connect_timeout_s)`.
- GUARDRAILS.md: `holdings_from_db` (kill switch for the shared answers), `holdings_max_age_s`.

## Risks & assumptions
- Highest risk: a stored snapshot served after a change we did not cause or see. Friday's feed shows dealer
  answers and their settlements at the tick boundary (27/27, pr-reviewer on #105), which the tick rule covers;
  the calm rule (no cache in a tick with a thread message of ours), the after-deal re-read and the 5 s age
  are the backstops; `holdings_from_db = false` turns it off.
- A send must never wait for the holdings: the tracker has its own connection, opened in the background,
  and a 0.2 s lock budget (review of #105: the first version could hold a send for seconds).
- A simulator sharing a database with the game: rows carry their world (review of #105).
- A lost epoch bump (Postgres blip during a send) is bounded by the age backstop; the send itself is never
  blocked by the tracker.
- One epoch for every team in the database: in a simulator database with several sim teams, a send by one
  invalidates the others too (more live reads, never a stale one).
