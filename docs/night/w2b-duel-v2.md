# Night W2b: duel policy v2 (silence is free, one accept per tick)

Draft PR #86, stacked on #60. Offline only. The default stays `duel_policy` = v1; one GUARDRAILS.md line flips it.
Full tables come from `uv run python scripts/duel_tournament.py` (our arena + replay); per-style tables and the review are in the PR body.

## Why v2
- The 8 practice deals give exactly `result = surplus × (1 − decay)^rounds`, with `rounds = min(our priced messages, the
  rival's)`. An accept adds no round. v1 counters every tick: 6–9 rounds a deal, ~30 % of the surplus gone.
- The team accepts **one** offer per tick across all duels. Six practice duels shared deadline 132; v1's endgame takes 2.

## What v2 does
- **Anchor once, then hold while the rival concedes.** Silence is free.
- **Free offers.** v1's descending offers go to a rival that never priced or went quiet; they cost a round only if it answers.
- **Rounds cap.** A stall-counter only when it beats one round of decay, at most 3 rounds spent.
- **Last offer.** Our floor goes out at D − 3 and again at D − 2 when nothing acceptable is on the table.
- **Accept planner.** Earliest deadline first; when the queue binds, the slowest rival first.
- **Days.** Worst case unless `duel_days_signed`.
- **Guard.** #60's strictly-inside-limit guard stays on every path: v2's own moves, Jev's legal moves, `guardrails.check`.

## Go/no-go
| harness | 0.06/0.08 | **0.08/0.10** (what is left) | bar |
|---|---|---|---|
| W2a's gate (PR #80, one duel at a time): v2/v1 mean | 1.42 (seeds 1.420–1.432) | **1.55** (1.550–1.575) | ≥ 1.40 |
| … if we move before the rival within a tick | 1.34 | 1.44 | |
| W2a's batch runner (6 duels per deadline, 1 accept/tick, `plan_moves`) | 1.33 | **1.45** | |
| Our arena (6 duels per deadline, 1 accept/tick) | 1.26–1.37 by decay | **1.42** (seeds 1.418–1.424); we-move-first 1.47 | |
| Deal rate vs conceders / vs one-shot (W2a) | 0.999 vs 0.992 / 0.895 vs bar 0.789 | 0.999 vs 0.993 / 0.877 vs bar 0.777 | ≥ v1 / ≥ 0.9 × v1 |
| Closes outside our limit | 0 of 14,400 (W2a) + 0 of 10,000 (arena) | same | 0 |
| Replay, 12 unanswered duels, one clock (ideal 195 P) | v2 178.4 P vs v1 121.7 P (W2a's replay: identical) | same | > v1 |

- Replay: v2 misses 16.6 P of the ideal.
  - 8.7 P on duel 201 (an anchor round, and an accept one tick early).
  - 4 P on duels 5 and 6 (the accept queue on deadline 132).
  - 2.5 P on stall-counters against rivals that never answered.
  - 1.4 P on 148 (a late talker turned our free offers into rounds).
- **In-sample caveat.** v2's knobs were tuned on both zoos tonight; the zoo lifts are in-sample. The 0.06/0.08 W2a cell went
  1.388 → 1.420 that way. Out-of-sample evidence: the replay on recorded rivals (+57 P over v1), and the same direction on two
  independently written zoos.

**Verdict: GO.** Both harnesses pass at 0.08/0.10 in both within-tick orders. W2a's gate passes all five checks at both decay pairs.
Safety holds everywhere.

## Risks
- **Within-tick order.** Real rivals act at their own moment, so the order is a per-rival mix, not one fact. v2 passes
  at 0.08/0.10 in either order (W2a 1.56 / 1.44; arena 1.42 / 1.47). Morning check: in the first Duels II ticks, compare each
  rival message's `tick` with the tick `duel run` first logged it (`.local/duels/duels.jsonl` has both).
- **`duel_days_signed` = true** is a bet on the sign: +3.3 P/duel if the simulator is right, 833 of 4,800 two-issue deals
  outside our limit if not. Keep it false until a real two-issue payload confirms.
- **D − 1 accepts.** The planner aims for D − 2. `duel_accept_margin_ticks` = 0 would add +0.05 to +0.13 of lift.
  Spend one low-value duel accepting on D − 1 on purpose and see whether it settles.

## What Marius must decide
1. Flip `duel_policy` = v2 for Duels II and Sunday? W2a 1.55×; arena 1.37× at 0.08 and 1.47× at 0.10.
2. Keep `duel_max_own_offers` = 3 rounds? The plan said 2 messages. On W2a a cap of 2 loses to v1 against tit-for-tat (14.2 vs 15.8 P); 3 does not (17.3).
3. Margin 0 after the D − 1 check.
4. Leave days signed off.

## How to rerun
- Our arena: `uv run python scripts/duel_tournament.py [--quick]`.
- W2a's gate, with this branch and #80 side by side:
  `PYTHONPATH=<this>/src:<w2a>/src python -c "from bazaar_sim import duel_gate; from bazaar_agent.agents.duel_v2 import single_duel_move as v2; from bazaar_agent.agents.duelist import duel_move as v1; print(duel_gate.go_no_go(v2, v1, n=200, decays=(0.08, 0.10)).checks)"`.
  `single_duel_move` plays GUARDRAILS.md's knobs for one duel. Batches of duels need `duel_v2.plan_moves`, the batch policy.
