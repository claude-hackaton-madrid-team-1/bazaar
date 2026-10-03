# B12 · Unpredictable dealer bids (night shift, 3 Oct 2026)

Draft PR #100, stacked on #81 (`night/w3-ladder`), which is stacked on #61. Nothing went live. The data is Friday's dealer threads (fixture), W3's fitted dealers, and the #55 simulator's dealers from a scratch merge that was never committed. Full tables: `b12-dealer-jitter-tables.md`.

## Verdict
- **Build: GO, off by default.** With every `dealer_jitter_*` knob at 0, `decide()` sends today's ladder, byte for byte (tested).
- **Invariants hold over 67.2 M conversations** (55.2 M model across two seeds, 12.1 M simulator):
  - 0 repeated prices and 0 bids above max;
  - deals at her opening price: 534 at the recommended level against 544 with jitter off, all from replayed real threads.
- **Invariant tests:** 51 tests, each property checked over 1,500 seeds.
- **Recommended setting** (in-sample; if Marius wants it on): `start_spread` 1, `band_jump_share` 0.35, `jump_max` 2, `band_gap` 3. In plain terms: open 0–1 under the plan's start; 35 % of the raises inside the band are +2, but only while her ask is still ≥ 3 above where the raise lands.
  - 47 of 377 levels pass all three evaluations. This is the least predictable of them.
  - Worst Abuela cell: 0.981 × today's share (model, 2 seeds) and 0.958 × (simulator). Mean 1.005 ×.
  - Cost in time: +0.9 ticks per deal at 2 ticks per round.
  - A rival's exact-price hit rate on our bids drops from 1.00 to 0.72 (model) and 0.61 (simulator); on the opening bid alone, to 0.47.
- **Value: unquantified.** Each team has its own dealer allotment and each conversation its own secret limit, so a rival who predicts our dealer bids has no scored lever at the dealer. We measured the cost, not a gain. My call: leave it off unless Marius sees a rival learning from our bids (duels, team trades).

## What changed (all behind STRATEGY.md, defaults = today)
- `dealer.StepJitter` on `BidPlan.jitter`. Every draw is a pure function of (seed, salt = thread id, bid index). Deciding twice on the same state sends the same bid.
- **First bid:** the start minus a random 0..`start_spread`.
- **Below the start:** with `jump_share`, a jump of base + 1..`jump_max`. It lands at most on the start, and at most her ask minus the gap.
- **From the start up:** with `band_jump_share`, a jump of the same size. With `band_gap` > 0, only while her standing ask (never below her secret limit) is ≥ gap above where the jump lands.
- **Base step** = max(plan step, `dealer_min_step_pct` × max), our stand-in for book. The B12 rule: she never moves faster than our last step, and a step under 2 % of book earns nothing. `counter_below` targets her ask minus the base step and keeps its +1 floor, because a bid that reaches her limit is still taken.
- **Seed:** `dealer_jitter_seed = 0` draws a seed per process. It is never logged (`repr=False`, r1), because a known seed plus the public thread id replays our bids.
- **Wiring:** the desk (`agent taker`) and `bazaar dealer buy` both follow the knobs, so runtime-launched buys match the desk. `--no-jitter` / `--jitter` override.
- **Evaluation:** `jitter_eval.py` (harness, step-capped dealer, rival predictor) and `scripts/dealer_jitter.py` (a 377-level grid with the gate below).

## How it was measured
- **Plans:** W3's floor plans (Abuela 8→12, 21→25, pack 20→24; Chato 27→31, 89→93, uncapped) and today's lowest-fill ladders (7→12, 17→26, 17→20).
- **Dealers, five ways:**
  - W3's fitted models;
  - the same models under B12's step-capped rule;
  - every real Friday thread replayed with its limit at the top and at the bottom of its bracket (10 jitter draws each);
  - the simulator.
  - Each at 1 and at 2 ticks per round (Friday's Abuela answered on the next tick 72 % of the time).
- **Gate:** Abuela share ≥ 0.95 × the deterministic plan in every cell of all three evaluations (a missed deal scores 0), and a deal takes at most +1 tick on average at 2 ticks per round (r1). Abuela allots 8 deals per hour, and one thread per dealer runs at a time.
- **Rival model:** it reads half of our threads and learns our next bid per (class, bid index, last bid), backing off to the most common step. It then predicts every bid of the other half. This is the best a reader of a stationary policy can do; it does not use her asks.

## Frontier (excerpt; worst cell = min over Abuela classes × plans × dealer models × speeds)
| level | spread / below / band / max / gap | worst share × model (2 seeds) | worst share × sim | mean share × | extra ticks per deal | Chato worst (model / sim) | hit all (model / sim) | hit first |
|---|---|---|---|---|---|---|---|---|
| off (today) | 0 / 0 / 0 / – / – | 1.000 | 1.000 | 1.000 | +0.00 | 1.000 / 1.000 | 1.000 / 1.000 | 1.000 |
| band only, no time cost | 0 / 0 / 0.2 / 2 / 2 | 0.978 | 0.981 | 1.001 | +0.00 | 0.964 / 0.965 | 0.929 / 0.953 | 1.000 |
| **recommended** | 1 / 0 / 0.35 / 2 / 3 | **0.981** | **0.958** | **1.005** | **+0.90** | 0.947 / 0.965 | **0.717 / 0.613** | 0.474 |
| more margin in the sim | 1 / 0 / 0.2 / 2 / 3 | 0.971 | 0.966 | 1.003 | +0.93 | 0.947 / 0.980 | 0.745 / 0.631 | 0.474 |
| spread 2 (fails on time) | 2 / 0.5 / 0.2 / 2 / 3 | 0.956 | 0.965 | 1.001 | +1.60 ✗ | 0.957 / 0.981 | 0.671 / 0.498 | 0.340 |
| same without gap | 2 / 0.5 / 0.2 / 2 / 0 | 0.948 ✗ | 0.952 | 0.989 | +1.38 ✗ | 0.949 / 0.966 | 0.632 / 0.492 | 0.340 |
| big band jumps | 0 / 0 / 0.35 / 4 / 0 | 0.828 ✗ | 0.874 ✗ | 0.947 | +0.00 | 0.841 / 0.863 | 0.867 / 0.922 | 1.000 |

- **Where the cost is.** W3's plan starts at the bottom of the limit band (Abuela uncommon limits p10–p90 = 21–25), so any step > 1 inside the band can overshoot her limit: 1 P = 0.17 share on uncommons, 0.5 on commons.
- **The band gap** is what makes in-band jumps affordable. A 20 % band share with +2 jumps scores 0.951 without the gap and 0.983 with gap 2; with a spread of 2 the same share fails without it (0.948). With the gap, a +2 jump happens only while her ask (never below her limit) is ≥ 3 above the landing. In the simulator, whose Abuela holds her ask far above her limit, the gap protects less (worst cell 0.958).
- **A lower opening bid** costs no overshoot but costs bids, so time: +0.9 ticks per deal for 1 primas of spread, +1.4–1.6 for 2.
- **Recommended level per Abuela class** (fitted W3, 2 ticks per round):
  - commons 8→12: share 0.976 → 0.981, ticks 5.0 → 5.9;
  - uncommons 21→25: 0.944 → 0.935, ticks 5.2 → 5.8, deal rate 0.993 → 0.986;
  - pack 20→24: 0.898 → 0.899.
  - Fill within 8 ticks drops most on today's common ladder (0.90 → 0.66), because one more round crosses the 8-tick line.
- **El Chato** stays ≥ 0.95 × in the simulator. In the model, 0.947 × is the floor of every spread-1 level. It is set by the rare on the real threads with the limit at the top: 0.948 → 0.898. The 2 % rule alone (a step-1 plan climbs 89 / 91 / 93) already gives 0.916.

## What Marius must decide
1. **Turn it on or not:** off (default), the recommended four knobs, or band-only (`band_jump_share` 0.2, `jump_max` 2, `band_gap` 2: no time cost, little hiding). Unpredictability scores nothing by itself (see Verdict).
2. **`dealer_min_step_pct` = 0.02** applies only with a jitter on. The "2 % of book" rule is not in RULES.md, the fixtures, the feed capture or the simulator; it comes from the B12 brief. On its own it takes Chato rare share on the real threads (limit at the top) from 0.948 to 0.916. Set it to 0 if the rule is not real.
3. **Merge order:** after #81. #72 conflicts in `dealer.py`, `taker.py` and `cli.py` (other stack). To re-apply after #72:
   - `may_close` is now `may_take` (one test);
   - `counter_below` should use `base_step`;
   - #72's `reopen_start` reads `neg.bids[0]`, which a jitter lowers, so the reopened thread starts 1 lower.

## Risks
- **In-sample:** the knobs were chosen on Friday's threads (Abuela 31 / 58 / 51, Chato 12 / 15) and on the simulator, whose Abuela is more patient than the real one. Re-run `scripts/dealer_jitter.py` on Saturday's feed before relying on the margins.
- The rival model does not condition on her asks. With a band gap, a reader who does would predict a little better than 0.72.
- `dealer buy` now reads STRATEGY.md, so an invalid STRATEGY.md stops it unless `--no-jitter` is passed (the desk already needed STRATEGY.md).
- `jump_max` is absolute. For a class whose base step is ≥ `jump_max` (Chato rare under the 2 % rule) there are no jumps, only the start spread.
