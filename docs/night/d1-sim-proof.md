# D1: the duel player on the live simulator (Duels II readiness)

Saturday 2026-10-03, 06:00-06:30, D1 takeover of Marius's duel PRs. **Question:** does the squashed player (#60, #86,
#103, #113, #115, #130 + the #60 review fixes) play price-only and two-issue duels end to end over HTTP, never close
outside our limit, and which GUARDRAILS setting should Duels II (decay 0.08) and Sunday (0.10) run?

## Method
- `scripts/duel_sim_proof.py run`: `bazaar-sim serve` (memory world, 2 s ticks, seed 7 at 0.08 and 11 at 0.10) and our
  real `bazaar duel run --play --no-jev` against it with `BAZAAR_SIM=local`, isolated like `scripts/sim_smoke.py`.
- 16 sessions per run, 3 seller/buyer pairs per team on one deadline (`SIM_DUEL_PAIRS=3`: 6 duels share each deadline
  and the team's one accept per tick), 12-tick duels. Odd sessions are price-only, even sessions price + days with a
  random signed `your_days_weight` in [-4, 4]. The last session is cut off, so 96-97 finished duels per run.
- Rivals: `h-` runs draw from the 7 zoo styles (linear, convex, one_shot, tit_for_tat, no_show, sim, holdout); `x-` runs
  from the two exploiters (squeezer, oracle_squeezer) that read our limit and squeeze the endgame.
- Policies, one scratch worktree each (only GUARDRAILS.md differs): `v1` (default), `v2` (`duel_policy` = v2),
  `v2b11` (+ `duel_endgame_min_share` 0.3, `duel_endgame_ticks` 1), `v2b11s` (+ `duel_days_signed` true: what
  `duel_days_auto` turns on once a real payload confirms the sign; the simulator's own sign is "gain (+)").
- Score = share × (1 − decay) ** rounds for a deal, 0 for none (the real result rule), with the share at the
  simulator's true signed weight. "No deal, inside" = a no-deal duel where a rival offer was strictly inside our limit
  at the worst-case valuation the policy uses (criterion 2).

## Decay 0.08 (Duels II)
| run | duels | deal rate | outside limit | mean score | share per deal | rounds/deal | no deal, inside |
|---|---|---|---|---|---|---|---|
| h-v1 (exit 0, crash markers 0) | 96 | 0.81 | 0 | 0.268 | 0.517 | 6.15 | 1 |
| h-v1 price only | 48 | 0.79 | 0 | 0.256 | 0.505 | 5.97 | 1 |
| h-v1 two-issue | 48 | 0.83 | 0 | 0.279 | 0.528 | 6.33 | 0 |
| h-v2 (exit 0, crash markers 0) | 96 | 0.82 | 0 | 0.364 | 0.470 | 1.08 | 1 |
| h-v2 price only | 48 | 0.79 | 0 | 0.364 | 0.485 | 0.89 | 1 |
| h-v2 two-issue | 48 | 0.85 | 0 | 0.363 | 0.456 | 1.24 | 0 |
| h-v2b11 (exit 0, crash markers 0) | 96 | 0.79 | 0 | 0.383 | 0.515 | 0.96 | 3 |
| h-v2b11 price only | 48 | 0.77 | 0 | 0.378 | 0.515 | 0.78 | 2 |
| h-v2b11 two-issue | 48 | 0.81 | 0 | 0.388 | 0.514 | 1.13 | 1 |
| h-v2b11s (exit 0, crash markers 0) | 96 | 0.79 | 0 | 0.419 | 0.552 | 0.84 | 3 |
| h-v2b11s price only | 48 | 0.77 | 0 | 0.378 | 0.515 | 0.78 | 2 |
| h-v2b11s two-issue | 48 | 0.81 | 0 | 0.459 | 0.588 | 0.90 | 1 |
| x-v1 (exit 0, crash markers 0) | 97 | 0.66 | 0 | 0.169 | 0.371 | 7.08 | 27 |
| x-v1 price only | 49 | 0.67 | 0 | 0.174 | 0.364 | 6.67 | 16 |
| x-v1 two-issue | 48 | 0.65 | 0 | 0.164 | 0.378 | 7.52 | 11 |
| x-v2 (exit 0, crash markers 0) | 97 | 0.77 | 0 | 0.259 | 0.380 | 1.87 | 0 |
| x-v2 price only | 49 | 0.84 | 0 | 0.248 | 0.336 | 1.88 | 0 |
| x-v2 two-issue | 48 | 0.71 | 0 | 0.271 | 0.434 | 1.85 | 0 |
| x-v2b11 (exit 0, crash markers 0) | 97 | 0.78 | 0 | 0.318 | 0.458 | 1.92 | 10 |
| x-v2b11 price only | 49 | 0.84 | 0 | 0.329 | 0.441 | 1.83 | 5 |
| x-v2b11 two-issue | 48 | 0.73 | 0 | 0.307 | 0.478 | 2.03 | 5 |
| x-v2b11s (exit 0, crash markers 0) | 97 | 0.78 | 0 | 0.346 | 0.481 | 1.70 | 9 |
| x-v2b11s price only | 49 | 0.84 | 0 | 0.329 | 0.441 | 1.83 | 5 |
| x-v2b11s two-issue | 48 | 0.73 | 0 | 0.364 | 0.528 | 1.54 | 4 |

By role (0.08):
| run | duels | deal rate | outside limit | mean score | share per deal | rounds/deal | no deal, inside |
| h-v1 seller | 48 | 0.83 | 0 | 0.228 | 0.419 | 6.05 | 0 |
| h-v1 buyer | 48 | 0.79 | 0 | 0.307 | 0.620 | 6.26 | 1 |
| h-v2b11s seller | 48 | 0.81 | 0 | 0.399 | 0.515 | 1.00 | 1 |
| h-v2b11s buyer | 48 | 0.77 | 0 | 0.438 | 0.592 | 0.68 | 2 |

## Decay 0.10 (Sunday)
| run | duels | deal rate | outside limit | mean score | share per deal | rounds/deal | no deal, inside |
|---|---|---|---|---|---|---|---|
| h-v1 (exit 0, crash markers 0) | 96 | 0.79 | 0 | 0.278 | 0.564 | 5.30 | 0 |
| h-v1 price only | 48 | 0.81 | 0 | 0.280 | 0.542 | 5.15 | 0 |
| h-v1 two-issue | 48 | 0.77 | 0 | 0.275 | 0.586 | 5.46 | 0 |
| h-v2 (exit 0, crash markers 0) | 96 | 0.81 | 0 | 0.401 | 0.536 | 1.06 | 0 |
| h-v2 price only | 48 | 0.83 | 0 | 0.410 | 0.516 | 0.78 | 0 |
| h-v2 two-issue | 48 | 0.79 | 0 | 0.393 | 0.557 | 1.37 | 0 |
| h-v2b11 (exit 0, crash markers 0) | 96 | 0.78 | 0 | 0.414 | 0.584 | 1.15 | 2 |
| h-v2b11 price only | 48 | 0.81 | 0 | 0.427 | 0.558 | 0.79 | 0 |
| h-v2b11 two-issue | 48 | 0.75 | 0 | 0.401 | 0.612 | 1.53 | 2 |
| h-v2b11s (exit 0, crash markers 0) | 96 | 0.80 | 0 | 0.455 | 0.611 | 1.03 | 2 |
| h-v2b11s price only | 48 | 0.81 | 0 | 0.427 | 0.558 | 0.79 | 0 |
| h-v2b11s two-issue | 48 | 0.79 | 0 | 0.483 | 0.664 | 1.26 | 2 |
| x-v1 (exit 0, crash markers 0) | 97 | 0.62 | 0 | 0.143 | 0.355 | 6.82 | 29 |
| x-v1 price only | 49 | 0.69 | 0 | 0.177 | 0.362 | 5.97 | 15 |
| x-v1 two-issue | 48 | 0.54 | 0 | 0.108 | 0.347 | 7.92 | 14 |
| x-v2 (exit 0, crash markers 0) | 97 | 0.73 | 0 | 0.218 | 0.338 | 1.79 | 1 |
| x-v2 price only | 49 | 0.76 | 0 | 0.227 | 0.321 | 1.32 | 0 |
| x-v2 two-issue | 48 | 0.71 | 0 | 0.209 | 0.356 | 2.29 | 1 |
| x-v2b11 (exit 0, crash markers 0) | 97 | 0.72 | 0 | 0.300 | 0.458 | 1.50 | 11 |
| x-v2b11 price only | 49 | 0.76 | 0 | 0.322 | 0.455 | 1.16 | 5 |
| x-v2b11 two-issue | 48 | 0.69 | 0 | 0.277 | 0.461 | 1.88 | 6 |
| x-v2b11s (exit 0, crash markers 0) | 97 | 0.75 | 0 | 0.358 | 0.520 | 1.41 | 11 |
| x-v2b11s price only | 49 | 0.76 | 0 | 0.322 | 0.455 | 1.16 | 5 |
| x-v2b11s two-issue | 48 | 0.75 | 0 | 0.395 | 0.588 | 1.67 | 6 |

## Findings
- **0 deals outside our limit in 1,552 finished duels** (16 runs), both roles, both issue sets. No crash, no traceback.
  The `duel_inside_limit` guard never had to deny a v2 move; under v1 it denied only second accepts in one tick.
- **v2 beats v1 at both decays**: honest 1.36× (0.08) and 1.44× (0.10); against exploiters 1.53× and 1.52×. v2 spends
  about 1 round per deal where v1 spends 5-7, which is where the decay goes.
- **B11 (min share 0.3, endgame 1) adds +5 % (0.08) / +3 % (0.10) on honest rivals and +23 % / +38 % against
  exploiters**, at a deal rate within 0.03 of v2. Its extra "no deal, inside" rows are refused squeezes, by design.
- **Signed days add +18 % (0.08) / +20 % (0.10) on two-issue duels** when the sign is right. On the real game this
  stays off until `duel_days_auto` latches a real payload (the simulator never counts as evidence).
- **v1 loses deals to the accept queue**: against exploiters 27-29 no-deal duels had an acceptable offer, because six
  duels want the one accept per tick in the same last ticks and v1 has no planner (the guard denies the second accept).
  v2's planner spreads them: 0-1.
- **Open, for Sunday**: v2's planner counts only duels that already hold an acceptable offer, so when several rivals
  cross into our limit on D − 3 one duel can run out of accept ticks (1 of 96 at 0.08: duel 86, rival 81 vs our value 87).
- **Simulator caveats**: zoo rivals are models fitted to the practice payloads; the simulator's pie always exists (the
  real game has no zone in about one duel in six); exploiters were the B11 tuning set (in-sample for `v2b11`).

## Reproduce
```
uv run python scripts/duel_sim_proof.py run --label v2 --decay 0.08 --sessions 16 --out .local/duel-proof
uv run python scripts/duel_sim_proof.py table .local/duel-proof
```
Set GUARDRAILS.md per policy in a scratch worktree (`--repo`); to run several at once give each its own port (patch
`LOCAL_SIM_URL` in that worktree only, pass `--port`).
