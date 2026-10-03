# TL1 — El Taller: spare copies become one card of the next rarity  (per-task spec)

- Task id: TL1
- Priority: P0 (Omar's hard rule and guardrail, Sat 3 Oct 19:37 CEST)
- Status: 🟡 in review (feat/taller)
- Backlog source: local (`.ai/specs`). Indexed in: [`02-plan.md`](./02-plan.md)

## Why (Omar, verbatim, Sat 3 Oct 19:37)
"(NEW HARDRULE AND GUARDRAIL) sell and use El Taller to bring new cards. And keep only x1 (or two) of each card,
because the more cards we hold, the lower that card's floor/value. Keeping x3 or x2 is not necessary. So sell those
into El Taller."

## The game fact (the only source)
`GET /api/levels`, id `taller`, kind `taller`, active since game hour 7.2: "POST /api/taller {"assets": [a, b, c]}:
three spare copies of one rarity (you keep at least one of each card) become one card of the next rarity. The pull
is luck, shown and never scored." Not in `docs/api/openapi.json`, the kit or the live OpenAPI: the answer shape,
cost and cooldown are UNKNOWN. The request goes through the kit's generic `call` (README §6: a level's route is
one `b.call("POST", "/api/...", {...})` away), the answer is recorded as received (`executions`) and read by
`agents.taller.pulled`, which takes any shape and never raises.

## Scoring context (docs/briefing.md)
Holdings never score by themselves; the pull does not score. Its worth: an uncommon that fills a missing page slot,
or stock for ladder sales to Pilar (L3) and Chato (L2), always above `your_value`.

## Design (merged with SA1, which landed on main first)
SA1 (`agents/taller.py`, the taker's `_taller`, `bazaar taller`, kind `taller` in `guardrails.check`) is the base:
level gating through the news sentinel's `/api/levels` read, the triple ranking (a pull that may fill a missing
slot, the highest dealer level that buys the result, the cheapest to give up) and the score impact of each given
copy at 0. TL1 hardens it after the #236 reviews.

## Acceptance criteria
1. Only FREE copies go in: our open offers (board and thread, read again right before the craft) and the accepts
   still settling (`settling`: `sell:<asset>`, a plain ref takes one copy, an unnamed `team:<thread>` holds every
   craft that tick) and, in the taker, copies our offers gave in the last UNSETTLED_TICKS ticks are never free;
   `max_copies_kept` (2) free copies of every card always stay, on any set; commons and uncommons only; never below
   `cash_floor`.
2. One hourly cap for every process: shared ledger rows (kind `spend`, price 0, item `taller:<refs>`; the table
   takes no other kind), booked before the send; `max_taller_per_game_hour` = 1 until a first real answer is seen.
3. The taker: at most one craft per tick, after its other sends, never the accept slot; never on a short tick, at
   the cap (no request), near a duel deadline or a Market Test (`deploy_guard.verdict`); a craft not sent waits 10
   ticks (a blocked guard: until its next safe tick); a cash drop trips the `taller` breaker for every process.
4. `bazaar taller`: no ids lists the ranked triples; three ids go through the same `craft_one` path (dry run unless
   `--live`), with the same holds.
5. The maker posts no new ask for a spare common, nor for an uncommon in the two ticks after a craft; open asks are
   never cancelled for it.
6. No craft in a process's first UNSETTLED_TICKS ticks (it has not seen our offers yet).
7. Tests with no network for each of the above, including the shared ledger's kind check on the Postgres path.

## Out of scope / follow-ups
- The MCP tool is not wired.
- First live use: the coordinator, by hand: `uv run bazaar taller` (ranked triples), then `uv run bazaar taller a b c
  --live`; the raw answer lands in `executions`.
- `bazaar_sim` has a minimal `POST /api/taller` (our model, not the real answer).
