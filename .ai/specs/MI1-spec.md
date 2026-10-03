# MI1 — Move impact: what a sale, swap or buy could cost our score, and a guard on it

**Source:** local backlog (`.ai/specs/02-plan.md`); requested by Omar after the SAL-07 incident (Sat 3 Oct).

## Why (the incident, measured from `me_snapshots` and `feed_events`)
- Tick 320: we bought SAL-07 (asset 438) from team t02 for 23 (settlement 408). It completed Salamanca.
- Tick 947: the hand-run `dealer sell` (agent `dealer-sell`, floor 20) accepted dealer Pilar's final of 29 for
  that copy (settlement 872, tick 948). Its /me `your_value` was 118.6.
- /me `neg_points` 134.2 → 44.6 at tick 948 (−89.6 = 29 − 118.6); board `negotiating` 20.75 → 16.48 at its next
  10-tick update (tick 950): −4.27 score.
- Tick 958: SAL-07 bought back from Abuela for 21 (settlement 881): the page came back, `neg_points` did not
  (a dealer deal is not a team trade).

## What
1. `move_impact.py` (pure): the score impact of selling / swapping away / buying one copy, from /me (`your_value`
   per copy, page bonus included), the copy's origin from our settlements (team, dealer, pack, starting stock),
   the price, the counterparty kind and k (board `negotiating` per neg_point, measured on our snapshots; fallback
   `score_per_neg_point_fallback`). Rules: a team-acquired copy sold to anyone, or any copy sold to a team, moves
   neg_points by price − your_value; a buy from a team by value − price; a dealer deal adds `dealer_ladder_score`
   and no neg_points; it also flags a sale that breaks a complete page.
2. Guard: `guardrails.check()` refuses every sale (maker ask, bid accept, dealer sell, the copy a swap gives) whose
   estimate is below −`max_score_loss_per_move` (0.2), unless `human_approvals` (#209) holds that card, side sell,
   down to that price. Origins and k are read once per tick (`impact_board`, a `TickBoard`); fail CLOSED (unread:
   every copy counts as bought from a team; a copy with no value refuses).
3. `uv run bazaar impact sell SAL-07 29 --to pilar` (read-only) prints the estimate and the reason; the team desk's
   Jev state (`swap_state`) and the dealer sell gate's state carry the estimate (`score_impact`).

## Acceptance
- Incident replay: SAL-07 at 29 to Pilar, at tick 947's state, estimates −4.7 ± 0.5 and is refused.
- An approval (`bazaar approve SAL-07 --sell --min 29`) lets exactly that move through.
- Unread facts price at the worst case; approvals unread hold (never walk).
- Full gate green; sim smoke passes on a private port.

## Assumptions (unverified against the server's formula)
- The neg_points model is fitted to one loss event (−89.6 exactly = 29 − 118.6) and the briefing's definition;
  a sale of a non-team copy to a team at price − your_value is the symmetric case, not observed.
- k is state-dependent (the board is relative to other teams): gains while we led moved it ~0 (ticks 376–386).
