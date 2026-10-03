# B12 · Unpredictable dealer bids (night shift, 3 Oct 2026)

Draft PR #100, stacked on #81 (`night/w3-ladder`), which is stacked on #61. Nothing went live. The data is Friday's dealer threads (fixture), W3's fitted dealers, and the #55 simulator's dealers from a scratch merge that was never committed. Full tables: `b12-dealer-jitter-tables.md`.

## Verdict
- **Build: GO, off by default.** With every `dealer_jitter_*` knob at 0, `decide()` sends today's ladder, byte for byte (tested).
- **Invariants hold over 39.6 M conversations** (27.6 M model, 12.1 M simulator):
  - 0 repeated prices and 0 bids above max;
  - deals at her opening price: 550 against 544 with jitter off, all from replayed real threads.
- **Invariant tests:** 49 tests, each property checked over 1,500 seeds.
- **Recommended setting** (if Marius wants it on): spread 2, below-start jump share 0.5, in-band jump share 0.2, jump max 2, band gap 3.
  - 62 of 377 levels keep Abuela share ≥ 0.95 × the deterministic plan in every cell of all three evaluations. This one is the least predictable of them whose worst cell, El Chato included, stays ≥ 0.955.
  - A rival's exact-price hit rate on our bids drops from 1.00 to 0.66 (model) and 0.50 (simulator). On the opening bid alone it drops from 1.00 to 0.34.
  - Mean share is 1.000 × today's; the worst cell is 0.956.
- **Value: unquantified.** Each team has its own dealer allotment and each conversation its own secret limit, so a rival who predicts our dealer bids has no scored lever at the dealer. We measured the cost, not a gain. My call: leave it off unless Marius sees a rival learning from our bids (duels, team trades).

## What changed (all behind STRATEGY.md, defaults = today)
- `dealer.StepJitter` on `BidPlan.jitter`. Every draw is a pure function of (seed, salt = thread id, bid index). Deciding twice on the same state sends the same bid.
- **First bid:** the start minus a random 0..`start_spread`.
- **Below the start:** with `jump_share`, a jump of base + 1..`jump_max`, clipped so it lands at most on the start.
- **From the start up:** with `band_jump_share`, a jump of the same size. With `band_gap` > 0, only while her standing ask (never below her secret limit) is ≥ gap above where the jump lands.
- **Base step** = max(plan step, `dealer_min_step_pct` × max), our stand-in for book. The B12 rule: she never moves faster than our last step, and a step under 2 % of book earns nothing. `counter_below` uses the same base step and keeps its +1 floor, because a bid that reaches her limit is still taken.
- **Seed:** `dealer_jitter_seed = 0` draws a seed per process. It is never logged (`repr=False`), because a known seed plus the public thread id replays our bids.
- **Wiring:** the desk (`agent taker`) applies the jitter per thread. `bazaar dealer buy --jitter` is opt-in.
- **Evaluation:** `jitter_eval.py` (harness, step-capped dealer, rival predictor) and `scripts/dealer_jitter.py` (the grid).

## How it was measured
- **Plans:** W3's floor plans (Abuela 8→12, 21→25, pack 20→24; Chato 27→31, 89→93, uncapped) and today's lowest-fill ladders (7→12, 17→26, 17→20).
- **Dealers, five ways:**
  - W3's fitted models;
  - the same models under B12's step-capped rule;
  - every real Friday thread replayed with its limit at the top and at the bottom of its bracket (10 jitter draws each);
  - the simulator.
  - Each at 1 and at 2 ticks per round (Friday's Abuela answered on the next tick 72 % of the time).
- **Rival model:** it reads half of our threads and learns our next bid per (class, bid index, last bid), backing off to the most common step. It then predicts every bid of the other half. This is the best a reader of a stationary policy can do; it does not use her asks.

## Frontier (excerpt; worst cell = min over Abuela classes × plans × dealer models × speeds)
| level | spread / below / band / max / gap | worst share × (model, 2 seeds) | worst share × (sim) | mean share × | Chato worst (model / sim) | hit all (model / sim) | hit first |
|---|---|---|---|---|---|---|---|
| off (today) | 0 / 0 / 0 / – / – | 1.000 | 1.000 | 1.000 | 1.000 / 1.000 | 1.000 / 1.000 | 1.000 |
| s0b0.1m2 (band only) | 0 / 0 / 0.1 / 2 / 0 | 0.969 | 0.986 | 0.995 | 0.967 / 0.965 | 0.953 / 0.978 | 1.000 |
| s1b0.1m2 | 1 / 0 / 0.1 / 2 / 0 | 0.960 | 0.960 | 0.992 | 0.947 / 0.978 | 0.745 / 0.636 | 0.474 |
| s2j1b0.2m2g3 | 2 / 1 / 0.2 / 2 / 3 | 0.969 | 0.959 | 1.006 | 0.957 / 0.982 | 0.710 / 0.538 | 0.340 |
| **s2j0.5b0.2m2g3 (recommended)** | 2 / 0.5 / 0.2 / 2 / 3 | **0.956** | **0.965** | **1.000** | 0.957 / 0.981 | **0.664 / 0.498** | 0.340 |
| s2j0.5b0.35m2g2 (least predictable pass) | 2 / 0.5 / 0.35 / 2 / 2 | 0.962 | 0.951 | 1.000 | 0.925 / 0.956 | 0.614 / 0.479 | 0.340 |
| s2j0.5b0.2m2 (same, no gap) | 2 / 0.5 / 0.2 / 2 / 0 | 0.948 ✗ | 0.952 | 0.988 | 0.951 / 0.966 | 0.632 / 0.492 | 0.340 |
| s0b0.35m4 (big band jumps) | 0 / 0 / 0.35 / 4 / 0 | 0.799 ✗ | 0.874 ✗ | 0.949 | 0.881 / 0.863 | 0.867 / 0.922 | 1.000 |
| s3j0.5b0.35m4 | 3 / 0.5 / 0.35 / 4 / 0 | 0.822 ✗ | 0.884 ✗ | 0.960 | 0.861 / 0.870 | 0.525 / 0.430 | 0.222 |

- **Where the cost is.** W3's plan starts at the bottom of the limit band (Abuela uncommon limits p10–p90 = 21–25), so any step > 1 inside the band can overshoot her limit: 1 P = 0.17 share on uncommons, 0.5 on commons. A lower opening bid costs no overshoot, but it costs bids, so time and the dealer's patience: at the recommended level the step-capped uncommon goes 0.929 → 0.898 (deal 0.978 → 0.942).
- **The band gap** is what keeps in-band jumps affordable. The same level without it fails (0.948). In the simulator, whose Abuela holds her ask far above her limit, the gap protects less (uncommon 0.967 → 0.937 at the recommended level).
- **Time:** about +1.0–1.5 ticks per deal at 2 ticks per round on W3's plans (uncommon 5.2 → 6.4). Fill within 8 ticks drops for that reason. The deal rate moves −0.01 to −0.04 (W3 uncommons, fitted and step-capped). This is small against Abuela's 8 deals per hour.

## What Marius must decide
1. **Turn it on or not:** off (default) or the recommended five knobs in STRATEGY.md. Unpredictability scores nothing by itself (see Verdict).
2. **`dealer_min_step_pct` = 0.02** applies only with a jitter on. The "2 % of book" rule is not in RULES.md, the fixtures, the feed capture or the simulator; it comes from the B12 brief. With it, a step-1 rare plan climbs 89 / 91 / 93, and Chato rare share on the real threads (limit at the top) drops 0.948 → 0.907. Set it to 0 if the rule is not real.
3. **Merge order:** after #81. #72 conflicts in `dealer.py`, `taker.py` and `cli.py` (other stack). To re-apply after #72:
   - `may_close` is now `may_take` (one test);
   - `counter_below` should use `base_step`;
   - #72's `reopen_start` reads `neg.bids[0]`, which a jitter lowers, so the reopened thread starts up to 2 lower: harmless, but note it.

## Risks
- The fitted models rest on Friday's 31 / 58 / 51 Abuela threads and Chato's 12 / 15. The simulator's Abuela is more patient than the real one.
- The rival model does not condition on her asks. With a band gap, a reader who does would predict a little better than 0.66.
- `jump_max` is absolute. For a class whose base step is ≥ `jump_max` (Chato rare under the 2 % rule) there are no jumps, only the start spread.
