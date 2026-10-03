# Night W2b: duel policy v2 (silence is free, one accept per tick)

Branch `night/w2b-duel-v2`, stacked on #60 (`fix/duel-offers-inside-limit`). Offline only: nothing here touched the game.
Reproduce with `uv run python scripts/duel_tournament.py` (~20 s). The W2a numbers come from PR #80 at `aa6d14f`
(`bazaar_sim.duel_gate.go_no_go(single_duel_move, v1, n=200)`); see "How to rerun".

## What the data says (practice session, 2026-10-02)
- `result = surplus × (1 − decay)^rounds` with `rounds = min(our priced messages, the rival's priced messages)`,
  exact on all 8 deals. An accept adds no round (duels 268, 273 show it).
- So a priced message costs us a round only once the rival has priced as many. Silence costs nothing.
- The team may accept **one** offer per tick, across all duels (RULES.md). Six practice duels shared deadline 132.
  v1's 2-tick endgame can take at most 2 of them.

## What v2 does (`duel_policy` = v2; the default stays v1)
1. Anchor once, then **hold while the rival keeps conceding**. When a rival message would add a round (we have
   offered more than it has), wait only while its average step beats the decay.
2. A rival that has not priced after `duel_stall_ticks` gets v1's descending offers, at most `duel_free_offers` = 16.
   They cost no round until it prices.
3. Stalled rival (no move in our favour for `duel_stall_ticks` = 3): accept if it meets our target. If it ignored our
   last counter, accept. Otherwise counter only if `(on_table + step) × (1 − d) > on_table`. Before the rival has
   shown a step, `step` = `duel_answer_share` (0.2) × the gap to our target; practice rivals moved 12–35 %.
4. At most `duel_max_own_offers` = 3 priced messages once the rival talks. A last offer at our floor goes out the
   tick before the endgame. A rival offer strictly inside the limit is taken by D − 1 − `duel_accept_margin_ticks`
   (default 1, i.e. by D − 2, like v1).
5. **Accept planner** (`plan_moves`): duels holding an acceptable offer queue by earliest deadline. When the duels
   ending by tick D need every remaining accept tick, the most urgent takes the slot now.
6. Days: worst case (|weight| per day, as #60) unless `duel_days_signed` = true. That switch also changes the guard.
   LLM words are off for duels under v2.

The strictly-inside-limit guard stays on every path. It covers v2's own offers, Jev's legal moves and
`guardrails.check`. Under v2, Jev's only legal move is the planner's accept when the planner gives one; otherwise
accept is not legal, and counter only within the caps.

## Evidence
**W2a's gate** (PR #80 `aa6d14f`, independent rival models with the fixed listening one-shot), n = 200, one duel at a time:

| check | decays 0.06/0.08 (plan) | decays 0.08/0.10 (what is left) | bar |
|---|---|---|---|
| mean result v2/v1 | 1.388 ❌ (seeds 7/11/13: 1.388 / 1.399 / 1.403) | **1.518 ✅** (seeds: 1.518 / 1.542 / 1.535) | ≥ 1.40 |
| … if we move before the rival in a tick | 1.347 (3-seed mean) | **1.469** | |
| … with `duel_accept_margin_ticks` = 0 | 1.500 | 1.645 | |
| deal rate vs conceders | 0.999 (v1 0.992) ✅ | 0.999 (v1 0.993) ✅ | ≥ v1 |
| deal rate vs one-shot | 0.895 (bar 0.789) ✅ | 0.877 (bar 0.777) ✅ | ≥ 0.9 × v1 |
| outside our limit (14,400 duels incl. two-issue, both truths) | 0 ✅ | 0 ✅ | 0 |
| replay, 12 unanswered duels | 173.5 vs 121.7 P ✅ (we move first: 152.8 vs 118.5) | same | > v1 |

Per style on W2a's zoo at 0.08/0.10 (v1 → v2 P/duel): linear 15.8 → 32.2, convex 14.4 → 22.4, sim bot 10.9 → 27.7,
tit-for-tat 15.8 → 17.3, one-shot 23.3 → 22.1, no-show 0 → 0. Their no-show never closes and none of their
rivals answers; one-shot is v2's only loss.

**Our arena** (`duel_arena.py`, the same six styles modelled independently). It is harsher: 6 duels share each
deadline and the team's one accept per tick. Decays 0.08/0.10, 12 and 16 ticks, 9,600 duels:

| rivals | v1 P/duel | v2 P/duel | v2/v1 | v1 rounds | v2 rounds |
|---|---|---|---|---|---|
| linear | 16.55 | 29.02 | 1.75× | 6.4 | 0.4 |
| convex | 17.54 | 22.44 | 1.28× | 4.7 | 1.1 |
| one-shot | 20.70 | 24.13 | 1.17× | 6.4 | 1.5 |
| tit-for-tat | 16.91 | 21.17 | 1.25× | 7.6 | 1.7 |
| no-show (takes offers silently) | 15.02 | 15.02 | 1.00× | 0 | 0 |
| simulator bot | 9.98 | 22.52 | 2.26× | 9.2 | 0 |
| **all** | **16.12** | **22.38** | **1.39×** (seeds 1.389 / 1.393 / 1.390) | 5.8 | 0.8 |

- By decay: 1.23× at 0.06, 1.34× at 0.08, **1.45× at 0.10**. With 2 duels per deadline: **1.47×** (seeds 1.472–1.475).
  With `duel_accept_margin_ticks` = 0: 1.44×.
- Deal rates: v2 0.972 vs v1 0.968. Against conceders 0.983 vs 0.978; against one-shot 0.981 vs 0.984 (bar 0.885).
- Outside our limit in 10,000 duels (price only and two-issue): 0 for both policies. Guardrail refusals: 0.
- Robustness rivals, not in the gate: `late` (silent 3–5 ticks, then concedes) 21.81 vs 17.37. `stubborn`
  (one price, duel 274) 23.19 vs 15.87. W2a's `holdout`: 28.2 vs 20.3.
- `duel_max_own_offers`: 1 → 1.31×, 2 → 1.38×, **3 → 1.39×**, 4 → 1.39×. On W2a's zoo, 2 loses to v1 against
  tit-for-tat (14.2 vs 15.8 P/duel); 3 does not (17.3).
- Two-issue (signed weights for both sides): v2 worst case 20.72 P/duel vs v1 14.82. `duel_days_signed` = true
  gives 23.84 if the simulator's sign is right. **If days really cost, 859 of 4,800 deals close outside our limit.**

**Replay of the 12 duels we never answered.** The rival replays its recorded offers; ideal = its best offer at 0 rounds.
Ideal 195 P, **v2 176.4 P** (margin 0: 187.0), v1 121.7 P. Where v2's 18.6 P go:
- 8.7 P on duel 201: the rival opened outside our limit, so we anchored (1 round), and we accepted 70 one tick before its 77.
- 6 P on duels 5 and 6: the one-tick accept margin while four duels shared deadline 132.
- 2.4 P on 119/120/132: one stall-counter each cost a round against a rival that never answered.
- 1.4 P on 148: the rival was silent for 8 ticks, then its 2 messages turned our offers into 2 rounds.

## Verdict
- **GO for the duels that are left** (Duels II at 0.08, Sunday at 0.10). On W2a's gate at 0.08/0.10 all five checks
  pass in both within-tick orders: 1.47–1.53×, 0 outside, replay +52 P.
- **On the bar at the plan's literal 0.06/0.08**: 1.388–1.403 across seeds on W2a's zoo (NO-GO on seed 7 by
  0.012). On our congested arena at 0.08/0.10 it is 1.39× (NO-GO by 0.01); it passes at 0.10 (1.45×) and with 2
  duels per deadline (1.47×).
- Safety holds everywhere with the default worst-case days: 0 outside-limit closes.

## Risks
- Every rival is a model. W2a: pure silence loses deals to tit-for-tat (deal rate 0.49). v2's anchor, stall-counter
  and last offer keep it at 0.999 against tit-for-tat.
- **Within-tick order.** If we move before the rival within a tick, v2's lift drops by 0.05–0.06 (W2a: 1.53 → 1.47
  at 0.08/0.10; replay 173.5 → 152.8 P). Free offers now wait 3 ticks for the rival to open; that took the replay
  from 140.1 to 152.8 in this order.
- The planner accepts by D − 2. An accept on D − 1 is worth +0.05 to +0.11 of lift and +10 P of replay, but it is
  unverified; it is one knob.
- Free offers: if a rival stays silent and then talks, each of its messages adds a round, up to our offer count.
  `late`: 1.3 rounds, still +26 % over v1.
- The runtime agent path acts only on the duel it is asked about. The duelist prompt now asks it to call
  `duel_move` for every live duel each tick. `duel run` plans across all duels by itself.

## What Marius must decide
1. Flip `duel_policy` = v2 for Duels II (0.08) or only for Sunday (0.10)? W2a: 1.53× at 0.08/0.10. Our arena: 1.34×
   at 0.08 and 1.45× at 0.10.
2. `duel_max_own_offers` = 3 instead of the plan's 2? Both zoos favour 3.
3. `duel_accept_margin_ticks` = 0 (accept on D − 1)? Only once a deal accepted on D − 1 is seen to settle.
4. Leave `duel_days_signed` = false until a real two-issue payload confirms the sign (recommended).

## How to rerun
- `uv run python scripts/duel_tournament.py [--quick]` prints every table of our arena and the replay.
- W2a's gate, with this branch and #80 side by side:
  `PYTHONPATH=<this>/src:<w2a>/src python -c "from bazaar_sim import duel_gate; from bazaar_agent.agents.duel_v2 import single_duel_move as v2; from bazaar_agent.agents.duelist import duel_move as v1; print(duel_gate.go_no_go(v2, v1, n=200, decays=(0.08, 0.10)).checks)"`.
  Once both are merged: `uv run python scripts/duel_zoo.py --gate bazaar_agent.agents.duel_v2:single_duel_move`.
