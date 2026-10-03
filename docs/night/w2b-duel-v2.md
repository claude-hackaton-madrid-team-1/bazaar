# Night W2b: duel policy v2 (silence is free, one accept per tick)

Branch `night/w2b-duel-v2`, stacked on #60 (`fix/duel-offers-inside-limit`). Offline only: nothing here touched the game.
Reproduce with `uv run python scripts/duel_tournament.py` (~25 s). The W2a numbers come from PR #80's harness
(`bazaar_sim.duel_gate`, `duel_zoo.play_batch`); see "How to rerun".

## What the data says (practice session, 2026-10-02)
- `result = surplus × (1 − decay)^rounds` with `rounds = min(our priced messages, the rival's priced messages)`,
  exact on all 8 deals. An accept adds no round (duels 268, 273 show it).
- So a priced message costs us a round only once the rival has priced as many. Silence costs nothing.
- The team may accept **one** offer per tick, across all duels (RULES.md; `max_accepts_per_tick` = 1). Six practice
  duels shared deadline 132, and v1's 2-tick endgame can take at most 2 of them.

## What v2 does (`duel_policy` = v2; the default stays v1)
1. Anchor once, then **hold while the rival keeps conceding**. When a rival message would add a round (we have
   offered more than it has), wait only while its average step beats the decay.
2. **Free offers.** v1's descending offers go to a rival that has not priced for `duel_stall_ticks` (3), and to one
   that went quiet: it has priced no more than us, nothing for 3 ticks, and ignores our last offer. These cost a
   round only if it answers with a price. At most `duel_free_offers` = 16 messages.
3. Stalled rival (no move in our favour for 3 ticks): accept if it meets our target. If it ignored our last counter,
   accept. Otherwise counter only if `(on_table + step) × (1 − d) > on_table`. Before the rival has shown a step,
   `step` = `duel_answer_share` (0.2) × the gap to our target; practice rivals moved 12–35 % of it.
4. At most `duel_max_own_offers` = 3 **rounds spent**. If nothing acceptable is on the table, the last offer at our
   floor goes out at D − 3 and again at D − 2, whatever the cap, so it is still fresh in the rival's endgame. No deal
   scores 0.
5. **Accept planner** (`plan_moves`): duels holding an acceptable offer queue by earliest deadline. When the duels
   ending by D need every remaining accept tick (by D − 1 − `duel_accept_margin_ticks`), the slowest rival is taken
   first, and the fast conceders keep conceding.
6. Days: worst case (|weight| per day, as #60) unless `duel_days_signed` = true. That switch also changes the guard.
   LLM words are off for duels under v2.

The strictly-inside-limit guard stays on every path. It covers v2's offers, Jev's legal moves and
`guardrails.check`. Under v2, Jev's only legal move is the planner's accept when it gives one; otherwise accept is
not legal, and counter only within the caps.

## Evidence
**W2a's gate** (PR #80 at `7f5c94c`; independent rival models; 6 styles × 200 × 2 roles; one duel at a time):

| check | decays 0.06/0.08 (plan) | decays 0.08/0.10 (what is left) | bar |
|---|---|---|---|
| mean result v2/v1 | **1.420 ✅** (seeds 7/11/13: 1.420 / 1.432 / 1.432) | **1.550 ✅** (1.550 / 1.575 / 1.566) | ≥ 1.40 |
| … if we move before the rival in a tick | 1.34 (seed 7) | **1.44** | |
| … with `duel_accept_margin_ticks` = 0 | 1.534 | 1.679 | |
| deal rate vs conceders | 0.999 (v1 0.992) ✅ | 0.999 (v1 0.993) ✅ | ≥ v1 |
| deal rate vs one-shot | 0.895 (bar 0.789) ✅ | 0.877 (bar 0.777) ✅ | ≥ 0.9 × v1 |
| outside our limit (14,400 duels incl. two-issue, both truths) | 0 ✅ | 0 ✅ | 0 |
| replay, 12 unanswered duels | 173.5 vs 121.7 P ✅ | same | > v1 |

- Per style at 0.08/0.10 (v1 → v2 P/duel): linear 15.8 → 32.2, convex 14.4 → 22.8, sim bot 10.9 → 27.7,
  tit-for-tat 15.8 → 17.3, one-shot 23.3 → 24.2, holdout 20.3 → 28.2. No style loses.
- W2a's **batch runner**: 6 duels share a deadline and one accept per tick, v2 = `plan_moves` at `a170148`, before
  the pace order. It gives 1.438× at 0.08/0.10 and 1.311× at 0.06/0.08. Its replay with one accept per tick
  gives 176.4 P, the same as ours.

**Our arena** (`duel_arena.py`; same six styles, modelled independently; 6 duels share each deadline and one accept per
tick). Decays 0.08/0.10, 12 and 16 ticks, 9,600 duels:

| rivals | v1 P/duel | v2 P/duel | v2/v1 | v1 rounds | v2 rounds |
|---|---|---|---|---|---|
| linear | 16.55 | 29.77 | 1.80× | 6.4 | 0.4 |
| convex | 17.54 | 22.63 | 1.29× | 4.7 | 1.4 |
| one-shot | 20.70 | 24.15 | 1.17× | 6.4 | 1.5 |
| tit-for-tat | 16.91 | 21.18 | 1.25× | 7.6 | 1.7 |
| no-show (takes offers silently) | 15.02 | 15.02 | 1.00× | 0 | 0 |
| simulator bot | 9.98 | 24.44 | 2.45× | 9.2 | 0 |
| **all** | **16.12** | **22.87** | **1.42×** (seeds 1.418 / 1.424 / 1.418) | 5.8 | 0.8 |

- By decay: 1.26× at 0.06, 1.37× at 0.08, **1.47× at 0.10**.
- If we move before the rival: 1.47×. With 2 duels per deadline: 1.48×. With `duel_accept_margin_ticks` = 0: 1.47×.
- Deal rates: v2 0.973 vs v1 0.968. Against conceders 0.983 vs 0.978; against one-shot 0.985 vs 0.984.
- Outside our limit in 10,000 duels (price only and two-issue): 0 for both policies. Guardrail refusals: 0.
- Robustness rivals, not in the gate: `late` (silent 3–5 ticks, then concedes) 22.39 vs 17.37. `stubborn`
  (one price, duel 274) 23.19 vs 15.87.
- `duel_max_own_offers` (rounds): 1 → 1.36×, 2 → 1.40×, **3 → 1.42×**, 4 → 1.42×. On W2a's zoo, 2 loses to v1 against
  tit-for-tat (14.2 vs 15.8 P/duel); 3 does not (17.3).
- Two-issue (signed weights for both sides): v2 worst case 21.04 P/duel vs v1 14.82. `duel_days_signed` = true gives
  24.29 if the simulator's sign is right. **If days really cost, 833 of 4,800 deals close outside our limit.**

**Replay of the 12 duels we never answered.** The rival replays its recorded offers; one accept per tick; ideal = its best
offer at 0 rounds. Ideal 195 P, **v2 178.4 P** (margin 0: 188.0), v1 121.7 P. Where v2's 16.6 P go:
- 8.7 P on duel 201: the rival opened outside our limit, so we anchored (1 round), and we accepted 70 one tick before its 77.
- 4 P on duels 5 and 6: four duels shared deadline 132, so with one accept per tick and the one-tick margin, two were taken early.
- 2.5 P on 119/120/132: one stall-counter each cost a round against a rival that never answered.
- 1.4 P on 148: the rival was silent for 8 ticks, then its 2 messages turned our free offers into 2 rounds.

## Verdict
- **GO on the plan's harness (W2a's gate): all five checks at both decay pairs, on every seed.** Mean lift 1.42× at 0.06/0.08
  and 1.55× at 0.08/0.10; 0 outside; replay +52 P.
- **GO on our congested arena** too, at 1.42× (0.08/0.10, every seed).
- Not GO when we move before the rival at 0.06/0.08: 1.34× on W2a. In that order it is still GO at 0.08/0.10 on W2a
  (1.44×), and our arena gives 1.47×.
- Safety holds everywhere with the default worst-case days: 0 outside-limit closes.

## Risks
- Every rival is a model. W2a: pure silence loses deals to tit-for-tat (deal rate 0.49); v2 keeps 0.999 there.
- **Within-tick order: the main open risk.** On W2a, moving before the rival costs about 0.1 of lift (1.56 → 1.44 at
  0.08/0.10; replay 173.5 → 152.8 P). Two changes help in that order:
  - Free offers wait 3 ticks for the rival to open (replay 140.1 → 152.8 P).
  - The doubled last offer (tit-for-tat deals 0.87 → 0.999).

  Our arena moves the other way. A morning probe of the real order settles it.
- The planner aims to accept by D − 2. When more duels queue than ticks remain, it still accepts on D − 1 rather than
  drop a duel; whether that settles is unverified. Accepting on D − 1 by design (margin 0) is worth +0.05 to +0.13 of lift.
- Free offers: a rival that goes silent and then talks again turns our offers into rounds. `late`: 1.4 rounds, still
  +29 % over v1; our arena's convex rivals: 1.4 rounds vs 1.1 before.
- The runtime agent path acts only on the duels it is asked about. The duelist prompt now asks it to call `duel_move`
  for every live duel, and the runtime remembers each duel's first tick (the live payload has no start). `duel run`
  plans across all duels by itself.
- An independent review of the diff found no change to v1 with the defaults, no move outside our limit, and never more
  than one accept per tick. Fixed from it:
  - the runtime tool ages duels;
  - tickless messages are never read as a stall;
  - a malformed row holds only that duel.

  Still open (low):
  - Jev may counter on a duel whose accept is queued (at most 3 rounds, inside our limit);
  - non-integer rival prices are truncated before valuing, as v1 does.

## What Marius must decide
1. Flip `duel_policy` = v2 for Duels II (0.08) and Sunday (0.10)? W2a: 1.55×; our arena: 1.37× at 0.08 and 1.47× at 0.10.
2. `duel_max_own_offers` = 3 rounds instead of the plan's 2 messages? Both zoos favour 3.
3. `duel_accept_margin_ticks` = 0 once a D − 1 accept is seen to settle (+0.05 to +0.13 lift).
4. Leave `duel_days_signed` = false until a real two-issue payload confirms the sign (recommended).

## How to rerun
- `uv run python scripts/duel_tournament.py [--quick]` prints every table of our arena and the replay.
- W2a's gate, with this branch and #80 side by side:
  `PYTHONPATH=<this>/src:<w2a>/src python -c "from bazaar_sim import duel_gate; from bazaar_agent.agents.duel_v2 import single_duel_move as v2; from bazaar_agent.agents.duelist import duel_move as v1; print(duel_gate.go_no_go(v2, v1, n=200, decays=(0.08, 0.10)).checks)"`.
  `single_duel_move` is one duel, with no cross-duel planner. For batches of duels, pass `duel_v2.plan_moves` as the
  batch policy. Once both branches are merged: `uv run python scripts/duel_zoo.py --gate bazaar_agent.agents.duel_v2:single_duel_move`.
