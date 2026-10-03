# W3 · Ladder maximiser (night shift, 3 Oct 2026)

Stacked on #61 (`fix/dealer-ladder-counter`). Draft PR #81. Nothing went live: the data is Friday's public feed and the simulator from #55, run locally.

## Verdict
- **Abuela: GO.** The PLAN rule (floor − 2 → floor + 2, step 1) passes every criterion on the two classes it can buy, commons and uncommons: mean share ≥ 0.94, ≥ 99 % filled within 8 ticks, 0 repeated prices.
- **El Chato: NO-GO under today's caps.** No buy can fill: his limits sit above `max_price_uncommon` 26 and `max_price_rare` 80. So no early L3 from Chato buys. Two ways to unblock it are listed below; Marius decides.
- **Packs: NO-GO under `max_price_pack` 20.** Three in four of Abuela's Friday pack limits were 21 or more.

## What the threads show (273 dealer threads, every team, Fri ticks 0–159)
Each thread's secret limit is bracketed by its own structure: every bid the dealer countered is below the limit, and every ask, the bid it took and its final are at or above it. A limit "point" is the price that closed the thread (a fill or a final). Threads where a team took the opening ask untested are left out: they show nothing about the limit.

| dealer | class | opening | threads | closed | floor p10 / p25 / **p50** / p75 / p90 | patience (bids before final) |
|---|---|---|---|---|---|---|
| abuela | common | 12 | 31 | 25 | 8 / 9 / **10** / 10 / 10 | – (no final seen) |
| abuela | uncommon | 29 | 58 | 37 | 21 / 22 / **23** / 24 / 25 | 5 |
| abuela | sobre_barrio | 30 | 51 | 34 | 20 / 21 / **22** / 23 / 24 | 5 |
| chato | uncommon | 33 | 12 | 6 | 28 / 28 / **29** / 31 / 32 | 6 |
| chato | rare | 97 | 15 | 9 | 82 / 90 / **91** / 91 / 93 | 5 |

How the dealers behave: Abuela opens, drops 2–5 on her first counter, then 0–2 per round (1 in 60–70 % of rounds). After a median of 5 bids she names a final, and she takes our bid as soon as it reaches her limit. Chato holds the first move, then moves about as far as we move. A second regime exists: 13 pack threads and 5 uncommon threads opened at 17 (ticks 0–102, cause unknown). Fills are matched to threads by team, dealer, item and a price the thread named. #61's `counter_below` handles it: we bid 16 and take 17 only after she concedes or when no whole price is left between us.

## Backtest: our real `decide()` (#61) against dealers fitted to these threads
Share = (opening − price) / (opening − that conversation's limit). No deal counts as 0. Model: 4,000 conversations per row. Replay: each real conversation re-run with its own first counter, later steps, patience, and its limit at the top (hi) or bottom (lo) of its bracket. "Teams got" is what the real teams captured in those same conversations.

| class | plan | model share | deal | fill ≤ 8 ticks | fill ≤ 8 at 2 ticks/round | repeats | replay hi / lo | teams got | n |
|---|---|---|---|---|---|---|---|---|---|
| abuela uncommon | today 17→26 (lowest fill) | 0.840 | 0.946 | 0.919 | 0.134 | 0 | 0.800 / 0.854 | 0.838 | 37 |
| abuela uncommon | **W3 21→25** | **0.945** | 0.994 | 0.994 | 0.847 | 0 | **0.973** / 0.888 | 0.838 | 37 |
| abuela common | today 7→12 | 0.984 | 1.000 | 1.000 | 0.906 | 0 | 0.960 / 0.959 | 0.960 | 25 |
| abuela common | W3 8→12 | 0.975 | 1.000 | 1.000 | 0.994 | 0 | 0.960 / 0.943 | 0.960 | 25 |
| abuela pack | today 17→20 | 0.421 | 0.432 | 0.432 | 0.325 | 0 | 0.118 / 0.603 | 0.581 | 34 |
| abuela pack | W3 (cap 20) | blocked | | | | | | | |
| abuela pack | W3 uncapped 20→24 | 0.901 | 0.959 | 0.959 | 0.818 | 0 | 0.933 / 0.838 | 0.581 | 34 |
| chato uncommon | W3 uncapped 27→31 | 0.690 | 0.832 | 0.832 | 0.676 | 0 | 0.833 / 0.560 | 1.000 | 6 |
| chato rare | W3 uncapped 89→93 | 0.756 | 0.997 | 0.997 | 0.975 | 0 | 0.948 / 0.618 | 0.889 | 9 |

The gain is in uncommons: +0.105 share in the model and +0.173 on the real replays, with the deal rate up from 0.946 to 0.994. It also survives slow replies: if every round cost two ticks, 85 % of W3's uncommon deals would still settle within 8 ticks, against 13 % for today's 17→26. For commons today's ladder is already at the ceiling: W3 is 0.009 lower in the model and equal on the replays. A grid over start and max shows that floor ± 2 is within 0.01 of the best choice on every Abuela class, by the mean of model and replay share (commons best 6→12 at 0.974 vs 0.965; uncommons 21→26 at 0.961 vs 0.960; packs: floor ± 2 is the best).

## On the simulator's dealers (#55, local merge, never pushed)
`scripts/ladder_sim_check.py`: 2,000 conversations per row, the simulator's own `dealers.start()` / `reply()`, our template words (her kindness discount applies).

| class | plan | sim limit range | share | deal | fill ≤ 8 | repeats |
|---|---|---|---|---|---|---|
| abuela common | 8→12 | 7–9 | 0.952 | 1.00 | 1.00 | 0 |
| abuela uncommon | 21→25 | 21–22 | 0.967 | 1.00 | 1.00 | 0 |
| abuela pack (uncapped) | 20→24 | 17–18 | 0.807 | 1.00 | 1.00 | 0 |
| chato uncommon (uncapped) | 27→31 | 27–30 | 1.000 | 1.00 | 1.00 | 0 |
| chato rare (uncapped) | 89→93 | 86–92 | 0.907 | 1.00 | 1.00 | 0 |

End to end, our real CLI (`bazaar dealer buy … --live`, BAZAAR_SIM=1) ran against an in-process simulator: 7 of 7 deals, all negotiated, 6 at exactly the secret limit and 1 at the limit + 1. The simulator's pack floor (17–18) is lower than Friday's real one (21 at p25). Trust the real numbers for packs.

## The 09:00–10:30 plan (`docs/night/ladder_plan.json`)
Inputs: cash 353 at the open plus the 150 grant at 09:03 (tick 6), `cash_floor` 270, 150 P per game hour, Abuela 8 deals per hour, one conversation per dealer at a time, and each slot reserves its max price. The first three slots are Abuela's best three: commons, the highest-share and cheapest class. The rest is album fill, alternating commons and uncommons. When the dealer takes our bid, no team accept slot is used.

| hour | slots | reserved | expected spend |
|---|---|---|---|
| 09:00–10:00 | 8 (09:00 → 09:21, every 3 min) | 135 P | 112 P |
| 10:00–10:30 | 6 (10:00 → 10:15) | 98 P | 81 P |

The cash floor binds: 503 − 270 = 233 P, all of it reserved. If each slot frees the difference between its max and the price it actually closed at, about 40 P more is available. Blocked, with numbers: Chato uncommon, Chato rare, Chato silver pack, Abuela pack. Card refs are left blank on purpose: at 09:00 run `uv run bazaar ladder plan --cash <cash> --refs <missing cards from bazaar strategy>`.

What-if (`ladder_plan.what_if_chato_31.json`, plan only, GUARDRAILS.md unchanged): with Chato's uncommon cap at 31, Chato and Abuela both start at 09:00. That gives 5 Chato and 5 Abuela slots in the window, with an expected Chato share of 0.69 in the model, 0.83 on the replays and 1.00 in the simulator.

## 09:00 runbook
1. `uv run bazaar feed capture` is running (or the desk can reach Postgres `feed_events`), so floors include every thread.
2. `uv run bazaar ladder floors --source feed`: check that Abuela still opens at 29 and 12, and look at the p50 column.
3. `uv run bazaar ladder plan --cash <cash from /api/me> --source feed --refs <missing cards from uv run bazaar strategy> --out ladder_plan.json`, then run each slot's `command` at its `wall` time (or set `ladder_floor_quantile = 0.5` and let the desk do it).
4. About 09:30, once Saturday threads exist: the same with `--since-tick <Saturday's first tick>`, so the plan is fitted on Saturday only.

## Constraints (reported, not changed)
| cap | value | the market it meets | effect |
|---|---|---|---|
| `max_price_uncommon` | 26 | Chato uncommon limits 28–32 (sim 27–30) | 0 Chato uncommon buys. A deal rate of 0.83 needs a cap of 31. |
| `max_price_rare` | 80 | Chato rare limits 82–93 (p25 90) | 0 Chato rare buys. 0.84 needs 90; 0.997 needs 93. |
| `max_price_pack` | 20 | Abuela pack limits p25 21, p50 22 | Deal rate 0.43 in the model, 0.12 on the replays (today's 17→20). 0.81 needs 22; 0.89 needs 23; 0.96 needs 24. |
| `cash_floor` | 270 | 233 P spendable 09:00–10:30 | Caps the window at about 14 Abuela deals. |

## What Marius must decide
1. **Chato (L2 ladder and early L3).** Either (a) a Chato-only uncommon cap of about 31, as a new guardrail parameter whose default keeps 26 (not built tonight), or (b) selling duplicates to Chato. He bids 13 and goes to 15–16 for an uncommon, and 39 → 46 for a rare. `agents/dealer.py` is buy-only today (`offer_terms_problem` refuses an offer that gives cash), so (b) needs a sell negotiation. It is also unverified that a sale counts for the ladder or for unlocking.
2. **Turn W3 on in the runtime:** set `ladder_floor_quantile = 0.5` in STRATEGY.md. The default 0 keeps today's ladder. The desk then opens Abuela uncommons at 21 instead of 17.
3. **Packs:** keep `max_price_pack` 20 and buy no packs from Abuela, or lift it to 22–24 (deal rate 0.81–0.96).

## Risks
- Fitted on Friday: Saturday's dealers may move, so re-run `bazaar ladder floors` on the morning feed. Chato's rows rest on only 6 and 9 closed threads.
- The organisers' "price range" is unknown. We score opening → that conversation's limit; a range per dealer and class would score every row lower, in the same order.
- One round per tick is assumed. Our own five Friday threads ran exactly one bid per tick, and across teams the median was 1.0 tick per bid (mean 1.25). The worst case of 2 ticks per round is in the table above.
- The plan assumes Abuela's 8 deals per hour reset at the game-hour boundary (10:00 = t 5.0), as the simulator does. If the real server uses a rolling hour, the 10:00 burst slides to about 10:09 (one hour after the first deal).
- The runtime floors (`ladder_floor_quantile` > 0) come from what the desk's `MarketFeed` holds: Postgres `feed_events` (all of Friday) or the captured JSONL, plus the live window. A class with fewer than 5 closed threads gets no floor plan and keeps today's ladder (tested).
