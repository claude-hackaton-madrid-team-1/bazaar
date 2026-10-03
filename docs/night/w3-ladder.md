# W3 · Ladder maximiser (night shift, 3 Oct 2026)

Draft PR #81, stacked on #61. Nothing went live: Friday's public feed (273 dealer threads, every team, ticks 0–159) and the #55 simulator, run locally.

## Verdict
- **Abuela: GO.** PLAN's rule (floor − 2 → floor + 2, step 1) passes on commons and uncommons: share ≥ 0.94, ≥ 99 % filled within 8 ticks, 0 repeated prices.
- **Chato: NO-GO under today's caps.** His limits sit above `max_price_uncommon` 26 and `max_price_rare` 80, so no buy can fill and there is no early L3 from buys. A one-line unblock is ready: `dealer_price_caps` (decision 1).
- **Packs: NO-GO under `max_price_pack` 20.** Three in four of Abuela's pack limits were 21 or more.

## Floors (each thread's secret limit, bracketed by what the dealer countered and what it took)
| dealer | class | opening | threads | closed | limit p10 / p25 / **p50** / p75 / p90 | bids before her final |
|---|---|---|---|---|---|---|
| abuela | common | 12 | 31 | 25 | 8 / 9 / **10** / 10 / 10 | – |
| abuela | uncommon | 29 | 58 | 37 | 21 / 22 / **23** / 24 / 25 | 5 |
| abuela | sobre_barrio | 30 | 51 | 34 | 20 / 21 / **22** / 23 / 24 | 5 |
| chato | uncommon | 33 | 12 | 6 | 28 / 28 / **29** / 31 / 32 | 6 |
| chato | rare | 97 | 15 | 9 | 82 / 90 / **91** / 91 / 93 | 5 |

How Abuela haggles: she opens, drops 2–5 on her first counter, then 0–2 per round. She names a final after a median of 5 bids, and she takes our bid the moment it reaches her limit. Chato holds his first move, then moves about as far as we do. A minority of threads opened at 17 (13 pack threads, 5 uncommon); #61's `counter_below` handles that case.

## Backtest: the real `decide()` (#61) against dealers fitted to these threads
Share = (opening − price) / (opening − that conversation's limit); no deal = 0. "Replay" re-runs each real thread with its own counters and patience, the limit at the top / bottom of its bracket.

| class | plan | model share | deal | fill ≤ 8 ticks | same, 2 ticks/round | replay hi / lo | real teams got |
|---|---|---|---|---|---|---|---|
| abuela uncommon | today 17→26 | 0.840 | 0.946 | 0.919 | 0.134 | 0.800 / 0.854 | 0.838 |
| abuela uncommon | **W3 21→25** | **0.945** | 0.994 | 0.994 | 0.847 | **0.973** / 0.888 | 0.838 |
| abuela common | today 7→12 | 0.984 | 1.000 | 1.000 | 0.906 | 0.960 / 0.959 | 0.960 |
| abuela common | W3 8→12 | 0.975 | 1.000 | 1.000 | 0.994 | 0.960 / 0.943 | 0.960 |
| abuela pack | today 17→20 | 0.421 | 0.432 | 0.432 | 0.325 | 0.118 / 0.603 | 0.647 |
| abuela pack | W3 uncapped 20→24 | 0.901 | 0.959 | 0.959 | 0.818 | 0.933 / 0.838 | 0.647 |
| chato uncommon | W3 uncapped 27→31 | 0.690 | 0.832 | 0.832 | 0.676 | 0.833 / 0.560 | 1.000 (n 6) |
| chato rare | W3 uncapped 89→93 | 0.756 | 0.997 | 0.997 | 0.975 | 0.948 / 0.618 | 0.889 (n 9) |

- The gain is in uncommons: share +0.105 in the model and +0.173 on the replays, and the 21→25 ladder still settles within 8 ticks when replies are slow (85 % against 13 %).
- Commons are already at the ceiling.
- Repeated prices: 0 in every row.
- Grid over start and max: floor ± 2 is within 0.01 of the best on every Abuela class.
- Held out in time: fitted only on threads before tick 80, 100 or 120, the plan is still 21→25. It closes every later thread at its limit (1.00), against 0.77–0.85 for today's 17→26.

## Simulator (#55, local merge, never pushed)
| class | plan | sim limits | share | deal | fill ≤ 8 | repeats |
|---|---|---|---|---|---|---|
| abuela common / uncommon | 8→12 / 21→25 | 7–9 / 21–22 | 0.952 / 0.967 | 1.00 | 1.00 | 0 |
| chato uncommon / rare (uncapped) | 27→31 / 89→93 | 27–30 / 86–92 | 1.000 / 0.907 | 1.00 | 1.00 | 0 |

- **`bazaar dealer buy --live` against an in-process simulator:** 7 of 7 deals, 6 at exactly the secret limit.
- **The desk (`agent taker --live`), with `ladder_floor_quantile` 0.5:** it opens uncommons at 21 through `Move.ladder` (7 deals, share 0.95).
- **The desk at 0:** it opens at 17 (8 deals, share 1.00).
- **Caveat:** the simulator's Abuela waits 10 rounds before her final, so starting low is free there. Friday's real Abuela waited 5, which is what costs 17→26 its 0.10–0.17. The simulator's pack floor (17–18) is also below the real one (21).

## The 09:00–10:30 plan (`docs/night/ladder_plan*.json`)
Inputs: cash 353 at the open plus the 150 grant at 09:03, `cash_floor` 270, and `max_spend_per_game_hour` 150 over a rolling game hour (as `guardrails.check` counts it). Abuela gives 8 deals per clock hour. One conversation per dealer at a time, and each slot reserves its max price.

| file | slots | reserved / expected spend |
|---|---|---|
| `ladder_plan.json` (classes only) | 3 commons first (best three), then commons and uncommons alternating: 8 slots 09:00–09:21, 6 slots 10:00–10:15 | 233 / 193 P (the cash floor binds) |
| `ladder_plan.page_cards.json` (W7's page cards, #87) | SAL-02 and SAL-05 at 09:00 and 09:03; LAV-08, SAL-07, SAL-08, MAL-06, MAL-07 from 09:06 to 09:18; MAL-08 at 10:03 | 174 / 151 P |
| `ladder_plan.what_if_chato_31.json` | Chato and Abuela both start at 09:00: 5 + 5 slots | 228 / 199 P |

When Abuela takes our bid, the team's accept slot is not used. Blocked, each with its numbers: the Chato uncommon, Chato rare and Chato silver pack, and the Abuela pack.

## What Marius must decide
1. **Chato (L2 ladder and early L3).** Set `dealer_price_caps = chato:uncommon=31` in GUARDRAILS.md. This rule is new, defaults to `none`, and applies to Chato only (Abuela and team buys keep 26).
   - At 31 the model gives deal rate 0.83 and share 0.70 (replays 0.83).
   - At 32, starting at 23 (`dealer buy <ref> --start 23 --max 32 --dealer chato`), it gives deal rate 1.00 and share 0.97. Thin data: 6 threads.
   - Rares need `chato:rare=93`.
   - The alternative is selling duplicates to Chato: he bids 13 and goes to 15–16 for an uncommon. That needs a sell negotiation (`dealer.py` is buy-only), and it is unverified that sales count.
2. **Runtime:** set `ladder_floor_quantile = 0.5` in STRATEGY.md (default 0 = today's ladder), so the desk opens Abuela uncommons at 21 instead of 17. Real threads say yes; the simulator, whose Abuela is more patient, shows no gain.
3. **Packs:** keep the cap at 20 and buy no Abuela packs, or lift it to 22–24 (deal rate 0.81–0.96).

## 09:00 runbook
1. `uv run bazaar feed capture` running (or Postgres `feed_events` reachable), so the floors see every thread.
2. `uv run bazaar ladder floors --source feed`: check that Abuela still opens at 29 and 12.
3. `uv run bazaar ladder plan --cash <cash> --source feed --refs <missing cards from bazaar strategy>`, then run each slot's `command` at its `wall` time, or let the desk do it with decision 2.
4. About 09:30: re-run with `--since-tick <Saturday's first tick>` to fit on Saturday only.

## Risks
- Fitted on Friday, and Chato on only 6 and 9 closed threads: re-run the floors on Saturday's feed.
- The organisers' "price range" is unknown. We score opening → that conversation's limit.
- One round per tick is assumed. Our Friday threads ran exactly that (median 1.0 tick per bid across teams), and the 2-ticks-per-round worst case is in the backtest table.
- Abuela's deal allotment is assumed to reset per clock hour, as in the simulator.
- With `ladder_floor_quantile` > 0, a class with fewer than 5 closed threads, or a floor plan below the market price, keeps today's ladder (tested).
