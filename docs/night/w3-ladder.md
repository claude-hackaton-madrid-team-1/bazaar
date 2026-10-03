# W3 · Ladder maximiser (night shift, 3 Oct 2026)

Stacked on #61 (`fix/dealer-ladder-counter`). Draft PR #81. Nothing went live: the data is Friday's public feed and the simulator from #55, run locally.

## Verdict
- **Abuela: GO.** The PLAN rule (floor − 2 → floor + 2, step 1) passes every criterion on every class it can buy: mean share ≥ 0.95, ≥ 99 % filled within 8 ticks, 0 repeated prices.
- **El Chato: NO-GO under today's caps.** No buy can fill: his limits sit above `max_price_uncommon` 26 and `max_price_rare` 80. So no early L3 from Chato buys. Two ways to unblock it are listed below; Marius decides.
- **Packs: NO-GO under `max_price_pack` 20.** Three in four of Abuela's Friday pack limits were 21 or more.

## What the threads show (273 dealer threads, every team, Fri ticks 0–159)
Each thread's secret limit is bracketed by its own structure: every bid the dealer countered is below the limit, and every ask, the bid it took and its final are at or above it. A limit "point" is the price that closed the thread (a fill or a final). Threads where a team took the opening ask untested are left out: they show nothing about the limit.

| dealer | class | opening | threads | closed | floor p10 / p25 / **p50** / p75 / p90 | patience (bids before final) |
|---|---|---|---|---|---|---|
| abuela | common | 12 | 31 | 25 | 8 / 9 / **10** / 10 / 10 | – (no final seen) |
| abuela | uncommon | 29 | 58 | 35 | 21 / 21 / **23** / 24 / 25 | 5 |
| abuela | sobre_barrio | 30 | 51 | 33 | 21 / 21 / **22** / 23 / 24 | 5 |
| chato | uncommon | 33 | 12 | 6 | 28 / 28 / **29** / 31 / 32 | 6 |
| chato | rare | 97 | 15 | 9 | 82 / 90 / **91** / 91 / 93 | 5 |

How the dealers behave: Abuela opens, drops 2–5 on her first counter, then 0–2 per round (1 in 60–70 % of rounds). After a median of 5 bids she names a final, and she takes our bid as soon as it reaches her limit. Chato holds the first move, then moves about as far as we move. A second regime exists: 13 pack threads and 5 uncommon threads opened at 17 (ticks 0–102, cause unknown). #61's `counter_below` handles it: we bid 16 and take 17 only after she concedes or when no whole price is left between us.

## Backtest: our real `decide()` (#61) against dealers fitted to these threads
Share = (opening − price) / (opening − that conversation's limit). No deal counts as 0. Model: 4,000 conversations per row. Replay: each real conversation re-run with its own first counter, later steps, patience, and its limit at the top (hi) or bottom (lo) of its bracket. "Teams got" is what the real teams captured in those same conversations.

| class | plan | model share | deal | fill ≤ 8 ticks | repeats | replay hi / lo | teams got | n |
|---|---|---|---|---|---|---|---|---|
| abuela uncommon | today 17→26 (lowest fill) | 0.852 | 0.950 | 0.927 | 0 | 0.792 / 0.863 | 0.840 | 35 |
| abuela uncommon | **W3 21→25** | **0.950** | 0.995 | 0.995 | 0 | **0.962** / 0.902 | 0.840 | 35 |
| abuela common | today 7→12 | 0.984 | 1.000 | 1.000 | 0 | 0.960 / 0.959 | 0.960 | 25 |
| abuela common | W3 8→12 | 0.967 | 1.000 | 1.000 | 0 | 0.952 / 0.935 | 0.960 | 25 |
| abuela pack | today 17→20 | 0.413 | 0.423 | 0.423 | 0 | 0.091 / 0.591 | 0.577 | 33 |
| abuela pack | W3 (cap 20) | blocked | | | | | | |
| abuela pack | W3 uncapped 20→24 | 0.903 | 0.959 | 0.959 | 0 | 0.934 / 0.836 | 0.577 | 33 |
| chato uncommon | W3 uncapped 27→31 | 0.690 | 0.832 | 0.832 | 0 | 0.833 / 0.560 | 0.800 | 6 |
| chato rare | W3 uncapped 89→93 | 0.756 | 0.997 | 0.997 | 0 | 0.948 / 0.618 | 0.889 | 9 |

The gain is in uncommons: +0.10 share in the model and +0.17 on the real replays, with the deal rate up from 0.95 to 0.995. For commons today's ladder is already at the ceiling: W3 is 0.017 lower and either is fine. A grid over start and max shows that floor ± 2 is within 0.015 of the best choice on every Abuela class, by the mean of model and replay share (commons best 6→11 at 0.974; uncommons 20→26 at 0.959).

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
| 09:00–10:00 | 8 (09:00 → 09:21, every 3 min) | 135 P | 111 P |
| 10:00–10:30 | 6 (10:00 → 10:15) | 98 P | 80 P |

The cash floor binds: 503 − 270 = 233 P, all of it reserved. If each slot frees the difference between its max and the price it actually closed at, about 40 P more is available. Blocked, with numbers: Chato uncommon, Chato rare, Chato silver pack, Abuela pack. Card refs are left blank on purpose: at 09:00 run `uv run bazaar ladder plan --cash <cash> --refs <missing cards from bazaar strategy>`.

What-if (`ladder_plan.what_if_chato_31.json`, plan only, GUARDRAILS.md unchanged): with Chato's uncommon cap at 31, Chato and Abuela both start at 09:00. That gives 5 Chato and 5 Abuela slots in the window, with an expected Chato share of 0.69 in the model, 0.83 on the replays and 1.00 in the simulator.

## Constraints (reported, not changed)
| cap | value | the market it meets | effect |
|---|---|---|---|
| `max_price_uncommon` | 26 | Chato uncommon limits 28–32 (sim 27–30) | 0 Chato uncommon buys. A deal rate of 0.83 needs a cap of 31. |
| `max_price_rare` | 80 | Chato rare limits 82–93 (p25 90) | 0 Chato rare buys. 0.84 needs 90; 0.997 needs 93. |
| `max_price_pack` | 20 | Abuela pack limits p25 21, p50 22 | Deal rate 0.42 in the model, 0.09 on the replays. 0.87 needs 22; 0.96 needs 24. |
| `cash_floor` | 270 | 233 P spendable 09:00–10:30 | Caps the window at about 14 Abuela deals. |

## What Marius must decide
1. **Chato (L2 ladder and early L3).** Either (a) a Chato-only uncommon cap of about 31, as a new guardrail parameter whose default keeps 26 (not built tonight), or (b) selling duplicates to Chato. He bids 13 and goes to 15–16 for an uncommon, and 39 → 46 for a rare. `agents/dealer.py` is buy-only today (`offer_terms_problem` refuses an offer that gives cash), so (b) needs a sell negotiation. It is also unverified that a sale counts for the ladder or for unlocking.
2. **Turn W3 on in the runtime:** set `ladder_floor_quantile = 0.5` in STRATEGY.md. The default 0 keeps today's ladder. The desk then opens Abuela uncommons at 21 instead of 17.
3. **Packs:** keep `max_price_pack` 20 and buy no packs from Abuela, or lift it to 22–24.

## Risks
- Fitted on Friday: Saturday's dealers may move, so re-run `bazaar ladder floors` on the morning feed. Chato's rows rest on only 6 and 9 closed threads.
- The organisers' "price range" is unknown. We score opening → that conversation's limit; a range per dealer and class would score every row lower, in the same order.
- One round per tick is assumed. Abuela's real replies came in the same tick 28 % of the time and the next tick 72 %. The mean is 3.0–3.2 ticks per deal, so the 8-tick go criterion has headroom.
