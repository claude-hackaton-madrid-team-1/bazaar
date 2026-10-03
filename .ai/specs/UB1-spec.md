# UB1 — Unblock: guardrails that cost opportunities + an activity watchdog  (per-task spec)

- Task id: UB1
- Priority: P0 (Sat 3 Oct ~20:35, Omar)
- Backlog source: local (`.ai/specs`).
- Traces up to: [`01-spec.md`](./01-spec.md)  ·  Indexed in: [`02-plan.md`](./02-plan.md)

## Goal
Omar: "fix every guardrail that, like the human approval one, made us lose opportunities. Today the activity
guardrail is: if in 30 s we have no movement or anything live, something is blocked. Tomorrow, with 15 s ticks, if no
agent does anything, something is wrong." No settlement of ours from tick 1099 to 1201 (~50 min).

## Acceptance criteria (each MUST be testable)
- [ ] 1. A dealer buy is ranked (`strategy.guarded`) at the ladder's first rung for cash and the hour's spend; its top
  still answers to the price caps. Evidence: the live refusal "cash 58 - 67 < cash_floor 5" (MAL-09, ticks 1095-1166).
- [ ] 2. A later rung refused ONLY for cash or the hour's spend bids the most we may still commit when that is a
  distinct step above our last bid; any other refusal walks as before; never below the plan's start.
- [ ] 3. A guardrail walk (the next rung passes the official value, or our cash room) rests on that card for an hour (no reopen loop:
  RET-09/RET-10 with Los Pícaros, ticks 1205-1227).
- [ ] 4. The refusals of the last 600 ticks are ranked (count, value blocked) with a keep/fix verdict each; clear fixes
  land, judgement calls go to the coordinator.
- [ ] 5. Activity watchdog: per tick, team stall = no agent sent anything in max(1, ceil(`activity_stall_seconds` /
  tick seconds)) ticks; expected idle (kill switch, PAUSE, doors closed, Market Test, duel session) labelled, not a
  stall; on a stall a WARN per stalled agent with its top blocker, an `activity_stall` decision row, a learning, and
  `activity` / `stalled_for_ticks` / `top_blocker` / `idle_reason` in the taker's /health and /state. It never trades and
  never relaxes a guardrail. No new service, no extra game request.

## Out of scope
Changing Jev gates, `max_price_*`, `cash_floor`, `protect_page_sets`, breakers or approvals (judgement calls: listed in
the PR body for the coordinator).
