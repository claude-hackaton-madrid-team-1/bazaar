# Duel settings card (Sat 4 Oct)

**One page for Marius.** All values are `GUARDRAILS.md` lines. `duel run` reads them at start: restart `bazaar duel run --play` (and the runtime) after changing any. Every row is offline evidence (zoo, replay, local sim); nothing here has been tried on the real game.

## Before Duels I: merge the duel stack, in this order
1. **#60**: our own two-issue offers stay strictly inside our limit (v1's fix).
2. **#86**: duel policy v2 behind `duel_policy` (default v1).
3. **#103**: B11, the endgame-squeeze knobs (`duel_endgame_min_share`, `duel_jitter`).
4. **#113**: B8, `duel_days_auto` (the evidence-gated days sign).
5. **#115**: B15, v1's forced accepts booked and sent before Jev.
6. **#130**: B7, one early pass for v2's planned accepts; tick-order evidence.

Each PR sits on the previous one, and the chain merges with no conflicts in this order (`night/b27-duel-stack`, 1,112 tests passing). The sim/zoo PRs #80, #97 and #117 are tooling only; merge them whenever.

## Settings

| GUARDRAILS line | today | **Duels I** (h6.5, price only) | **Duels II** (h13, price + days) |
|---|---|---|---|
| `duel_policy` | v1 | **v2** | **v2** |
| `duel_endgame_ticks` | 2 | **1** | **1** |
| `duel_endgame_min_share` | 0 | **0.3** | **0.3** |
| `duel_jitter` | 0 | 0 | 0 |
| `duel_max_own_offers` | 3 | 3 | 3 |
| `duel_accept_margin_ticks` | 1 | 1 | 1 |
| `duel_days_auto` | false | false (no days to read) | **true** (see the warning below) |
| `duel_days_signed` | false | false | false (leave it to the latch) |
| LLM words for duels | v1 uses them if configured | off: v2 sends templates | off |

## Why (offline numbers, n = 200 per cell)
- **v2 vs v1** (W2a zoo, plan gate): result lift 1.42 at decays 0.06/0.08, 1.55 at 0.08/0.10; all 5 checks pass; 0 outside-limit closes in 14,400 duels.
  - Replay on the 12 practice duels we never answered: 173.5 P vs 121.7 P.
  - At the real tick order (we priced first in 55 % of shared ticks), the lift is 1.38 / 1.51. With these B11 settings it is 1.41 / 1.54 (W2b).
- **Endgame squeeze** (B11): against rivals that read our limit, our score goes from 0.193 to 0.298 with min_share 0.3, at 1.007× honest result and 0.994× honest deals.
  - 0.5 scores 0.365 on W2a's rivals, but fails the deal-rate bar in W2b's arena (0.930), so **0.3 is the safe pick**.
  - Against an exploiter that never backs down it costs a little (0.154 → 0.141). It pays unless more than 89 % of informed rivals are that stubborn.
- **Days** (B8): if the real game scores days the simulator's way, valuing them gives v2 +3–10 % P per two-issue duel.
- **End to end** (local sim, 6 concurrent duels on one deadline, zoo + exploiter rivals, taker alongside, 15 s ticks, same 36 scenarios): v2 recommended scores 118.8 sim points vs v1's 79.1 (1.50×). Pie share per duel 0.40 vs 0.24; deal rate 0.83 vs 0.86; 1.3 vs 6.9 rounds per deal; 0 outside our limit for both. v1 lost 11 accepts to the one-per-tick cap and v2 none. No missed ticks, and no slot clash with the taker.

## Warnings
- **`duel_days_auto` = true is only as good as the first real evidence.**
  - It turns signed days on after a REAL two-issue payload whose `days_meaning` says "you gain (+)", or a finished real deal whose score shows it. A conflict or a cost-scored deal turns it off for good.
  - If it flipped wrongly, 7–11 % of duels would close outside our limit.
  - **Read the first Duels II payload yourself** (`.local/duels/duels.jsonl`).
  - On Railway the latch is per service and a redeploy re-arms it.
- **One accept per tick per team.** When several duels share a deadline, v2's planner queues its accepts early (from D−7). Do not run a one-duel-at-a-time policy that waits for the endgame: the cap halves it.
- **Tick order** is the main residual risk. If we move before the rival in most ticks, v2's lift falls to about 1.34 (still ≥ v1).
