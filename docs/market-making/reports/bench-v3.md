# Bench v3: what session 9 taught the harness, and every policy rerun

Harness only (`scripts/bench_tournament.py`, `scripts/bench_lookahead_v2_plugin.py`; no `src/**` change). Raw tables are in
[bench-v3/](bench-v3/): `worlds.md`, `miss.md`, `replay-b155.md`, `replay-b120-b137.md`, `robust.md`, `fit.md`,
`whatwedid-b155.md`. Seeds: 300 per world (miss runs: cal_normal20 and cal_hard24 only), 100 for the robust worlds,
200 posterior draws per replay (b155 `--whatwedid`: 300). Three points readings are in every table:
**linear** (below the stall: 0.5·E/Es), **zero** (nothing below), **round-avg** ((0.5 + 0.5 + this session linear)/3: what
`/me bench_points` would read after the session if it is the Sunday round average).

## TL;DR

- **Session 9 did not break the model, it fell inside it.** Replaying b155 under posterior draws, our six real pairs
  score efficiency 0.727 against the stall's 0.830 (E/Es 0.88, P(below) 0.93, linear points 0.467, round-avg 0.489). The
  observed 0.472 is close to the per-session linear reading; the round-average reading would need ~0.416 (E/Es 0.83).
  The replay cannot separate the two (the draw spread covers both), so **the "`/me` is the round average" reading is
  plausible (it explains the board's −0.25) but not verified.** Treat both as live.
- **Why we lost to the stall on b155 (replay estimate).** Giving the stall's choice at BOTH the skipped tick (2262) and the
  deviation tick (2264) closes 84 % of the gap (−0.103 → −0.017 efficiency). Separately they do not add up: the stall at
  2262 alone makes things worse (−31 % of the gap) because the ghost buyer it would have matched interacts with
  later pairs we sent; the stall at 2264 alone closes 10 %. Read it as: the skipped tick and the deviation are jointly most of
  the loss, and the skip cost something only because we then deviated. The unseen buyer b155-2 is a modelled ghost
  (one tick, quote drawn from seen buyers), so the 2262 share is soft.
- **Best policy per reading** (cal_normal20; hard24 and uniform agree on the order):
  - linear: `lookahead_bold` 0.707 > `slack4` 0.691 > `lookahead_safe` 0.668 = `slack2` 0.668 > `slack1` 0.656 > `lookahead` 0.642 (stall 0.500).
  - zero-below: `lookahead_safe` 0.602 > `slack1` 0.587 > `lookahead` 0.577 > `slack2`/`slack4` 0.577–0.578 > `lookahead_bold` 0.563 (stall 0.500).
  - round-avg: `lookahead_bold` 0.569 > `slack4` 0.564 > `lookahead_safe` 0.556 = `slack2` 0.556 (stall 0.500). The scale is compressed (max 0.667).
- **Would any policy have beaten the stall on b155?** Only `lookahead_bold` in the replay (P(above) 0.61, P(below) 0.39,
  margin +0.038, linear 0.785, zero 0.613). `lookahead_safe` ties (0.523 / 0.443 zero), `lookahead` and `slack1/2` lose
  slightly (0.497 / 0.458 zero), `slack4` 0.520 / 0.400, `hold6` loses (0.330 P(below)). Everything stall-like ties at 0.500.
  Under zero-below nothing but `lookahead_bold` is above 0.5, and the replay is in-sample.
- **Downtime hurts the stall-like brokers too, and policies that think keep their edge.** At p = 0.06 per tick our `exact`
  falls below the stall in 11 % of cal_normal20 sessions (margin −0.013, zero-below 0.453). `lookahead_safe` and `slack1`
  stay slightly positive in margin (+0.002 / +0.006) and keep 0.55 zero-below; `lookahead_bold` goes to −0.002 margin.
  On the hard world at 0.06 every lookahead is slightly negative in margin but still 0.5–0.65 linear.
- **Lookahead v2 slack variants loaded** from af794985 through a plugin (git show, nothing in src). Slack helps the
  linear reading (slack4 0.691) and costs the zero reading (0.577 vs `lookahead_safe` 0.602); on b137 `slack1/2` are the best
  non-oracle rows (+0.08), on b155 they lose like `lookahead`.

## 1. The b155 fit check

[bench-v3/fit.md](bench-v3/fit.md). The simulated mean is `cal_normal20` playing the stall under the same observation
process. b155 differs from the world mostly in level: sellers open much lower (median first ask 49.5 vs 70.3 simulated,
because the world mixes a dear bump at 65–110; b155 had ~half its sellers in the cheap bump), steps are smaller (3.5 vs 5.3
P a tick), more of the book matched (0.63 vs 0.50). Firm share (0.23 vs 0.20), one-tick sightings (0.32 vs 0.40) and
life (3.5 vs 3.4 ticks) fit. The **replay** uses the recorded paths, so the level offset affects only the draws' hidden limits.
Tick 2262 has no rows: the loader now fills each trader's gap tick by interpolation, and adds one ghost buyer for b155-2.

## 2. Worlds: every policy, 300 seeds (E[points]; margin = efficiency over the stall)

| policy | cal_normal20 margin | linear | zero | round | cal_hard24 linear | zero | uniform linear | zero | old_normal20 linear | zero |
|---|---|---|---|---|---|---|---|---|---|---|
| stall / exact | 0 | 0.500 | 0.500 | 0.500 | 0.500 | 0.500 | 0.500 | 0.500 | 0.500 | 0.500 |
| edge20 | +0.0004 | 0.503 | 0.503 | 0.501 | 0.502 | 0.500 | 0.503 | 0.502 | 0.500 | 0.498 |
| edge (margin 10) | +0.0012 | 0.513 | 0.510 | 0.504 | 0.519 | 0.505 | 0.540 | 0.507 | 0.543 | 0.515 |
| edge5 | +0.0019 | 0.542 | 0.520 | 0.514 | 0.555 | 0.525 | 0.581 | 0.528 | 0.571 | 0.517 |
| edge0 = edge_none | +0.0015 | 0.570 | 0.532 | 0.523 | 0.583 | 0.503 | 0.636 | 0.552 | 0.598 | 0.490 |
| edgecal_none | −0.0022 | 0.573 | 0.513 | 0.524 | 0.587 | 0.492 | 0.645 | 0.552 | 0.593 | 0.455 |
| edgecal5 | −0.0002 | 0.556 | 0.520 | 0.519 | 0.567 | 0.508 | 0.626 | 0.553 | 0.584 | 0.478 |
| maxcount | −0.0019 | 0.544 | 0.505 | 0.515 | 0.557 | 0.485 | 0.632 | 0.550 | 0.579 | 0.455 |
| hold3 | +0.0009 | 0.505 | 0.500 | 0.502 | 0.506 | 0.500 | 0.512 | 0.492 | 0.519 | 0.500 |
| hold6 | +0.0036 | 0.538 | 0.507 | 0.513 | 0.538 | 0.485 | 0.525 | 0.433 | 0.575 | 0.498 |
| lookahead (#292) | +0.0199 | 0.642 | 0.577 | 0.547 | 0.639 | 0.538 | 0.682 | 0.543 | 0.660 | 0.502 |
| lookahead_safe | +0.0187 | 0.668 | 0.602 | 0.556 | 0.671 | 0.573 | 0.686 | 0.570 | 0.657 | 0.518 |
| lookahead_bold | +0.0143 | 0.707 | 0.563 | 0.569 | 0.717 | 0.542 | 0.724 | 0.543 | 0.669 | 0.447 |
| slack1 (v2) | +0.0191 | 0.656 | 0.587 | 0.552 | 0.653 | 0.532 | 0.688 | 0.537 | 0.677 | 0.502 |
| slack2 (v2) | +0.0165 | 0.668 | 0.578 | 0.556 | 0.670 | 0.532 | 0.686 | 0.512 | 0.681 | 0.493 |
| slack4 (v2) | +0.0145 | 0.691 | 0.577 | 0.564 | 0.709 | 0.563 | 0.701 | 0.513 | 0.670 | 0.458 |
| who_oracle (bound) | +0.0049 | 0.675 | 0.615 | 0.558 | 0.695 | 0.598 | 0.709 | 0.617 | 0.635 | 0.485 |
| oracle (all limits and exits) | +0.1005 | 0.932 | 0.932 | 0.644 | 0.955 | 0.955 | 0.960 | 0.960 | 0.970 | 0.970 |

P(above) / P(below) on cal_normal20: `lookahead` 0.293 / 0.140, `lookahead_safe` 0.347 / 0.143, `lookahead_bold` 0.440 / 0.313,
`slack1` 0.323 / 0.150, `slack4` 0.403 / 0.250, `edge_none` 0.147 / 0.083. Full table with eff, stall, worst: [worlds.md](bench-v3/worlds.md).
`who_oracle` (knows every limit, no holding) is **below** `lookahead_safe` in zero-below on old_normal20 and below the
lookaheads in margin in most worlds: timing, not limits, is what pays, as in [bench-sim](bench-sim.md).

**Robust (12 perturbed worlds, 100 seeds; [robust.md](bench-v3/robust.md)).** Min–max of E linear / zero-below / round-avg
per policy over the 12 worlds (plus the unperturbed one): `lookahead_safe` 0.61–0.69 / 0.50–0.64 / 0.54–0.56;
`lookahead` 0.56–0.68 / 0.47–0.63; `lookahead_bold` 0.67–0.73 / 0.48–0.62; `slack1` 0.60–0.70 / 0.47–0.65;
`slack4` 0.63–0.73 / 0.44–0.64; `edge_none` 0.53–0.59 / 0.47–0.55; `hold6` 0.51–0.55 / 0.46–0.52.
Only `lookahead_safe` never goes below 0.50 under zero-below.

## 3. Broker downtime (read missed with probability p, stall always acts)

E[points] linear / zero ([miss.md](bench-v3/miss.md)); the stall rows are our broker's own stall logic under the same misses.

| policy | normal p=0 | 0.03 | 0.06 | hard p=0 | 0.03 | 0.06 |
|---|---|---|---|---|---|---|
| stall / exact | 0.500 / 0.500 | 0.495 / 0.468 | 0.500 / 0.453 | 0.500 / 0.500 | 0.500 / 0.477 | 0.509 / 0.462 |
| edge_none | 0.570 / 0.532 | 0.563 / 0.500 | 0.566 / 0.487 | 0.583 / 0.503 | 0.577 / 0.478 | 0.576 / 0.462 |
| lookahead | 0.642 / 0.577 | 0.636 / 0.550 | 0.634 / 0.540 | 0.639 / 0.538 | 0.623 / 0.497 | 0.609 / 0.457 |
| lookahead_safe | 0.668 / 0.602 | 0.660 / 0.570 | 0.652 / 0.552 | 0.671 / 0.573 | 0.653 / 0.530 | 0.649 / 0.512 |
| lookahead_bold | 0.707 / 0.563 | 0.694 / 0.532 | 0.680 / 0.498 | 0.717 / 0.542 | 0.699 / 0.505 | 0.684 / 0.477 |
| slack1 | 0.656 / 0.587 | 0.648 / 0.560 | 0.648 / 0.548 | 0.653 / 0.532 | 0.636 / 0.488 | 0.626 / 0.457 |
| slack4 | 0.691 / 0.577 | 0.675 / 0.543 | 0.665 / 0.522 | 0.709 / 0.563 | 0.694 / 0.525 | 0.679 / 0.493 |

Each 3 % of missed reads costs about 0.03 zero-below points; the stall level is a moving target (a miss on a tick the
engine would have matched is a loss relative to it). `lookahead_safe` degrades least. Mean margin over the stall at p = 0.06 on cal_normal20:
`exact` −0.013, `edge_none` −0.012, `lookahead` +0.006, `lookahead_safe` +0.002, `slack1` +0.006, `lookahead_bold` −0.002.

## 4. b155 replayed ([replay-b155.md](bench-v3/replay-b155.md), [replay-b120-b137.md](bench-v3/replay-b120-b137.md))

E[points] linear / zero, round-avg last; P(above)/P(below). **In-sample** (priors fitted on b120/b137), and the replay's stall level is off.

| policy | b155 | b155 miss 0.03 | b155 miss 0.06 | b120 | b137 |
|---|---|---|---|---|---|
| stall/exact/edge/maxcount | 0.500 / 0.500 | 0.502 / 0.482 | 0.511 / 0.472 | 0.500 / 0.500 | 0.500 / 0.500 (maxcount 0.455 / 0.003) |
| lookahead | 0.497 / 0.458 (0 / 0.09) | 0.496 / 0.440 | 0.505 / 0.435 | 0.501 / 0.417 | **0.894 / 0.818** |
| lookahead_safe | 0.523 / 0.443 (0.05 / 0.17) | 0.518 / 0.405 | 0.527 / 0.400 | 0.809 / 0.660 | 0.789 / 0.618 |
| lookahead_bold | **0.785 / 0.613** (0.61 / 0.39) | 0.772 / 0.588 | 0.753 / 0.560 | 0.811 / 0.660 | 0.789 / 0.618 |
| slack1 / slack2 | 0.497 / 0.458 | 0.496 / 0.440 | 0.505 / 0.435 | 0.501–0.508 / 0.417 | 0.904–0.907 / 0.838–0.843 |
| slack4 | 0.520 / 0.400 | 0.511 / 0.378 | 0.510 / 0.367 | 0.765 / 0.562 | 0.907 / 0.843 |
| hold6 | 0.487 / 0.335 | 0.485 / 0.315 | 0.494 / 0.305 | 0.500 / 0.500 | 0.500 / 0.500 |
| who_oracle | 0.524 / 0.497 | 0.521 / 0.475 | 0.530 / 0.468 | 0.638 / 0.600 | 0.483 / 0.050 |
| oracle | 0.920 / 0.920 | same | same | 0.917 | 0.995 |

b155 is a book on which only one policy wins, so s9's "no edge" is what the replay predicts for `lookahead_safe`
(margin −0.003, P(above) 0.05). The three replays disagree about who is best: `lookahead` / `slack` on b137, `lookahead_safe` /
`bold` on b120, `bold` alone on b155.

## 5. What we did on b155, against the stall ([whatwedid-b155.md](bench-v3/whatwedid-b155.md))

Our real sends (decisions 6453–6486: S11×B4 t2256, S18×B0 t2257, S10×B9, S14×B7, S12×B8 t2263, S15×B6 t2264) replayed
under 300 posterior draws; "stall acts" substitutes the engine's choice at that tick.

| variant | eff | margin | P(below) | E/Es | linear | zero | round-avg |
|---|---|---|---|---|---|---|---|
| what we did | 0.7274 | −0.1026 | 0.927 | 0.879 | 0.467 | 0.065 | 0.489 |
| + stall acts at the skipped tick (2262) | 0.6956 | −0.1343 | 0.927 | 0.841 | 0.448 | 0.065 | 0.483 |
| + stall acts at the deviation tick (2264) | 0.7378 | −0.0922 | 0.773 | 0.891 | 0.446 | 0.113 | 0.482 |
| + both | 0.8131 | −0.0169 | 0.107 | 0.980 | 0.490 | 0.447 | 0.497 |
| the stall | 0.8300 | 0 | 0 | 1 | 0.500 | 0.500 | 0.500 |

The replay stall level 0.83 is below the server's (round `/me bench_efficiency` 0.841–0.895 range): the level offset
caveat. Zero-below, "what we did" is 0.065: **if the server scored below-stall sessions at zero we would have lost almost the whole
session**; the s9 board move (−0.25 for t01, 13 teams flat) says the below-stall penalty is small, which is the linear reading.

## 6. Does `/me bench_points` read as the round average?

`/me` 0.500 → 0.472 and the board's −0.25 fit "round average of three sessions": s9 alone ≈ 3·0.472 − 1.0 = 0.416 (E/Es 0.83),
and 0.472 alone would move the board about −0.07 on the 22.5 × bench weighting. The replay says our sends score
E/Es 0.88 (linear 0.467 ± the draw spread), close to the per-session value and a little above the round-average one. Both
are inside the draw spread, so **not verified**; what would settle it: `/me` after session 10 (a stall-level session should
move the average back up by ~0.03 under the round reading and not at all under the per-session one).

## Caveats

- The b155 and b120/b137 replays use priors fitted on b120/b137 (in-sample), and b155's cheap-seller bump is half the
  world's mix: its stall level (0.83) is below the server's. Only signs of margins are trustworthy.
- The ghost buyer for the skipped tick is a modelled guess; the share of the gap attributed to 2262 is soft.
- Attribution variants do not add (interactions with later scripted pairs): quote the "both" row, not the pieces.
- Downtime model: misses are independent per tick, the policy sees nothing on a miss (its internal state does not advance).
  The real 2262 miss may have a cause correlated with load.
- The round-average reading is a monotone rescaling of linear ((1 + p)/3); it changes nothing in the order, only what the
  number on `/me` would read.
- 300 seeds: differences under ~0.02 E[points] between neighbours are noise; use the P columns.
- Slack variants are loaded from commit af794985 (needs it in the local clone) with the wall-clock budget switched off.
