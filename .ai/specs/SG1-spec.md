# SG1 — Jev-gated strategy pack + guardrail review

Source: local backlog (`.ai/specs/02-plan.md`, decided 2026-10-03). Asked by Omar (Sat 3 Oct afternoon): "check
the guardrails in case we need to update something, in only one PR. We need more strategies to move more things
and rank up. Use Jev always for the strategy decisions."

## Live situation (tick ~668, read-only)
Cash 81, `cash_floor` 50, rank 8, score 25.2 (duels 15, ladder 0.02, market 7.5). No taker dealer thread since
tick 443: the strategy ranks only LAV-09/10 and MAL-09/10 (denied by cash) and skips every RET/LAT card as
"surplus too small". `dealer_sell_enabled` is off since #200 (four sell threads for one card blocked the taker).

## Requirements
1. Guardrail review: every rule that may block points gets a Jev question with the live state; only a decided
   verdict changes a rule, cited in its GUARDRAILS.md line; undecided keeps it. Question and state files live in
   `questions/` (reproducible).
2. Strategies run only on a decided Jev yes (`questions/strategies.json`), asked again every
   `strategy_jev_refresh_ticks`; undecided, no, timeout, error or no Jev keep them off; every answer is a
   `strategy_gate` decision row. No strategy is hard-coded on.
3. (a) Ladder probe: one small negotiated dealer buy per dealer per game hour, never at the opening ask, top at
   most the official value, the rarity cap and the cash above the floor, and only when the top keeps at least
   `ladder_probe_min_share` of the dealer's range (opening − top) / (opening − lowest fill), never below her
   lowest fill.
4. (b) Dealer sells: the existing desk (backoff from #202 already on main) opens a new sell thread only on a Jev
   yes, never with a dealer the taker wanted in the last `dealer_sell_taker_window_ticks`, and takes a final only
   at or above max(floor, `dealer_sell_final_min_first_ask_share` × our first ask). Still behind
   `dealer_sell_enabled`.
5. (c) Market creation on v19: not possible. RULES.md: "You cannot trade on your own venue with your team key".

## Out of scope
Changing any value Jev did not decide; packs in the probe (the pack Jev gate owns packs); adopting a sell thread
after a restart.
