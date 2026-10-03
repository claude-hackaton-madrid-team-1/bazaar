# Night W2b: duel policy v2 (silence is free, one accept per tick)

Branch `night/w2b-duel-v2`, stacked on #60 (`fix/duel-offers-inside-limit`). Offline only: nothing here touched the game.
Reproduce with `uv run python scripts/duel_tournament.py` (~15 s). The W2a numbers come from PR #80's
`bazaar_sim.duel_gate.go_no_go(single_duel_move, v1, n=200)` (see "How to rerun").

## What the data says (practice session, 2026-10-02)
- `result = surplus × (1 − decay)^rounds` with `rounds = min(our priced messages, the rival's priced messages)`,
  exact on all 8 deals. An accept adds no round (duels 268, 273 show it).
- So a priced message costs us a round only once the rival has priced as many. Silence costs nothing; a
  rival that never prices makes every offer of ours free.
- The team may accept **one** offer per tick, shared by all duels (RULES.md). Six practice duels shared
  deadline 132. v1's 2-tick endgame can take at most 2 of them.

## What v2 does (`duel_policy` = v2; the default stays v1)
1. Anchor once, then **hold while the rival keeps conceding**. When a rival message would add a round
   (we have offered more than it has), wait only while its average step beats the decay.
2. A rival that never priced gets v1's descending offers, at most `duel_free_offers` = 16. They are free.
3. Stalled rival (no move in our favour for `duel_stall_ticks` = 3): accept if it meets our target. If it
   ignored our last counter, accept. Otherwise counter only if `(on_table + step) × (1 − d) > on_table`.
   Before the rival has shown a step, `step` = `duel_answer_share` (0.2) × the gap to our target.
4. At most `duel_max_own_offers` = 3 priced messages once the rival talks. A last offer at our floor goes out
   the tick before the endgame. In the endgame, any rival offer strictly inside the limit is taken.
5. **Accept planner** (`plan_moves`): the duels holding an acceptable offer form a queue, earliest deadline
   first. When the duels ending by tick D need every remaining accept tick, the most urgent one takes the
   slot now. One tick of margin is kept before the deadline.
6. Days: worst case (|weight| per day, as #60) unless `duel_days_signed` = true. That switch also changes
   the guard. LLM words are off for duels under v2.

The strictly-inside-limit guard stays on every path. It covers v2's own offers, Jev's legal moves (under v2,
accept only where the planner gave the slot, counter only within the caps) and `guardrails.check`.

## Evidence
**Go/no-go on W2a's zoo** (PR #80, independent rival models, 6 styles × 200 × 2 roles, one duel at a time):

| check | decays 0.06/0.08 | decays 0.08/0.10 | bar |
|---|---|---|---|
| mean result v2/v1 | **1.46×** | **1.60×** | ≥ 1.40 |
| deal rate vs conceders | 0.999 (v1 0.992) | 0.999 (v1 0.993) | ≥ v1 |
| deal rate vs one-shot | 0.740 (v1 0.740) | 0.731 (v1 0.731) | ≥ 0.9 × v1 |
| outside our limit (14,400 duels incl. two-issue, both truths) | 0 | 0 | 0 |
| replay, 12 unanswered duels | 173.5 P (v1 121.7) | same | > v1 |

**Our arena** (the same six styles modelled independently, plus the accept slot). It is harder on v2: 6 duels
share each deadline and one accept per tick. Decays 0.08/0.10, 12 and 16 ticks, 9,600 duels:

| rivals | v1 P/duel | v2 P/duel | v2/v1 | v1 rounds | v2 rounds |
|---|---|---|---|---|---|
| linear | 16.55 | 29.02 | 1.75× | 6.4 | 0.4 |
| convex | 17.54 | 22.44 | 1.28× | 4.7 | 1.1 |
| one-shot | 20.70 | 24.13 | 1.17× | 6.4 | 1.5 |
| tit-for-tat | 16.91 | 21.17 | 1.25× | 7.6 | 1.7 |
| no-show (takes offers silently) | 15.02 | 15.02 | 1.00× | 0 | 0 |
| simulator bot | 9.98 | 22.52 | 2.26× | 9.2 | 0 |
| **all** | **16.12** | **22.38** | **1.39×** | 5.8 | 0.8 |

- By decay: 1.23× at 0.06, 1.34× at 0.08, **1.45× at 0.10** (Sunday). With 2 duels per deadline instead of 6: 1.47×.
- Deal rates: v2 0.972 vs v1 0.968 overall. Against conceders 0.983 vs 0.978; against one-shot 0.981 vs 0.984.
- Outside our limit in 10,000 duels (price only and two-issue): 0 for both policies. Moves the guardrail
  refused: 0.
- Robustness rivals, not in the gate: `late` (silent 3–5 ticks, then concedes) 18.53 vs 17.37. `stubborn`
  (repeats one price, duel 274) 23.19 vs 15.87. W2a's `holdout` 28.15 vs 20.25.
- `duel_max_own_offers`: 1 → 1.31×, 2 → 1.38×, **3 → 1.39×**, 4 → 1.39×. On W2a's zoo, 2 loses to v1 against
  tit-for-tat (14.2 vs 15.8 P) and 3 does not (17.3 vs 15.8).
- Two-issue (signed weights for both sides): v2 worst case 20.72 P/duel vs v1 14.82. `duel_days_signed` = true
  gives 23.84 if the simulator's sign is right. **If days really cost (worst truth), 859 of 4,800 deals close
  outside our limit.**

**Replay of the 12 duels we never answered** (rival replays its recorded offers; ideal = its best offer, 0 rounds):
195 P ideal, **v2 176.4 P**, v1 121.7 P. Where v2's 18.6 P go:
- 8.7 P on duel 201. The rival opened outside our limit, so v2 anchored (1 round), and the planner took 70 one tick before its 77.
- 6 P on duels 5 and 6: the one-tick planner margin while four duels shared deadline 132.
- 2.4 P on 119/120/132: one stall-counter each cost a round against a rival that never answered.
- 1.4 P on 148: the rival stayed silent for 8 ticks while v2 sent free offers; its 2 late messages made 2 rounds.

## Verdict
- **GO** on W2a's gate (the plan's harness): all five checks pass at n = 200, mean 1.46× (0.06/0.08) and 1.60× (0.08/0.10).
- **Marginal on our arena's congested case**: 1.39× against the 1.40 bar. It passes at decay 0.10 (1.45×) and with 2 duels
  per deadline (1.47×). All other checks pass.
- Safety: 0 outside-limit closes on both harnesses with the default worst-case days.

## Risks
- Every rival is a model. Pure silence loses to a rival that waits for us (W2a: tit-for-tat deal rate 0.49).
  v2's anchor, stall-counter and last offer exist for that case: v2's tit-for-tat deal rate is 0.999.
- The planner assumes an accept at D − 2 still settles. Accepting at D − 1 is unverified, so it is not used.
- `duel_free_offers`: if a rival stays silent and then talks, each of its messages adds a round, up to our
  offer count (`late`: v2 rounds 2.7, still above v1).
- Jev may still pick `counter` while v2 would hold, within the caps. That is at most 3 rounds.

## What Marius must decide
1. Flip `duel_policy` = v2 for Duels II (decay 0.08) or only for Sunday (0.10)? On our arena: 1.34× vs 1.45×.
2. Keep `duel_max_own_offers` = 3? The plan said 2; the data favours 3 on both zoos.
3. Leave `duel_days_signed` = false until a real two-issue payload confirms the sign (859 / 4,800 outside if wrong).

## How to rerun
- `uv run python scripts/duel_tournament.py [--quick]` prints every table above except W2a's.
- W2a gate, with this branch and #80 side by side:
  `PYTHONPATH=<this>/src:<w2a>/src python -c "from bazaar_sim import duel_gate; from bazaar_agent.agents.duel_v2 import single_duel_move as v2; from bazaar_agent.agents.duelist import duel_move as v1; print(duel_gate.go_no_go(v2, v1, n=200).checks)"`.
  Once merged: `uv run python scripts/duel_zoo.py --gate bazaar_agent.agents.duel_v2:single_duel_move`.
