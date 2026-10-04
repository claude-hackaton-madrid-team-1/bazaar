# Beating the stall on the Market Test: a lookahead that plays the points rule (Sun 4 Oct 2026)

Question: is there a broker policy that beats the SDK baseline (the free auto stall: per bench run, asks ascending
against bids descending, cross while bid >= ask, at the midpoint) on session 9 (~12:37 local, the last Market Test)?

## TL;DR

- **Yes, in simulation: `BAZAAR_BENCH_POLICY=lookahead_safe`** (new value; branch `feat/bench-search-292` on top of #292's `feat/bench-beat-stall`, local only; renamed from `lookahead` so it never collides with #292's own `lookahead`).
  **E[points] 0.65-0.68 a session against 0.50 for `exact`** (900 books for each of the three calibrated worlds),
  P(above stall) 0.32-0.37, P(below) 0.17-0.26. Its mean efficiency is also *above* the stall's (+0.2 to +1.3
  points of efficiency), so it is a better matcher, not only a bet on the points rule.
- It is **robust to the points rule below the stall**: if a venue under the stall got *nothing* (instead of
  Saturday's fitted 0.5·E/Es) it still makes **0.55-0.58**. In the 12 worlds with one parameter at ±50 % it makes
  0.60-0.70 (`edge_none` 0.54-0.59); on the replays of the real books 0.78 (b137) and 0.80 (b120).
- `lookahead_bold` (a session under the stall weighed at half the fitted 0.5·E/Es) makes **0.70-0.71**, P(above)
  0.43-0.46 but P(below) 0.33-0.41, and **0.52-0.55** if nothing is paid under the stall. Weighing it at the full fit
  (an earlier version) gambled more and did worse under *both* rules (0.68-0.70 / 0.44-0.48).
- **Recommendation: `lookahead_safe`** if the rule under the stall is uncertain; `lookahead_bold` gains ~0.04 points if
  Saturday's fit holds. Either beats `exact` under both rules in every world tried except shorter lives (−50 %),
  where `lookahead_safe` makes 0.604 / 0.497 (one book in four below the stall).
- The env flip is a maker-only restart in the 10:42-12:30 window (bench-sim's runbook); `BAZAAR_BENCH_POLICY` unset
  or `exact` keeps today's behaviour. Marius decides; nothing was pushed or set.

## Head-to-head with #292's `lookahead` (11:27)

Same harness (#292's `scripts/bench_tournament.py` at `15dc2cfc`), same books and seeds, 600 books a world, the 12
±50 % worlds, both replays (300 draws). E[points] under the linear rule / under a zero-below-stall rule
(E0 = P(above) + 0.5·P(tie)):

| world | exact | #292 lookahead | lookahead_safe | lookahead_bold |
|---|---|---|---|---|
| cal_normal20 | 0.500 / 0.500 | 0.628 / 0.556 | **0.668 / 0.601** | 0.713 / 0.568 |
| cal_hard24 | 0.500 / 0.500 | 0.641 / 0.528 | **0.672 / 0.571** | 0.717 / 0.542 |
| cal_normal20_uniform | 0.500 / 0.500 | 0.682 / 0.555 | **0.682 / 0.567** | 0.717 / 0.532 |
| old_normal20 | 0.500 / 0.500 | **0.669 / 0.520** | 0.656 / 0.517 | 0.670 / 0.460 |
| replay b120 (s7) | 0.500 / 0.500 | 0.502 / 0.422 | **0.806 / 0.653** | 0.808 / 0.653 |
| replay b137 (s8) | 0.500 / 0.500 | **0.899 / 0.827** | 0.783 / 0.610 | 0.783 / 0.610 |

In the ±50 % worlds `lookahead_safe` is ahead of #292 under the zero-below rule in 10 of 12 (behind at relax −50 %:
0.574 against 0.588; level at lives −50 %: 0.500 against 0.499) and under the linear rule in 12 of 12. P(above) /
P(below) on cal_normal20: safe 0.35 / 0.15, #292 0.27 / 0.16, bold 0.45 / 0.32. Worst margin: safe −0.34, #292
−0.22. Compute a tick (80 simulated sessions on a loaded laptop): #292 mean 7 ms, max 0.21 s; safe mean 11 ms,
max 0.33 s. Both are far inside the ~13 s budget.

**Verdict: no clear winner under the zero-below rule.** `lookahead_safe` wins the calibrated sims by about 0.01-0.05
points, but the two real replays split hard: b120 is safe +0.23, and b137 (session 8, a normal test like session 9)
is #292 +0.22, from one large holding gain (#292 margin +0.075 there). The mean of the two replays is level (0.632
against 0.625). My call is to keep #292 unless the sim worlds are weighted over the single b137 replay. The swap
is ready: branch `feat/bench-search-292` = `15dc2cfc` + the posterior planner, gates green,
`BAZAAR_BENCH_POLICY=lookahead_safe`.

## Why a different objective

The score is the share of the possible gains between true limits; bench points are 0.5 for matching the stall, the
mean of the top three for the full points (RULES.md "The Market Test"). Nobody has beaten the stall in seven
sessions (bench-baseline 10:29), so a venue alone above the stall is its own top three and gets **1.0**; under the
stall it gets 0.5·E/Es (bench-sim's Saturday fit) — a few hundredths below 0.5. So E[points] ≈ 0.5 + 0.5·P(above)
− (a few hundredths)·P(below): the objective is P(above stall), not mean efficiency. The server refuses non-crossing
pairs (t1692, 400 bad_match), so the only levers are WHICH quote-crossing traders match and WHEN.

Two facts that shape the planner:
- Pairing inside a matched set is irrelevant to the score (gain = Σ buyer values − Σ seller costs of the matched
  traders); only the *set* matters, and the set changes who is left for later ticks.
- Matching more pairs is not better: in b137 at t1779 the max-cardinality plan (17×4 and 15×0 instead of the
  stall's 17×0) adds v4 − c15, which the posterior puts below zero (v4 ≈ 60, c15 ≈ 85). `maxcount` loses on the
  b137 replay in 99 % of draws.

## The policy (`src/bazaar_agent/agents/bench_posterior.py`)

Each tick with at least one crossing pair:
1. **Posterior per seen trader**: over (limit, life, relax) on a grid, weight = prior(limit)/limit (the chance the
   opening quote rounds to what we saw) × prior(life) × prior(relax), kept only if the linear-relax quote path
   reproduces every quote seen within a prima. A trader that left unmatched lived exactly as long as it was seen.
   Priors are bench-sim's calibrated world `cal_normal20` (two-bump seller costs 25-42 / 65-110, values 40-105, asks
   1.05-1.45 × cost, bids 0.6-0.95 × value, 20 % firm, 25 % impatient, lives 1-2 / 3-6, arrivals over ticks 0-11).
   On the 44 real traders of b120 and b137, 43 paths fit; one falls back to "limit = last quote".
2. **96 sample worlds**: seen traders from their posteriors, unseen ones (10 a side minus those seen) from the
   prior, arriving after now.
3. **Candidates**: every set of asks and bids that can all cross at once (sorted asks against sorted bids), the
   empty set (hold everything) included.
4. **Score**: in each world, the stall is replayed from the run's first tick (on the quotes we actually saw, the
   model's only where we saw none), and each candidate is followed by the stall's own rule for us until the end.
   Points per world by the real rule, a session under the stall weighed by `below` × 0.5·E/Es (0 for
   `lookahead_safe`, 0.5 for `lookahead_bold`). The best candidate goes out only if it beats the stall's own set by
   `min_edge` = 0.01 points; otherwise the stall's own pairs are sent, with the stall's own pairing.

Broker wiring (`broker.py`): the lookahead's pairs are re-checked with `feasible()` and priced with `match_price()`
as the exact plan is (fee-safe), public matches take the slots left, any exception falls back to today's exact
matching for that tick and restarts the models. The session start comes from `expires_tick − 16` (true on b120 and
b137). Wall time: mean 5 ms, p99 32 ms, max 0.1 s a tick (1280 calls).

## Results

`uv run python scripts/bench_posterior_proof.py --seeds 900` (same books for every policy; `E0` = nothing below
the stall):

| world | policy | margin | P(above) | P(below) | worst | E[points] | E0[points] |
|---|---|---|---|---|---|---|---|
| cal_normal20 | exact | +0.0000 | 0.000 | 0.000 | +0.000 | 0.500 | 0.500 |
| cal_normal20 | edge_none | -0.0016 | 0.136 | 0.122 | -0.222 | 0.563 | 0.507 |
| cal_normal20 | **lookahead_safe** | **+0.0126** | 0.333 | 0.174 | -0.341 | **0.659** | **0.579** |
| cal_normal20 | lookahead_bold | +0.0095 | 0.441 | 0.334 | -0.275 | 0.706 | 0.553 |
| cal_hard24 | edge_none | -0.0000 | 0.176 | 0.161 | -0.166 | 0.583 | 0.507 |
| cal_hard24 | **lookahead_safe** | +0.0020 | 0.317 | 0.224 | -0.324 | **0.647** | **0.546** |
| cal_hard24 | lookahead_bold | -0.0009 | 0.431 | 0.384 | -0.321 | 0.698 | 0.523 |
| cal_normal20_uniform | edge_none | +0.0027 | 0.229 | 0.167 | -0.190 | 0.610 | 0.531 |
| cal_normal20_uniform | **lookahead_safe** | +0.0040 | 0.372 | 0.256 | -0.281 | **0.677** | **0.558** |
| cal_normal20_uniform | lookahead_bold | -0.0047 | 0.456 | 0.408 | -0.281 | 0.712 | 0.524 |

`cal_hard24` has 12 traders a side while the planner assumes 10: a mis-specified count costs little.

The weight of a session under the stall in the planner's own scoring (`below`, 900 books, E[points] / E0):

| below | cal_normal20 | cal_hard24 | cal_normal20_uniform |
|---|---|---|---|
| 0 (`lookahead_safe`) | 0.659 / 0.580 | 0.647 / 0.546 | 0.677 / 0.558 |
| 0.25 | 0.685 / 0.576 | 0.672 / 0.532 | 0.694 / 0.548 |
| 0.35 | 0.694 / 0.565 | 0.679 / 0.522 | 0.709 / 0.544 |
| 0.5 (`lookahead_bold`) | 0.706 / 0.553 | 0.698 / 0.523 | 0.712 / 0.524 |
| 1.0 (the fitted rule itself) | 0.696 / 0.479 | 0.680 / 0.435 | 0.692 / 0.454 |

Robustness (`--robust`: cal_normal20 with one parameter at ±50 %, 300 books each), E[points] / E0:

| world | lookahead_safe | lookahead_bold | edge_none (E) |
|---|---|---|---|
| firm −50 % | 0.665 / 0.595 | 0.712 / 0.575 | 0.560 |
| impatient −50 % | 0.697 / 0.638 | 0.738 / 0.617 | 0.568 |
| relax −50 % | 0.660 / 0.585 | 0.708 / 0.555 | 0.565 |
| shade −50 % | 0.662 / 0.562 | 0.713 / 0.540 | 0.539 |
| cheap share −50 % | 0.622 / 0.567 | 0.651 / 0.512 | 0.551 |
| lives −50 % | 0.604 / 0.497 | 0.660 / 0.492 | 0.557 |
| firm +50 % | 0.666 / 0.600 | 0.711 / 0.565 | 0.571 |
| impatient +50 % | 0.652 / 0.577 | 0.704 / 0.557 | 0.562 |
| relax +50 % | 0.657 / 0.585 | 0.716 / 0.582 | 0.559 |
| shade +50 % | 0.633 / 0.555 | 0.679 / 0.547 | 0.548 |
| cheap share +50 % | 0.671 / 0.587 | 0.721 / 0.568 | 0.592 |
| lives +50 % | 0.658 / 0.568 | 0.694 / 0.552 | 0.559 |

Real-book replay (`bench_tournament.py --plugin scripts/bench_posterior_proof.py --replay b137 b120 --draws 300`,
posterior draws behind the recorded quote paths; both variants take the same decisions there):
b137 (session 8) 0.783, P(above) 0.60, P(below) 0.38, margin +0.0026; b120 (session 7) 0.803-0.804, 0.65 / 0.35,
margin −0.0004 / +0.0010. Every policy that never leaves the stall's set (exact, edge*, hold*) scores exactly 0.5.
A replay is one real book under posterior uncertainty, so its P(above) is a per-book number, not a session rate.

Sample count (300 books, `lookahead_safe`): 48 samples 0.674 / 0.649 (normal / hard), 96 0.668 / 0.657, 192 0.663 /
0.642, 256 0.658 / 0.643: more samples = fewer deviations and a higher mean margin; 96 is kept. A minimum edge of
0 / 0.01 / 0.02 / 0.04 points: 0.673 / 0.668 / 0.659 / 0.639 on cal_normal20 (0.01 kept). Differences under ~0.03
are within noise at 300 books (SE of P(above) ≈ 0.03; ≈ 0.016 at 900).

## Caveats

- **Model risk.** All of this is in bench-sim's calibrated worlds, fitted to two real books. The planner's prior
  *is* the main world's generator; the ±50 % worlds and the replays are the only out-of-model checks. The stall's
  headroom in the sim (oracle − stall ≈ 8-10 %) is larger than s7's real ~3 %, so real P(above) may be lower.
- **Queued matches.** The real server queues a match and settles it next tick (`settles_at_tick`, bench_evidence);
  the sim settles at once. Every queued match in s7 and s8 settled. A pair the lookahead *holds* and sends on a
  trader's last tick is the untested case; if a held trader leaves before the queued match settles, the match is lost.
- **Points rule.** "Alone above = 1.0" assumes no other team beats the stall in session 9; under that assumption
  `lookahead_safe`'s positive mean margin is also the safer side if one does.
- The rule under the stall (0.5·E/Es) is bench-sim's fit, not mine; `E0` is the downside if it is wrong.

## Files

- `src/bazaar_agent/agents/bench_posterior.py` — the planner (pure).
- `src/bazaar_agent/agents/broker.py` — `BAZAAR_BENCH_POLICY=lookahead_safe | lookahead_bold`, fallback to exact.
- `tests/test_bench_posterior.py` — env parsing, quiet book, last tick, candidate sets, posterior, points rule,
  re-opened traders, the broker's reasons and fallback, the proof script.
- `scripts/bench_posterior_proof.py` — the table above; also a `--plugin` for `bench_tournament.py --replay`.
- Builds on bench-sim's harness `scripts/bench_tournament.py` (cherry-picked from `6dd8e013`).
