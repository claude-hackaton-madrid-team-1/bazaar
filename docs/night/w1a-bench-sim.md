# W1a: a realistic Market Test bench in bazaar-sim

Night shift of 3–4 Oct 2026. Branch `night/w1a-bench-sim`, draft PR #77, stacked on #55 (`ogarciarevett/feat-bazaar-sim`). Refs #12.

## What it is

`src/bazaar_sim/bench.py` is the Market Test with no `World` attached. The simulator's venues run it tick by tick, and a broker under test runs it in-process (1,000 hard books with the stall and the oracle scored in 0.14 s):

```python
from bazaar_sim.bench import HARD, NORMAL, make_book, simulate, stall_policy
r = simulate(policy, NORMAL, seed=7, rule="quote")  # policy(book) -> [(sell, buy, price), ...]
r.efficiency, r.stall, r.oracle, r.points(), r.refused, r.max_requests_per_tick
```

| Parameter | normal | hard | Source |
|---|---|---|---|
| traders, ticks | 10, 16 | 12, 16 | schedule fixture (`get_api_schedule.anon.json`) |
| firm share (quote never moves) | 0.20 | 0.35 | issue #12 |
| impatient share (stays 1–2 ticks, the rest 3–6) | 0.25 | 0.35 | issue #12 |
| arrivals | spread over ticks 0–10 | same | **assumption** (`arrive_spread`) |
| relaxing | linear with age, gives up 50–100 % of its shade by its last tick | same | **assumption** |
| limits, shades | cost 20–60, ask +5–30 %; value 40–95, bid −5–25 % | same | #55, unchanged |
| match rule | `quote` (today's, default) or `limit` (hidden limits honoured) | same | **unknown**. The SDK's `Broker.match` docstring, "(ask <= price, price + fee <= bid)", reads as `quote`; the kit README's "a broker that estimates those limits does better" fits `limit` better. The morning probe settles it. |
| stall replica | crosses by quote every tick, fee 0, the kit's tie-break | same | starter_broker.py docstring; fee is an **assumption** |

Efficiency is the realised gain divided by the possible gains at the true limits (the static optimum, regardless of who is in the book when). The **oracle** knows every limit, arrival, departure and future quote: it is an exact max-weight matching over the pairs that can be matched at some tick under the rule. No broker can beat it.

## Evidence (1,000 seeded books per row; `uv run bazaar-sim bench --seeds 1000`)

| preset | rule | stall p10/p50/mean | oracle p10/p50/mean | oracle − stall p50/mean | oracle > stall | oracle points* |
|---|---|---|---|---|---|---|
| normal | quote | 0.54 / 0.83 / 0.79 | 0.63 / 0.91 / 0.86 | 0.027 / 0.071 | 58.7 % | 0.79 |
| normal | limit | 0.54 / 0.83 / 0.79 | 0.66 / 0.93 / 0.88 | 0.046 / 0.085 | 65.6 % | 0.83 |
| hard | quote | 0.56 / 0.82 / 0.79 | 0.67 / 0.91 / 0.87 | 0.042 / 0.076 | 68.7 % | 0.84 |
| hard | limit | 0.56 / 0.82 / 0.79 | 0.69 / 0.93 / 0.88 | 0.062 / 0.091 | 76.4 % | 0.88 |

\* Mean session points against two stall-level rival venues. Our reading of RULES.md: matching the stall gives 0.5, matching the top-three mean gives 1.0, and points are linear in between. The stall itself scores 0.50. Against such a field the column equals 0.5 + 0.5 × the win rate: any edge earns the full point for the session, and a tie earns half.

**Sensitivity (median oracle − stall, 1,000 books per cell; 72 cells were run, these are the corners):**

| arrivals | shade × | relax | normal quote | normal limit | hard quote | hard limit |
|---|---|---|---|---|---|---|
| spread | 1.0 | 0.5–1.0 (default) | 0.027 | 0.046 | 0.042 | 0.062 |
| spread | 2.0 | 0.5–1.0 | 0.020 | 0.123 | 0.045 | 0.160 |
| spread | 2.0 | none (all firm) | 0.000 | 0.262 | 0.021 | 0.270 |
| all at tick 0 | 1.0 | 0.5–1.0 | 0.008 | 0.009 | 0.023 | 0.023 |
| all at tick 0 | 2.0 | none | 0.157 | 0.272 | 0.169 | 0.274 |

**Session points by rival field (mean of 1,000 books; "half" = stall + 50 % of the oracle's edge, a hypothetical efficiency rather than a real policy):**

| preset, rule | ours = half: field 2 stall / 1 oracle + 1 stall / 2 oracle | ours = oracle: same three fields |
|---|---|---|
| normal, quote | 0.79 / 0.79 / 0.68 | 0.79 / 0.79 / 0.79 |
| hard, limit | 0.88 / 0.88 / 0.73 | 0.88 / 0.88 / 0.88 |

## What it means

1. **W1b's edge policy on this bench** (its message at 02:40, PR #84, 1,000 books): under `quote` it wins 7–9 % of sessions, with points 0.53–0.54 and the same mean as the stall. Under `limit` its probe scores 0.61–0.92 points depending on the cell. With every trader at tick 0, 2× shades and all firm, it reaches +0.13 p50.
2. **The W1b bar "p50 ≥ stall + 0.15" cannot be reached in the default cell.** Even the oracle reaches only +0.03 to +0.06 there. It reaches +0.15 only under the `limit` rule with shades about 2× wider or traders mostly firm. W1b found the same thing independently with its own bench model (STATUS 02:17: stall 0.80, ceiling 0.90).
3. **The win rate over the stall matters more than the margin.** Points are relative to the top-three mean, so beating a stall-level field by any amount earns the full point for that session, and ties earn 0.5. The oracle ties the stall on 23–41 % of books. That is the ceiling on the share of sessions we can win.
4. **The default cell is the most pessimistic one for a smart broker.** The kit says crossing by quote earns "half the bench points and no more" and that estimating limits beats the stall. In the default cell the hard test is barely harder than the normal one (stall 0.821 vs 0.827), because 5–30 % shades let firm traders cross anyway. The cells consistent with the designers' text are the `limit` rule and/or shades ≥ 1.5×. If the probe confirms `quote`, as the SDK docstring suggests, the oracle's median edge stays ≤ 0.05 whenever arrivals are spread out. The edge then comes almost entirely from winning ties, never from margin.

## Go / no-go

- Simulator deliverable: **go.** The bench is in, both presets are in, the stall replica is scored in the same run, and the oracle bounds every policy. Refusals are counted by reason, and reads and posts are counted per tick. Gates: 817 passed (25 new, including the kit's `starter_broker.py` driving a simulator venue over real HTTP and scoring exactly the stall replica), ruff, black and mypy clean. `/code-review` (high) raised 10 candidates. 7 are fixed. Two duplications are kept and pinned equal by tests (`stall_policy` vs `cross_by_quote`; `BenchSession.match` vs `broker._bench_match`). `mm_points` is kept as #55 had it (see Risks).
- The W1b criteria as written: **no-go by construction** (see 2). Proposed instead, per preset and per rule over 1,000 books:
  - never below the stall on any book (issue #12's own "never worse than greedy");
  - 0 refused matches under `quote`, and refusals reported under `limit`;
  - win rate over the stall ≥ 50 % of the oracle's win rate;
  - mean session points ≥ 0.70 against the stall-level field (the stall scores 0.50, the oracle 0.79–0.88);
  - the efficiency margin reported, but not gated.

## Risks

- Arrivals, relaxing, the shades and the stall's fee are not in any official text, and the match rule is ambiguous (see the table). Every one of them is a parameter, and the grid shows the headroom moves by 10× across them. The shape of the relax curve does not matter: relaxing late (age³) instead of linearly moves the median edge by < 0.003.
- The points curve between 0, the stall and the top three is our reading of one sentence in RULES.md.
- The two old bench tests in `test_sim_world.py` pass only because `bench_ticks=3` collapses the arrival spread to 0. The staggered regime is covered in `tests/test_sim_bench.py`.
- Simulator standings still add `10 × efficiency` to `mm_points` (unchanged #55 behaviour). The stall-relative curve lives in `bench.points` and in the `bench.finished` fields; it is not wired into the simulator's leaderboard.
- `bench.finished` gains `preset`, `stall_efficiency` and `top3_efficiency`. They are simulator-only, so code must not expect them from the real feed.
- Bench offers now carry both sides in full. Before this change the kit's `starter_broker.py` crashed on a simulator book (`KeyError` on `want.cash`).

## What Marius must decide

1. **The W1b go/no-go:** adopt the points-based bar above or keep "stall + 0.15".
2. **The simulator default after the morning probe.** If a match that crosses only at the limits is accepted, set `SIM_BENCH_MATCH_RULE=limit` on the shared simulator. Nothing changes tonight.
3. **Calibration, no venue needed.** Our team has a free stall, so after each real Market Test `GET /api/me` → `score.bench_efficiency` is the real stall's efficiency on that book. Compare it with the simulator's stall per cell (`bazaar-sim bench --shade X --relax lo,hi`):

   | normal, spread | relax 0.5–1.0 | all firm |
   |---|---|---|
   | shade 1.0 | 0.79 | 0.77 |
   | shade 1.5 | 0.77 | 0.70 |
   | shade 2.0 | 0.73 | 0.59 |

   The values are mean stall efficiency. One session's SD is 0.17–0.24, so 4–8 sessions can separate the corners (0.79 vs 0.59) but never neighbouring cells. Also log the public `bench.finished` payload. Once we have a board venue, log `bench_offers` on every read: arrivals, lifetimes and the relax curve can all be read off it.
