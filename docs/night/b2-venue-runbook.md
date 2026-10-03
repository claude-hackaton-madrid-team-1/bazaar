# B2: venue go-live runbook and Saturday's Market Tests simulated

Night backlog item B2, 4 Oct 2026. Branch `night/b2-venue-runbook`, draft PR #92, stacked on #84 (W1b), which is stacked on #71.

**Rewritten at 03:50 for the new #71** (e82ba8d at 03:14–03:23, then e489449 at ~03:50: the venue opens once across restarts, a venue closed by hand is never reopened). It now opens our board venue from the **maker's venue keeper** at game hour 6.5 (11:30 if the clock jumps, 12:51 if it resumes) with an exact broker, keeps the broker key in Postgres, and sets `allow_venue_open = true`, `cash_floor = 100`, `venue_bond_reserve = 270` and `venue_open_after_game_hours = 6.5`. Nothing here touched the live game. Every guardrail change below is a **proposal against #71's values**.

> **Reconciled proposal: #118 (W1a, B20)** merges this runbook with #71. Two differences, both endorsed here:
> 1. Open right after the grant (`venue_open_after_game_hours` 4.05: 09:03 jump / 10:24 resume), as insurance in case the free stall does not count.
> 2. Fall back to `venue_mechanism = auto` if the edge is not live on `bazaar-maker` by then.
>
> **Caveat on the fallback:** a venue's mechanism is set only at opening (RULES.md and the SDK offer only a fee change). An auto venue therefore gives up the edge and the limit probe for the whole of Saturday. Decide before the grant, from the maker's startup line `venue keeper: broker bench edge`.

## What the venue is worth on Saturday (500 simulated Saturdays per row, W1a's bench #77)

**The clock decides the wall times (#102, B6).** Friday's clock froze at game hour 2.65. Gate G0 at 08:55: `uv run bazaar clock` shows `t_hours`.
- **4.0 (jump):** h4 = 09:00, and Saturday's round has **8** Market Tests.
- **2.65 (resume):** every event lands 1 h 21 min later. h3 (09:21) still counts for Friday, and h17 (23:21) falls after the 23:00 close, so Saturday's round has **7**.

| Event | game hour | jump | resume |
|---|---|---|---|
| grant 150 P | 4.05 | 09:03 | 10:24 |
| Market Tests | 5, 7, 9, 11, 13, 15, **16 (hard)**, 17 | 10:00, 12:00, …, 20:00, **21:00**, 22:00 | 11:21, 13:21, …, 21:21, **22:21**, (23:21: Sunday) |
| the keeper opens (#71) | 6.5 | 11:30 | 12:51 |
| the opening right after the grant (proposal) | 4.05 | 09:03 | 10:24 |

The plans below are named by game hour: `h6.5` is #71's keeper, `h4.05` the proposal.

The free stall alone earns 0.5 of the bench points every session. With W5's split that is 15 round points × 0.5 × 0.40 = **3.00 final points**.

Cells are the change in final points against that. The rivals are two stall-level venues; in brackets, three venues at the clairvoyant oracle (`top3`), the pessimistic bound. `uv run python -m bazaar_agent.evals.saturday --days 500`.

| Bench world (jump clock, 8 sessions) | keeper as #71 ships it (exact), h6.5 | keeper + `BAZAAR_BENCH_POLICY=edge`, h6.5 | edge + `BAZAAR_BENCH_CROSS=limit`, h6.5 | edge, opened at h4.05 |
|---|---|---|---|---|
| default, quote rule (best guess) | **+0.00** | **+0.17** (+0.12); 23 % of days worse | +0.17 | +0.19 |
| default, limit rule | +0.00 | +0.17 (+0.10) | **+0.60** (+0.40) | +0.19 |
| 2× shade, limit | +0.00 | +0.17 (+0.09) | **+1.34** (+1.00) | +0.19 |
| whole book at tick 0, quote | +0.00 | +1.03 (+0.94) | +1.03 | +1.17 |
| tick 0, 2× shade, firm, quote | +0.00 | +1.47 (+1.26) | +1.47 | +1.69 |
| tick 0, 2× shade, firm, limit | +0.00 | +1.47 (+0.98) | **+2.35** (+2.06) | +1.69 |

**Resumed clock (7 sessions; keeper at h6.5 = 12:51 runs 6 of them).** The table hardly moves, because points average over the sessions. Keeper as shipped: +0.00. With edge: +0.16 (default), +1.02 (tick 0), +1.46 (tick 0, wide, firm, limit). With edge + limit probe: +0.56 (default, limit), +1.32 (2× shade, limit), +2.31. Opening at h4.05 (10:24) adds +0.02 to +0.38. With 5 % downtime: exact −0.14, edge +0.05 (default) / +0.85 (tick 0). `--clock resume`.

**With the broker down in 5 % of sessions:** exact is **−0.12 to −0.15** and edge **+0.04 to +0.05** in the default worlds; in the tick-0 worlds edge is +0.86 to +1.30. A board venue whose broker is not matching realises nothing in that session: it scores 0 there instead of the stall's 0.5, a loss of 0.375 final points per lost session.

1. **The keeper as #71 ships it earns exactly the stall's bench points.** #71's tie-break makes exact identical to the stall on every modelled bench. On the bench, the venue then only adds risk (maker downtime). Its other value is the organic half of market-making, which is B1's item and not in these numbers.
2. **`BAZAAR_BENCH_POLICY=edge` on `bazaar-maker` is the switch that makes the bench pay:**
   - +0.12 to +0.19 in the default world (break-even downtime ≈ 6 % of sessions);
   - +0.9 to +1.7 when the book is thick or wide.
3. **`BAZAAR_BENCH_CROSS=limit` adds +0.4 to +1.2 if the server checks hidden limits.** If it checks quotes, it costs at most ~13 refused requests per maker restart, then switches itself off. Pairs that would cross with the fee rounded down are never probed.
4. **h4.05 instead of h6.5** adds the h5 session: +0.02 (default) to +0.34 (best cells), with edge only. With exact it adds nothing.
5. **The rival field.** With only two rivals, any session above the stall earns the full point. The `top3` column (three oracle-level venues) is the pessimistic bound: it takes 0.05–0.5 off, and changes no sign.

## Recommendation

- **Keep #71's h6.5 keeper**, and set `BAZAAR_BENCH_POLICY=edge` on `bazaar-maker` before it opens.
- Set `BAZAAR_BENCH_CROSS=limit` too, if refused probe requests on the broker key are acceptable. This is the morning probe, done by the keeper itself: no laptop and no key copy.
- **One broker host: the keeper.** Never run `bazaar broker run --live` on a laptop while the keeper brokers the same venue: two brokers would share one key and one book.

## Cash under #71 (restated)

- **At the grant (09:03 jump / 10:24 resume):** Friday's 353 P plus the 150 P gives **503 P**. Before the grant 353 < 370, so #71's reserve blocks every purchase. Under resume that means no buys at all from 09:00 to 10:24.
- **Until the venue opens:** every purchase keeps `cash_floor` + `venue_bond_reserve` = **370 P**, so the morning can spend **133 P**.
- **At h6.5:** the keeper opens if cash minus what our open bids already promise is ≥ 370 (`venue_keeper._maybe_open`: the bond is judged like a purchase). A guardrail refusal is checked again **every tick**; only a refusal by the server waits 10 ticks.
- **After the opening:** the floor is 100. If the morning spent all 133 P, cash is 100 and nothing more can be bought.

**Saturday's spendable cash is 133 P in total**, against W3 + W4's ~136 P plan (W7). That is 3 P short. Trades that sell bring cash back.

## Guardrail proposals against #71 (for Marius; none applied)

| Want | Change in `GUARDRAILS.md` | Effect |
|---|---|---|
| W3 + W4's whole plan plus a cushion | `cash_floor` 100 → **50** | spendable 133 → **183 P**; the venue still opens at h6.5 (370 − 270 = 100 ≥ 50) |
| The venue right after the grant (+0.02 to +0.34, edge only) | `venue_open_after_game_hours` 6.5 → **4.05** | opens on the first tick after the grant (09:03 jump / 10:24 resume): 503 − 270 = 233 ≥ 100. At 4.0 (before the grant) 353 − 270 = 83 < 100 is refused by the guardrail, and checked again every tick until the grant lands |
| No venue at all | `allow_venue_open` true → false | the free stall all day (3.00) and 503 − 100 = 403 P spendable; no organic market-making |

## Runbook (h6.5 keeper; wall times as jump / resume, settled by G0 at 08:55)

| When | Who / where | What | Check |
|---|---|---|---|
| 08:55 (G0) | anyone | `uv run bazaar clock` | `t_hours` 4.0 = jump, 2.65 = resume: pick the column below |
| Before h6.5 | Marius | merge #71 (then #84 and #92 if the edge is wanted); approve any proposal above | `uv run bazaar rules` shows `allow_venue_open true`, `venue_open_after_game_hours 6.5` |
| Before h6.5 | Railway, `bazaar-maker` variables | `BAZAAR_BENCH_POLICY=edge`; optionally `BAZAAR_BENCH_CROSS=limit`; redeploy | maker log at start: `venue keeper: broker bench edge (normal, accepts by quote\|limit)` |
| grant → h6.5 (09:03–11:30 / 10:24–12:51) | taker, maker, ladder | spend at most 133 P, as #71's reserve enforces | the venue opening needs cash minus open bids ≥ 370 at h6.5 |
| h6.5 (11:30 / 12:51) | the keeper | opens "Team 1 market", board, 0 bps; the key goes to Postgres | maker log: `venue: broker on for vNN (LIVE)` |
| h7 (12:00 / 13:21), the first session with our venue | watch the maker log | one line per tick with proposals, and `Market Test bNN over: pairs, quoted surplus` | pairs > 0 |
| after h7 | the maker's data dir, or its log | `broker_bench_shapes.jsonl` / `broker: bench bNN offers carry {...}` | an expiry field means the edge can hold pairs (set `expiry_margin` only after reading what it means) |
| after each session | `/me` → `score.bench_efficiency` (the monitor's `agent.me` snapshot) | our efficiency; compare with the stall row of W1a's calibration table | — |
| h16 (21:00 / 22:21) | nobody | the hard test, same broker | — |

- **Manual probe (optional; only on a machine that holds the broker key, and only while the keeper runs `BAZAAR_BENCH_CROSS` unset or `quote`; with `limit` the keeper probes by itself and could probe the same pair):** `uv run bazaar broker probe --auto`, then `--live`. It sends only with `--live`; `BAZAAR_LIVE` alone does not. Read the venue's answer:
  - accepted: limits are checked;
  - refused because an offer is gone or not open: no evidence, probe again;
  - refused on price: probe again with `--auto --avoid <sell>,<buy>`, and stop when the product of (1 − P) over the price refusals falls under 0.05.

  The probe skips offers the keeper's broker could take, so the keeper and a probe never compete for an offer.
- **Never** `bazaar broker run --live` on a laptop next to the keeper. For a dry look at what the broker would do, use `env -u BAZAAR_LIVE uv run bazaar broker run --max-ticks 1` (BAZAAR_LIVE=1 makes `broker run` live).
- **Back to exact:** unset `BAZAAR_BENCH_POLICY` and redeploy.
- **Stop the venue:** `allow_venue_open = false` stops the opening and every match, which scores 0 in a running session. `uv run bazaar venue close <id> --live` closes it; the bond comes back after the cooldown. Whether the free stall then comes back is not stated.
- **Rate budget:** the keeper paces matches at 5 per second inside the maker's tick. `bazaar broker run --read-offset` (W5's stagger) is only for a laptop broker, which this runbook does not use.

## What changed in the code (PR #92 on the rebased #84)

- **Keeper bench options from the environment.** `BAZAAR_BENCH_POLICY`, `BAZAAR_BENCH_PRESET` and `BAZAAR_BENCH_CROSS` set the keeper's broker (#84's `bench_config_from_env`; unset means exact). The keeper logs which one it runs. Reads stay at one a tick inside the maker.
- **`bazaar broker probe --auto [--avoid ids]`:** the pair and price most likely to be accepted if limits are checked. It skips offers a broker could take; SELL BUY PRICE and `--auto` are exclusive; it sends only with `--live`.
- **`bazaar broker run --read-offset`:** a laptop broker's first read waits after the tick (W5's stagger).
- **`evals/saturday.py`:**
  - plans h4.05, h6.5 (#71's keeper) and never, for a jumped clock (8 sessions) or a resumed one (7; `--clock resume`);
  - policies exact, edge and edge + probe;
  - rival fields stall, strong and top3;
  - a broker-down risk per session;
  - each plan has its own broker process per day.
- Gates: 2,081 passed, 35 skipped; ruff, black and mypy clean.

## Risks

- **Maker downtime is now the venue's downtime**, and with exact it is a pure loss against the free stall.
- **Every bench number is a model.** Arrivals, shading, relax and the match rule are unobserved. W5's 15/15 bench/venue split is assumed.
- **Probe statistics live in memory:** each maker restart re-probes (≤ ~13 refused requests on a quote-only server).
- **Cash is 3 P short** of W3 + W4's plan under #71's floor.

## What Marius must decide

1. `BAZAAR_BENCH_POLICY=edge` on `bazaar-maker` (merge #84 first), and whether to add `BAZAAR_BENCH_CROSS=limit`.
2. `cash_floor` 100 → 50, for 183 P of spendable cash.
3. Keep h6.5 (11:30 / 12:51), or move the opening to right after the grant (`venue_open_after_game_hours` 4.05: 09:03 / 10:24).
4. **Setting `BAZAAR_BENCH_CROSS=limit` on a live maker IS the decision to send live probes**: no other switch gates them.
