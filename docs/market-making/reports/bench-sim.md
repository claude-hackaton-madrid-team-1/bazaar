# Beating the free stall in the Market Test: calibrated simulation (bench-sim)

Sunday 4 Oct 2026, 10:28–11:xx Madrid, session `bench-sim`. Read-only on the game, Railway and Postgres (SELECTs on
`bench_books` and `decisions`, keyless `/api/schedule` and `/api/clock`). Branch `feat/bench-beat-stall` (local, not
pushed). Builds on `bench-baseline` (scoring and session facts) and the night reports (`BENCH_BEAT_STALL.md`,
`mm-probe.md`).

## TL;DR

1. **The server checks quotes, not hidden limits.** The one-shot match probe fired in session 7: tick 1692, sell
   b120-12 ask 40 × buy b120-2 bid 39 at 39 → `400 bad_match "price must sit between the ask 40 and the bid 39"`
   (`decisions` id 4264). So `probe` and every "non-crossing pair" idea are dead; a broker can beat the stall only by
   **which** quote-crossing traders it matches and **when**.
2. **"When" is where the money is, "who" is not.** On books fitted to sessions 7 and 8, a broker that knew every
   departure (the quote-rule oracle) realises **+10 %** of the possible gains over the stall (+4.5 % and +14 % on
   posterior replays of the real books b120 and b137). Picking better partners among today's crossing pairs, even
   knowing every limit (`who_oracle`), gains < 1 % and is often negative. Existing `edge` only does "who".
3. **New policy `BAZAAR_BENCH_POLICY=lookahead`** (`agents/bench_lookahead.py`): each tick, 128 posterior futures
   (hidden limits, lives and relax shares that reproduce every trader's quote path, plus the traders not yet seen),
   every matching of crossing pairs (hold, re-pair, one more pair) played against them with the stall afterwards;
   the best expected true surplus wins, ties keep exact. Default stays `exact`.
4. **Scored with the real rule** (alone above the stall = 1.0 because nobody has ever beaten it; below = 0.5·E/Es),
   session 9's world (`cal_normal20`, 300 seeds): **lookahead E[pts] 0.64, P(above) 0.29, P(below) 0.14** vs
   `edge` with `BAZAAR_BENCH_GUARD_MARGIN=none` 0.57 / 0.15 / 0.08, `edge` (margin 10) 0.51 and `exact` 0.500.
   Robust: 0.58–0.67 in every ±50 % world. Real-book replays: **b137 (session 8) 0.90**, b120 (session 7) 0.50
   (replays check the decisions on real quote paths; the outcome still rests on drawn limits and lives, §4).
5. **Deploying it needs a merge** (src/** → bazaar-maker, -taker, -duels, … redeploy): only after Duels III ends
   (expected ~11:35–11:40, see §5) and before ~12:25, a ~45 min window; never 12:37–12:42 (session 9 = t 17.0, the LAST bench). The variable can be set first with
   `--skip-deploys`: today's code ignores an unknown value loudly and stays `exact`. Fallback with no merge:
   `BAZAAR_BENCH_POLICY=edge` + `BAZAAR_BENCH_GUARD_MARGIN=none` (maker-only restart): 0.57 in the sim, but it tied
   the stall on both real replays.

## 1. Calibration (step 1)

Data: `bench_books` runs **b120** (session 7, the hard test, 12 a side, ticks 1690–1705) and **b137** (session 8,
normal, 10 a side, ticks 1775–1788; `expires_tick` 1790 on every offer, so the run is ticks 1774–1789), plus our
`broker_match` decisions (14 and 8 traders matched by us: their lives are censored at the match tick).

What the real books show:
- **Sellers come in two bumps.** b120 opening asks 35–48 (six, all matched) or 77–134 (six, none ever crossed); b137
  27–54 or 71–129. #77's preset (costs 20–60 × 1.05–1.30) cannot draw an ask above 78.
- **Buyers open far below their limit.** b137-3 rises 42 → 64 over 4 ticks and is still rising; b137-7 36 → 55 over
  5 ticks. Fitting the linear relax model needs bids down to ~0.6 × value; asks up to ~1.45 × cost.
- **Steps** are 1–10 P a tick, linear in age (b120-17: 105, 99, 93, 88; b137-13: 129, 123, 118, 112, 106, 101).
- **Firm share:** b120 5 of 16 traders seen ≥ 2 ticks; b137 1 of 16.
- **Lives of unmatched traders** (uncensored): b120 2, 2, 3, 3, 4, 4, 4, 6, 5+; b137 2, 2, 3, 4, 5, 6+. None of the 15
  left after a single tick (the prior's impatient life 1–2 says 1 in 8 should; weak evidence for a minimum of 2).
- **Arrivals** spread over run ticks 0–11 in both.

The fitted worlds (`scripts/bench_tournament.py` `WORLDS`): sellers' costs 25–42 or 65–110 (half each), values
40–105, asks 1.05–1.45 × cost, bids 0.6–0.95 × value, arrivals 0–11, #12's firm/impatient shares (0.2/0.25 normal,
0.35/0.35 hard) and lives (1–2 / 3–6).

Fit, the real book as our exact broker saw it vs the same broker on 300 simulated books (quantiles 10/25/50/75/90 %):

| book | a side | matched | firm (≥ 2 ticks) | opening asks | opening bids | unmatched lives | ask steps | bid steps | stall | oracle |
|---|---|---|---|---|---|---|---|---|---|---|
| real b120 (s7) | 12 | 0.58 | 0.31 | 38 40 48 93 105 | 37 37 56 63 74 | 2 2 3 4 5 | 1 2 4 6 6 | 2 3 5 6 10 | 0.967 (ours = stall) | – |
| real b137 (s8) | 10 | 0.40 | 0.06 | 27 54 83 107 116 | 30 42 50 61 69 | 2 2 3 4 5 | 3 4 5 6 6 | 2 3 4 5 6 | – | – |
| `cal_hard24` | 12 | 0.48 | 0.35 | 34 41 60 109 128 | 35 43 55 68 80 | 1 2 3 5 6 | 1 2 4 7 10 | 1 2 3 5 8 | 0.801 | 0.905 |
| `cal_normal20` | 10 | 0.49 | 0.20 | 34 41 59 108 128 | 35 42 55 68 80 | 1 2 4 5 6 | 1 2 4 6 9 | 1 2 3 5 7 | 0.808 | 0.909 |
| `old_normal20` (#77 ×2) | 10 | 0.71 | 0.19 | 27 35 47 59 66 | 38 45 57 69 76 | 1 2 3 5 6 | 1 1 2 3 4 | 1 1 2 3 4 | 0.878 | 0.956 |

- The calibrated worlds reproduce the quote paths; #77's preset does not (asks too low and too tight, steps half the
  real ones, too many matches).
- **Efficiency level:** posterior replays of b120 give the stall 0.937 (real: 0.967, our exact = stall), b137 0.777.
  The simulated stall (0.80) sits below Saturday's 0.85–0.97; the score's denominator may be time-aware on the
  server (here it is the static optimum). That moves the level, not the comparison: every policy is scored against
  the stall on the same book.

## 2. What can beat the stall, and by how much (step 2)

Rule facts used: only quote-crossing pairs match (§TL;DR 1); a match's true gain is `buyer limit − seller limit`
whatever the price, so pricing is moot and, for one item, the pairing does not matter either, only the **set** of
traders matched. Points (`real_points`): alone above the stall → 1.0 (bench-baseline: nobody beat the stall in any of
the 8 sessions, so the top-3 mean is the stall and a lone team above it is in the top 3); below → 0.5·E/Es
(Saturday's fit on t03/t13, `BENCH_BEAT_STALL.md` §1). Under the round-average reading the same P(above) decides.

Policies, all on the same 300 books per world (`--seeds 300`, seeds 0–299):
- `stall` (kit's starter broker = the free stall), `exact` (today's broker);
- `edge` with guard margin 20 / 10 (default) / 5 / 0 / none (`BAZAAR_BENCH_GUARD_MARGIN`), and `edgecal_*` (the same
  with the edge's prior refitted to b120);
- `probe` and `BAZAAR_BENCH_MATCH_PROBE`: not run; every non-crossing pair is refused (§TL;DR 1), so they equal
  `exact` plus refused POSTs;
- new: `maxcount` (most crossing pairs each tick), `hold3` / `hold6` (hold young pairs crossing by < 3 / < 6 P),
  **`lookahead`**; bounds: `who_oracle` (knows limits, never holds), `oracle` (knows everything).

| world | policy | eff | stall | margin | P(above) | P(below) | worst | E[points] |
|---|---|---|---|---|---|---|---|---|
| cal_normal20 | exact | 0.8082 | 0.8082 | +0.0000 | 0.000 | 0.000 | +0.000 | 0.500 |
| cal_normal20 | edge20 | 0.8086 | 0.8082 | +0.0004 | 0.007 | 0.000 | +0.000 | 0.503 |
| cal_normal20 | edge | 0.8094 | 0.8082 | +0.0012 | 0.027 | 0.007 | -0.159 | 0.513 |
| cal_normal20 | edge5 | 0.8101 | 0.8082 | +0.0019 | 0.087 | 0.047 | -0.159 | 0.542 |
| cal_normal20 | edge_none | 0.8098 | 0.8082 | +0.0015 | 0.147 | 0.083 | -0.161 | 0.570 |
| cal_normal20 | edgecal_none | 0.8060 | 0.8082 | -0.0022 | 0.157 | 0.130 | -0.231 | 0.573 |
| cal_normal20 | maxcount | 0.8063 | 0.8082 | -0.0019 | 0.097 | 0.087 | -0.201 | 0.544 |
| cal_normal20 | hold3 | 0.8091 | 0.8082 | +0.0009 | 0.010 | 0.010 | -0.059 | 0.505 |
| cal_normal20 | hold6 | 0.8118 | 0.8082 | +0.0036 | 0.080 | 0.067 | -0.179 | 0.538 |
| cal_normal20 | lookahead | 0.8281 | 0.8082 | +0.0199 | 0.293 | 0.140 | -0.224 | 0.642 |
| cal_normal20 | who_oracle | 0.8132 | 0.8082 | +0.0049 | 0.363 | 0.133 | -0.210 | 0.675 |
| cal_normal20 | oracle | 0.9088 | 0.8082 | +0.1005 | 0.863 | 0.000 | +0.000 | 0.932 |
| cal_hard24 | exact | 0.8013 | 0.8013 | +0.0000 | 0.000 | 0.000 | +0.000 | 0.500 |
| cal_hard24 | edge20 | 0.8014 | 0.8013 | +0.0001 | 0.003 | 0.003 | -0.036 | 0.502 |
| cal_hard24 | edge | 0.8030 | 0.8013 | +0.0017 | 0.040 | 0.030 | -0.104 | 0.519 |
| cal_hard24 | edge5 | 0.8043 | 0.8013 | +0.0030 | 0.113 | 0.063 | -0.145 | 0.555 |
| cal_hard24 | edge_none | 0.8018 | 0.8013 | +0.0005 | 0.177 | 0.170 | -0.166 | 0.583 |
| cal_hard24 | edgecal_none | 0.8004 | 0.8013 | -0.0009 | 0.187 | 0.203 | -0.166 | 0.587 |
| cal_hard24 | maxcount | 0.7979 | 0.8013 | -0.0034 | 0.127 | 0.157 | -0.166 | 0.557 |
| cal_hard24 | hold3 | 0.8008 | 0.8013 | -0.0005 | 0.013 | 0.013 | -0.141 | 0.506 |
| cal_hard24 | hold6 | 0.8002 | 0.8013 | -0.0011 | 0.087 | 0.117 | -0.205 | 0.538 |
| cal_hard24 | lookahead | 0.8128 | 0.8013 | +0.0115 | 0.293 | 0.217 | -0.235 | 0.639 |
| cal_hard24 | who_oracle | 0.8085 | 0.8013 | +0.0071 | 0.407 | 0.210 | -0.249 | 0.695 |
| cal_hard24 | oracle | 0.9054 | 0.8013 | +0.1041 | 0.910 | 0.000 | +0.000 | 0.955 |
| cal_normal20_uniform | exact | 0.8601 | 0.8601 | +0.0000 | 0.000 | 0.000 | +0.000 | 0.500 |
| cal_normal20_uniform | edge20 | 0.8606 | 0.8601 | +0.0005 | 0.007 | 0.003 | -0.002 | 0.503 |
| cal_normal20_uniform | edge | 0.8627 | 0.8601 | +0.0026 | 0.083 | 0.070 | -0.097 | 0.540 |
| cal_normal20_uniform | edge5 | 0.8643 | 0.8601 | +0.0042 | 0.167 | 0.110 | -0.185 | 0.581 |
| cal_normal20_uniform | edge_none | 0.8656 | 0.8601 | +0.0055 | 0.280 | 0.177 | -0.185 | 0.636 |
| cal_normal20_uniform | edgecal_none | 0.8656 | 0.8601 | +0.0055 | 0.300 | 0.197 | -0.185 | 0.645 |
| cal_normal20_uniform | maxcount | 0.8635 | 0.8601 | +0.0034 | 0.273 | 0.173 | -0.185 | 0.632 |
| cal_normal20_uniform | hold3 | 0.8589 | 0.8601 | -0.0012 | 0.027 | 0.043 | -0.136 | 0.512 |
| cal_normal20_uniform | hold6 | 0.8519 | 0.8601 | -0.0082 | 0.063 | 0.197 | -0.242 | 0.525 |
| cal_normal20_uniform | lookahead | 0.8692 | 0.8601 | +0.0090 | 0.380 | 0.293 | -0.224 | 0.682 |
| cal_normal20_uniform | who_oracle | 0.8671 | 0.8601 | +0.0070 | 0.430 | 0.197 | -0.185 | 0.709 |
| cal_normal20_uniform | oracle | 0.9434 | 0.8601 | +0.0833 | 0.920 | 0.000 | +0.000 | 0.960 |
| old_normal20 | exact | 0.8783 | 0.8783 | +0.0000 | 0.000 | 0.000 | +0.000 | 0.500 |
| old_normal20 | edge20 | 0.8781 | 0.8783 | -0.0001 | 0.000 | 0.003 | -0.039 | 0.500 |
| old_normal20 | edge | 0.8795 | 0.8783 | +0.0012 | 0.090 | 0.060 | -0.123 | 0.543 |
| old_normal20 | edge5 | 0.8774 | 0.8783 | -0.0009 | 0.150 | 0.117 | -0.173 | 0.571 |
| old_normal20 | edge_none | 0.8724 | 0.8783 | -0.0059 | 0.210 | 0.230 | -0.187 | 0.598 |
| old_normal20 | edgecal_none | 0.8693 | 0.8783 | -0.0089 | 0.203 | 0.293 | -0.187 | 0.593 |
| old_normal20 | maxcount | 0.8712 | 0.8783 | -0.0071 | 0.173 | 0.263 | -0.187 | 0.579 |
| old_normal20 | hold3 | 0.8777 | 0.8783 | -0.0006 | 0.040 | 0.040 | -0.132 | 0.519 |
| old_normal20 | hold6 | 0.8768 | 0.8783 | -0.0015 | 0.160 | 0.163 | -0.160 | 0.575 |
| old_normal20 | lookahead | 0.8808 | 0.8783 | +0.0026 | 0.337 | 0.333 | -0.215 | 0.660 |
| old_normal20 | who_oracle | 0.8696 | 0.8783 | -0.0087 | 0.290 | 0.320 | -0.187 | 0.635 |
| old_normal20 | oracle | 0.9561 | 0.8783 | +0.0778 | 0.940 | 0.000 | +0.000 | 0.970 |

Reading it:
- **`oracle − stall` = +8 to +10 %**: the headroom is large, and it is timing: `who_oracle` (perfect limits, no
  holding) is only +0.5 to +0.9 %, and negative on #77's world.
- Naive holding (`hold*`, a grid of 42 settings in the scratchpad) loses on average everywhere: a held trader leaves.
- `maxcount` is not better than exact on average: one more pair now can take the seller a better buyer needed later.
- `edge` helps a little because it deviates rarely; `edge_none` deviates more and so is above the stall more often.
- **`lookahead` has the highest mean margin (+0.3 to +2.0 %) and the highest P(above) of every real policy.**

## 3. Robustness (±50 % on each parameter of `cal_normal20`)

| world | exact | edge (10) | edge_none | lookahead: margin / P(above) / P(below) / E[points] | oracle margin |
|---|---|---|---|---|---|
| firm-50% | 0.500 | 0.513 | 0.560 | +0.0189 / 0.297 / 0.147 / **0.644** | +0.097 |
| impatient-50% | 0.500 | 0.514 | 0.568 | +0.0233 / 0.313 / 0.133 / **0.653** | +0.098 |
| relax-50% | 0.500 | 0.510 | 0.565 | +0.0265 / 0.340 / 0.103 / **0.666** | +0.102 |
| shade-50% | 0.500 | 0.514 | 0.539 | +0.0113 / 0.280 / 0.227 / **0.632** | +0.100 |
| cheap_share-50% | 0.500 | 0.508 | 0.551 | +0.0104 / 0.180 / 0.127 / **0.583** | +0.092 |
| lives-50% | 0.500 | 0.514 | 0.557 | +0.0013 / 0.190 / 0.190 / **0.584** | +0.070 |
| firm+50% | 0.500 | 0.513 | 0.571 | +0.0229 / 0.307 / 0.150 / **0.649** | +0.104 |
| impatient+50% | 0.500 | 0.513 | 0.562 | +0.0133 / 0.283 / 0.193 / **0.633** | +0.098 |
| relax+50% | 0.500 | 0.511 | 0.559 | +0.0209 / 0.287 / 0.137 / **0.639** | +0.099 |
| shade+50% | 0.500 | 0.511 | 0.548 | +0.0105 / 0.253 / 0.167 / **0.619** | +0.097 |
| cheap_share+50% | 0.500 | 0.525 | 0.592 | +0.0145 / 0.323 / 0.203 / **0.656** | +0.086 |
| lives+50% | 0.500 | 0.509 | 0.559 | +0.0135 / 0.270 / 0.173 / **0.630** | +0.096 |

## 4. The real books replayed (step 3)

Each recorded book rebuilt as traders whose hidden limits, lives and relax shares are drawn (300 draws) so that every
recorded quote path is reproduced (`posterior_trader`; our matched traders' lives drawn ≥ what was seen); every
policy and the stall then run on the same draws.

| world | policy | eff | stall | margin | P(above) | P(below) | worst | E[points] |
|---|---|---|---|---|---|---|---|---|
| replay b120 | exact | 0.9364 | 0.9364 | +0.0000 | 0.000 | 0.000 | +0.000 | 0.500 |
| replay b120 | edge | 0.9364 | 0.9364 | +0.0000 | 0.000 | 0.000 | +0.000 | 0.500 |
| replay b120 | edge_none | 0.9364 | 0.9364 | +0.0000 | 0.000 | 0.000 | +0.000 | 0.500 |
| replay b120 | maxcount | 0.9364 | 0.9364 | +0.0000 | 0.000 | 0.000 | +0.000 | 0.500 |
| replay b120 | hold6 | 0.9364 | 0.9364 | +0.0000 | 0.000 | 0.000 | +0.000 | 0.500 |
| replay b120 | lookahead | 0.9250 | 0.9364 | -0.0114 | 0.017 | 0.173 | -0.171 | 0.502 |
| replay b120 | who_oracle | 0.9396 | 0.9364 | +0.0032 | 0.283 | 0.093 | -0.136 | 0.640 |
| replay b120 | oracle | 0.9811 | 0.9364 | +0.0447 | 0.840 | 0.000 | +0.000 | 0.920 |
| replay b137 | exact | 0.7775 | 0.7775 | +0.0000 | 0.000 | 0.000 | +0.000 | 0.500 |
| replay b137 | edge | 0.7775 | 0.7775 | +0.0000 | 0.000 | 0.000 | +0.000 | 0.500 |
| replay b137 | edge_none | 0.7775 | 0.7775 | +0.0000 | 0.000 | 0.000 | +0.000 | 0.500 |
| replay b137 | maxcount | 0.7085 | 0.7775 | -0.0689 | 0.000 | 0.993 | -0.146 | 0.456 |
| replay b137 | hold6 | 0.7775 | 0.7775 | +0.0000 | 0.000 | 0.000 | +0.000 | 0.500 |
| replay b137 | lookahead | 0.8524 | 0.7775 | +0.0749 | 0.823 | 0.170 | -0.224 | 0.899 |
| replay b137 | who_oracle | 0.7168 | 0.7775 | -0.0607 | 0.040 | 0.947 | -0.143 | 0.481 |
| replay b137 | oracle | 0.9198 | 0.7775 | +0.1424 | 0.980 | 0.000 | +0.000 | 0.990 |

And the live code fed the real JSON of each tick (scratchpad `live_look.py`, seeds 0–2):
- b137 t1783: exact crosses 12(32)×5(46) and 18(27)×3(65); lookahead keeps seller 12 for the bidders still to come
  (b137-2 bid 68 and b137-6 bid 69 arrived the next tick; 8 of 10 buyers seen). That is the +7.6 % on b137.
- b120 t1696: 18(38)×5(73) instead of 18×6(74) (b120-5 relaxing fast, about to leave; b120-6 new), and t1701: 15(35)×10(68) +
  22(73)×9(76), two pairs where exact (and the stall) made one; 22×10 crossed the next tick anyway, so this one is
  neutral in reality.

How much to trust b137's 0.90: the +7.6 % rests on seller b137-12 still being there at tick 1784. It was matched
the tick it arrived, so its real life is unobserved and is drawn from the prior (P(life ≥ 2) = 0.875). The real books
lean the same way (none of the 15 unmatched traders left after a single tick), but the replay validates the decision
on real quote paths, not its outcome.

## 5. Chosen policy and deployment

**Policy:** `BAZAAR_BENCH_POLICY=lookahead` on **bazaar-maker** only (`src/bazaar_agent/agents/bench_lookahead.py`,
wired in `agents/broker.py` `_with_lookahead`; commit `d4c5b328` on `feat/bench-beat-stall`, local).

| | value |
|---|---|
| E[points], session 9's world (`cal_normal20`) | **0.642** (exact 0.500, edge 0.513, edge_none 0.570) |
| P(above the stall) / P(below) | 0.293 / 0.140 |
| Mean margin over the stall | +2.0 % of the possible gains (oracle headroom +10 %) |
| Worst session in 300 | −0.22 efficiency → 0.5 × 0.78 ≈ 0.39 points (or −0.07 on a round average of three) |
| ±50 % on any parameter | 0.58–0.67 |
| Real replays | b137 (session 8, normal like session 9) 0.899; b120 (session 7, hard) 0.502 |
| Cost per tick | ≤ 0.1 s worst case (128 rollouts), mean 3 ms; no extra requests (same POSTs as exact, never more pairs than the book's crossing traders) |

What it does that exact does not (§4): holds a cheap seller when more bidders are due (b137 t1783), takes a relaxing
buyer about to leave before a fresh one (b120 t1696), crosses two pairs where the stall's zip stops at one (b120 t1701).
It never sends a pair whose quotes do not cross (test `test_it_only_ever_sends_pairs_whose_quotes_cross`).

**Deploy window for session 9 (t 17.0 ≈ 12:37, the last bench; /api/schedule):**
1. `railway variables --service bazaar-maker --set BAZAAR_BENCH_POLICY=lookahead --skip-deploys` (any time: the
   running code ignores an unknown value loudly and stays `exact`; `bench_config_from_env`).
2. Push `feat/bench-beat-stall`, PR, merge. A merge touching `src/**` redeploys maker, taker, duels and the rest:
   **only after Duels III has finished** and before ~12:25 (`scripts/merge_safe.sh`). Duels III starts at t 15.367
   (≈ 10:59). Duels II ran 612 duels at 16 duel ticks in 192 ticks (`feed_events` duels.scheduled t1239 →
   duels.finished t1431); at 12 duel ticks that scales to ~144 ticks ≈ 36 min at 15 s, so it should end ~11:35–11:40
   (watch for `duels.finished` "Duels III"). That leaves ~45 min for PR, CI, merge and deploy.
3. Check the maker's start line: `broker bench lookahead (exact unless a crossing matching earns more in rollouts)`.
   During the bench, deviations log `tick N broker: bench lookahead: k pair(s) instead of exact's m, +x expected P`,
   and their `broker_match` decisions carry the reason `bench lookahead: …`.
4. No restart, merge or variable change 12:35–12:42.

**Rollback:** `railway variables --service bazaar-maker --set BAZAAR_BENCH_POLICY=exact` (restarts the maker only),
outside 12:35–12:42. The code path falls back to the exact plan for a tick on any exception
(`bench lookahead failed (…); exact matching this tick`).

**No-merge fallback:** `BAZAAR_BENCH_POLICY=edge` + `BAZAAR_BENCH_GUARD_MARGIN=none` (maker-only restart): 0.570 in
the sim (P(above) 0.15), but on both real replays it never deviated from the stall (0.500).

## 6. Risks

- **One shot.** Session 9 is the last bench: P(above) ≈ 0.3 means ~70 % it ties or loses a little. Expected value is
  clearly positive under the real rule (+0.14 bench points ≈ +3 round market points), not a sure thing.
- **b120 replay is flat-to-negative** (0.502: above 2 %, below 17 %). Session 7 was the hard test; session 9 is normal
  like session 8 (b137: 0.899). Two real books are all the evidence there is.
- **The fitted model can be wrong** (traders a side, arrival window, lives). ±50 % on each parameter keeps
  E[pts] ≥ 0.58; the worst case is shorter lives (`lives-50%`: margin +0.1 %, P(above) = P(below) = 0.19).
- **Below-the-stall rule** is Saturday's fit (0.5·E/Es), not a published one; a harsher curve lowers E[points] by
  roughly P(below) × the extra penalty.
- **Other teams** could beat the stall in session 9 too; then the top-3 mean rises and a small win earns less than 1.0.
- **Run clock:** the planner places the run from the offers' `expires_tick` − 16 (true for b120 and b137); without
  it, from the first read, so a maker restart mid-run would shift its clock.
- **Merge blast radius:** the PR also redeploys bazaar-duels and the taker (hence the window).

## 7. Reproduce

```
uv run python scripts/bench_tournament.py --seeds 300 --robust --replay b120 b137 --draws 300   # replay: DATABASE_URL, read-only
uv run pytest tests/test_bench_lookahead.py
```

`scripts/bench_tournament.py` takes `--plugin file.py` (a `POLICIES` dict) so other strategy families plug in without
editing it; `--worlds` and `--only` narrow a run.
