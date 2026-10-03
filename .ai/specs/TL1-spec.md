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
cost and cooldown are UNKNOWN. The answer is parsed tolerantly (`TallerResult`, extra=allow) and kept raw.

## Scoring context (docs/briefing.md)
Holdings never score by themselves; the pull does not score. Its worth: an uncommon that fills a missing page slot,
or stock for ladder sales to Pilar (L3) and Chato (L2), always above `your_value`.

## Acceptance criteria
1. `taller.plan_taller(me, open_offers, rules, catalog)` is pure and deterministic (no Jev, no LLM): only FREE
   spares (held − copies in our open offers − 1 per card; an offer that does not name its card counts against every
   card); commons first, then uncommons, never a rare or above; cards held more than `max_copies_kept` first, then
   the lowest set multiplier (/me `affinity`), then the lowest `your_value`; the three may be different cards.
2. `guardrails.check()` kind `taller`: refuses with `taller_enabled` false, with the hourly count unread or at
   `max_taller_per_game_hour` (shared ledger rows `taller`), anything but three copies of one common/uncommon
   rarity, and any input that is the last free copy of its card (every set). The kill switch and a tripped
   `taller` breaker hold it.
3. `TeamBazaar.taller(assets)`: one POST, never re-sent after a 429, a 5xx or a network error.
4. Taker: at most one conversion per tick, after its other sends; album first (fresh /me before, /me after);
   skipped on a short tick, at the hourly cap, and while `deploy_guard.verdict` (live duel deadlines within
   `deploy_guard_duel_ticks`, Market Test benches, scheduled events) is unsafe; never the team's accept slot.
5. Maker: while `taller_enabled`, no NEW ask for a spare common; an ask already open is never cancelled for it.
6. `bazaar taller [a b c] [--live]`: dry run by default, the plan with no ids, the same `taller.convert` path.
7. Tests with no network for each of the above.

## Out of scope / follow-ups
- The MCP tool (`taller` in `runtime/tools.py`) is not wired in this PR.
- First live use is by the coordinator, by hand: `uv run bazaar taller --live`, recording the real answer.
- No `bazaar_sim` route: `scripts/sim_smoke.py` does not run a conversion.
