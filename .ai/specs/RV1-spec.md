# RV1 — Rival board: each rival's strengths, what it wants vs what we hold, our move  (per-task spec)

- Task id: RV1 (coordinator brief, 2026-10-03: Omar asked for a Rivals view with "the weakness and strength of the
  rivals, statistics of what they want vs what we have, and a live strategy to negotiate")
- Status: 🔵 PR #224 (this repo) + bazaar-live #46 (the screen)
- Backlog source: local (`.ai/specs`). Indexed in: [`02-plan.md`](./02-plan.md)

## Goal
One read-only row per other team that a human (DataGrip) or bazaar-live's Rivals screen can read, built only from
what we already store, with a deterministic move that respects fair play and never helps a close rival.

## Acceptance criteria
- [ ] 1. `rival_board` view: rank and score trend over the last 60 ticks (`leaderboard_snapshots`, world `real`).
- [ ] 2. Strength and weakness codes against us: negotiating, market, pages (both ways), dealer ladder deals, venue
  trades, climbing / falling, no venue trades, needs cards.
- [ ] 3. They want (bids and swap wants in the last 60 feed ticks, not bought since), they have (asks and swap gives,
  not sold since), our spares they want (a copy in an open offer of ours is not spare), their copies of page cards we
  miss, a match count; a price only from a live listing (not lapsed, not cancelled, open to anyone or to us).
- [ ] 4. A deterministic move (swap, sell, buy; hold, watch) with our gain and theirs as estimates (their side at
  book × the top multiplier; accepting their bid or ask we pay the venue's fee: El Rastro 500 bps + 1 P, any other
  venue at the caps 1000 bps + 5 P); never a move with our gain <= 0; a guarded team (top 5,
  within 3 ranks of us, or any team while our rank is unknown) only when our gain is at least twice theirs.
- [ ] 5. Hostile feed payloads never raise and never reach a row; refs must be catalog cards.
- [ ] 6. Applied by `init_schema` without ever failing it: replaced only by a newer version (view comment), a 2 s
  lock wait, a warning on any error. Readable by `bazaar_team_ro`; bazaar-live reads it through `show.game_rivals`.
- [ ] 7. Tests on the local Postgres (throwaway schemas) for every criterion above.
- Out of scope: the screen (bazaar-live #46); `team_affinity`, `team_matrix` and `team_buyer_rank` as inputs (a later
  version); GUARDRAILS.md's `team_desk_never_trade` (the view cannot read it; the team desk enforces it at write time, and
  the board's own guard covers the same top-5 and near teams).
