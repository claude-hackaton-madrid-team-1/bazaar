# Other teams' dealing on Saturday, and what each kind of deal scored (part 4 of the sat-dealing review)

Written by a forked sub-session of sat-dealing (Sun 4 Oct, ~00:20 Madrid); saved and spot-checked by the main session
(the round-trip totals in §4.1 were re-run: Pícaros → Pilar 20 trips, +497 P, matches).

**Review notes (sat-dealing revision):** "Saturday = tick ≥ 159" here also pulls in 2 Friday settlements of other teams
and a few Friday-night thread opens (tick 159 spans Fri 22:59 → Sat 09:28); negligible against 471 dealer settlements.
t10's #1 is not explained by flips alone: its 11.93 lead over t01 is 5.00 market and 6.93 negotiating, and a flip's cash
does not score (only ladder slots and team resales do). Addressed offers, a channel this report does not cover, are in
`../../dealing.md` §3.3 and §4.

This is a read-only analysis. Sources are the shared Postgres tables `feed_events` (public feed, ticks 0–1445), `dealer_curves` and `leaderboard_snapshots` (world `real`, every 10 ticks, **ticks 610–1430 only**). "Saturday" means tick ≥ 159; round 2 started at tick 160. No API calls were made. Every number cites a query file in this folder, run with `uv run python ../q.py < NN.sql`; the `.py` files read that output as TSV on stdin.

## TL;DR
1. **Team-to-team rare trades on the board scored the most per deal, for both sides.** Measured in 10-tick windows with no other deal, minus the field median:
   - Buyer side: mean **+1.64** `negotiating` (n=6). Seller side: mean **+1.26** (n=6).
   - LAT-09 at 88 (t16→t03, tick 724): t03 +2.94 and t16 +2.89.
   - SAL-09 at 68 (t15→t06, tick 789): t06 +3.01.
   - Commons: about +0.1.
   - Sources: 13, 14, 09.
2. **The winning loop was Pícaros → Pilar, and Pícaros → rastro.**
   - 20 round trips bought from Pícaros and resold to Pilar netted **+497 P** (about +25 P each).
   - 8 trips resold on rastro netted +277 P.
   - t10 (rank 1, +7.94 `negotiating` from tick 610; its lead over us is 5.00 market + 6.93 negotiating) did 6 such flips, t06 5, t08 5, t12 3 and t14 3. The cash itself does not score.
   - A rare bought from Pícaros scored **+0.44** on average (n=33, 73 % positive). Sources: 12, 13.
3. **Selling page or epic cards to a dealer below value was the biggest loss of the day.**
   - Banco bought 3 epics at 116–120: t16 **−5.56** (tick 1110), t06 **−2.40** (tick 1226).
   - Our SAL-07 sale to Pilar shows the same pattern on the public board: −4.27 at tick 948.
   - Pilar epic sales at 179–199 scored about 0 (−1.15 to +0.86). Sources: 10, 13.
4. **Volume does not score.**
   - t13 made the most dealer deals (46) and lost −1.71 `negotiating` between ticks 610 and 1430.
   - t04 made 45 (+1.65) and t02 36 (−0.05); t03 made only 15 and gained +6.73.
   - Abuela common sales scored **0.00** (n=23). Sources: 01, 08, 13.
5. **t01's position.**
   - Deal rate: 21 % of 75 dealer threads (field 37 %).
   - Price on deals: 18 % off the dealer's opening (field mean about 15 %).
   - Pilar sales: **+18.7 % over her opening, the best of any team with more than 4 Pilar deals.**
   - Our price step is **1 P** (median), the smallest in the field; most teams step 2–3.
   - After Payday (+400 P to every team, tick 1201), t10, t04 and t12 each bought 475–554 P of cards; t01 bought 61 P. Sources: 07, 17, 18.

## 1. Deals per team, Saturday (01, 02, 02b)

Settlements with the team as a party. "Buy" and "sell" mean the card moves to or from the team.

| team | Abuela buy/sell | Chato buy/sell | Pilar sell | Pícaros buy/sell | Banco sell | dealer total | rastro | team venues | swaps |
|---|---|---|---|---|---|---|---|---|---|
| t13 | 11/12 | 3/2 | 13 | 4/1 | – | 46 | 13 | 4 | 1 |
| t08 | 3/13 | 3/1 | 13 | 9/2 | 1 | 45 | 5 | 9 | 1 |
| t04 | 9/12 | 3/1 | 11 | 7/2 | – | 45 | 14 | 7 | – |
| t02 | 9/10 | 5/2 | 6 | 1/3 | – | 36 | 16 | 1 | – |
| t07 | 3/16 | 4/2 | 4 | 1/3 | – | 33 | 11 | 4 | 8 |
| t10 | 6/4 | 0/2 | 11 | 10/0 | – | 33 | 8 | 2 | – |
| t16 | 8/7 | 2/1 | 7 | 3/2 | 1 | 31 | 10 | 2 | 1 |
| t12 | 9/0 | 2/2 | 9 | 4/3 | – | 29 | 8 | 12 | – |
| t14 | 9/0 | 2/1 | 9 | 7/0 | – | 28 | 7 | 8 | 2 |
| t06 | 6/0 | 2/3 | 6 | 7/2 | 1 | 27 | 19 | 7 | – |
| t15 | 5/11 | 3/0 | 3 | 2/0 | – | 24 | 10 | 8 | 6 |
| t05 | 6/0 | 3/2 | 6 | 3/1 | – | 21 | 2 | 4 | 2 |
| **t01** | 3/1 | 1/1 | 5 | 4/1 | – | **16** | 11 | 2 | – |
| t18 | 6/1 | 2/0 | 4 | 3/0 | – | 16 | 7 | – | – |
| t03 | 4/0 | 0/2 | 4 | 3/2 | – | 15 | 4 | 3 | 1 |
| t09 | 2/5 | 3/1 | 3 | 0/0 | – | 14 | 13 | 6 | – |
| t17 | 3/0 | 1/3 | 2 | 3/0 | – | 12 | 4 | 1 | – |

- t11 made no deal all day.
- Abuela buys include 11 sobre_barrio packs; Chato buys include 1 sobre_plata pack.
- **Nobody bought from Pilar** (24 sobre_oro threads, 0 deals) **or from Banco's vault** (20 threads, 0 deals). Banco only bought 3 epics.

Prices (02b):

| What | Avg | Range |
|---|---|---|
| Pícaros sells rares | 56.6 | 48–67 |
| Pícaros sells epics | 144.7 | 128–167 |
| Pilar buys rares | 72.4 | 50–87 |
| Pilar buys epics | 178.3 | 140–199 |
| Chato sells rares | 87.2 | 75–96 |

The gap between Pícaros' sell prices and Pilar's buy prices is the arbitrage in §4.

Level unlocks (02):
- **L2**: earned on Friday with 3–8 Abuela deals.
- **L3 (Pilar)**: opened to everyone at tick 502. Only t13 earned it earlier, at tick 262, with 3 Chato deals.
- **L4 (Pícaros)**: 10 teams earned it at tick 761 with 2–3 Pilar deals (t01 included), t14 at 781 and t15 at 802. The other 6 waited for "open to everyone" at 881, an hour later.
- **L5 (Banco)**: 8 teams unlocked it at tick 971 (t08 had 8 Pilar deals but unlocked no earlier than the others). It opened to everyone at 1091. L5 produced nothing measurable: no team bought from the vault.

The leaderboard `deals` column counts **all settlements since Friday**, dealer and team alike (t01 has 33 at tick 1430). Checked for t01, t07, t10, t12 and t13 at ticks 610 and 1430.

## 2. Dealer negotiation effectiveness (06 → 07)

`dealer_curves.fill_price` is unreliable: t07's threads 1399 (bought LAT-06 from Chato) and 1413 (sold it back) carry swapped fills (14 and 26), against the settlements at tick 968 (26) and tick 975 (14). So every Saturday dealer thread was rebuilt from the feed:
- The dealer's opening and last price come from the dealer's own offers.
- The deal is the matching settlement: same team, persona, item and direction, between the thread's open and its last message + 3 ticks.
- 469 of the 471 Saturday dealer settlements match a thread.
- "Gain" is the discount off the dealer's opening ask (buys) or the premium over the dealer's opening bid (sells).

| dealer, side | threads | deals | deal rate | mean gain | median gain | team price steps per deal |
|---|---|---|---|---|---|---|
| Abuela buy | 283 | 102 | 36 % | 22.7 % | 24.1 % | 3.8 |
| Abuela sell | 235 | 92 | 39 % | 12.5 % | 20.0 % | 3.9 |
| Chato buy | 262 | 39 | **15 %** | 10.1 % | 9.1 % | 5.3 |
| Chato sell | 70 | 25 | 36 % | 7.8 % | 7.7 % | 4.7 |
| Pícaros buy | 207 | 71 | 34 % | 22.6 % | 23.3 % | 3.9 |
| Pícaros sell | 124 | 22 | 18 % | 17.3 % | 25.0 % | 3.5 |
| Pilar sell | 240 | 115 | **48 %** | 12.5 % | 12.5 % | 4.4 |
| Pilar buy (gold packs) | 24 | 0 | 0 % | – | – | – |
| Banco buy / sell | 20 / 13 | 0 / 3 | 0 / 23 % | – / 5.0 % | – | 7.7 |

Per team, all dealers: deal rate runs from 19 % (t10) to 65 % (t03), and mean gain from 11 % (t07) to 20.5 % (t17). **t01: 75 threads, 16 deals (21 %), mean gain 18.0 %.**

Per dealer (07 `team_dealer`):
- **Abuela buys**: t17 got 33 % off her opening, t08 27 %, t03 27 %. t01 got 18 % (3 deals from 13 threads).
- **Chato** is the hardest dealer: 15 % deal rate, about 10 % off. t13 opened 65 buy threads for 3 deals; t10 opened 27 for none.
- **Pícaros buys**: t12 got 31 % off (4 deals), t05 28 %, t06 24 % (7 deals from 10 threads). t01 got 17.5 % (4 deals from 33 threads).
- **Pilar sells**: t01 +18.7 % over her opening (5 deals), t09 +19.7 % (3), t07 +18.8 % (4), t05 +16.6 % (6). The high-volume sellers got less: t08 +9.1 % (12), t04 +9.5 % (11), t13 +10.7 % (13).

What the best threads did. **Team message text is blank in the public feed** (5,594 team messages, none with text), so only teams' prices are visible; the quotes below are the dealers'.
- **t10 → Pilar, SAL rare (thread 1992, ticks 1317–1327): 61 → 77 (+26 %).**
  - t10 opened at 1.5× her bid (92) and came down 3 P every tick: 92, 89, 86, 83, 80, 77.
  - Pilar answered +2 or +3 each time ("You inch, I inch") and closed at 77 without a `final`.
  - Silent, regular, mid-sized steps.
- **t12 ← Pícaros, SAL-09 (thread 1506, ticks 1031–1058): 73 → 48 (−34 %).**
  - t12 anchored at 55 % of their ask (40) and raised 2–3 P per step.
  - At 45 against their 52 it **paused for 23 ticks**, then came back at 47; Pícaros answered with a `final` 48.
  - t12 repeated this (thread 1651, also 48) and resold the two copies to Pilar at 80 and on v16 at 70.
- **t09 → Pilar (thread 778): 16 → 21 (+31 %).** It came down from 30 in 1-P steps over 7 rounds; Pilar's final came at 21.
- **Chato** moves only when you move, and in proportion: "You moved four, I moved two. That is how this works" (thread 1399). A 1-P step earns "You came down one, I came down none".

Step size (17), the median P between a team's consecutive priced messages:
- 1 P: t01, t02, t15, t16, t03.
- 3 P: t05, t06, t08, t18. Most other teams use 2.
- The link to deal rate is weak (t05 and t06 43 %, t18 50 %, against t01 21 % and t02 30 %) and confounded by what each team asked for.

## 3. Score produced by each kind of deal (08, 09, 13 → 14)

Method:
- For each Saturday settlement after tick 610, take each team party's `negotiating` change over the 10-tick snapshot window that contains it.
- Keep it only if that team had no other settlement in that window.
- Subtract the median change of all active teams in the same window, to remove relative-scoring drift.
- A settlement at the snapshot tick is already in that snapshot (t16's Banco sale at tick 1110 shows −5.60 in the 1100→1110 window).
- This leaves 251 clean (team, deal) pairs.

| kind (via, rarity) | n | mean excess | median | share > 0 |
|---|---|---|---|---|
| board buy, rastro, rare | 6 | **+1.64** | +1.83 | 0.67 |
| board sell, rastro, rare | 6 | **+1.26** | +1.12 | 0.83 |
| board sell, team venue, rare | 1 | +1.04 | | |
| board buy / sell, uncommon | 11 / 15 | +0.31 / +0.28 | +0.07 / +0.22 | 0.73 / 0.67 |
| board buy / sell, common | 14 / 13 | +0.08 / +0.12 | 0.0–0.18 | ~0.5 |
| board buy / sell, rastro, epic | 3 / 2 | +0.30 / +0.57 | | |
| swap (multi-card) | 9 | +0.30 | +0.12 | 0.56 |
| Pícaros buy, rare | 33 | **+0.44** | +0.55 | 0.73 |
| Pícaros buy, epic | 14 | +0.20 | +0.12 | 0.50 |
| Pícaros sell, common / uncommon | 7 / 4 | +0.27 / +0.25 | | |
| Pilar sell, uncommon | 37 | +0.23 | +0.18 | 0.59 (min −4.33 = t01 SAL-07) |
| Pilar sell, rare | 16 | +0.27 | +0.08 | 0.62 |
| Pilar sell, epic | 3 | −0.10 | −0.01 | 0.33 |
| Chato sell, uncommon | 7 | +0.31 | +0.17 | 0.71 |
| Chato buy, rare | 5 | +0.14 | 0.00 | 0.40 |
| Abuela buy, common / uncommon | 4 / 9 | +0.10 / 0.00 | 0.00 | ≤ 0.33 |
| Abuela sell, common | 23 | **0.00** | 0.00 | 0.13 |
| Banco sell, epic | 3 | **−2.60** | −2.40 | 0.33 (t16 −5.56, t06 −2.40) |
| pack buy (Abuela / Chato) | 1 / 1 | −0.68 / −0.43 | | |

How to read it:
- A trade scores roughly by how far its price sits inside **both** sides' values: the buyer gains value − price, the seller price − value.
- Team-to-team board trades can do that for both sides at once, which is why rares on rastro paid about +1.3 to +1.6 to each side.
- Dealer prices sit near book value, so dealer deals pay less. The exception is Pícaros' cheap rares, bought at about 56 and resold at about 75–85.

Caveats:
- Samples are small (n=1–7) everywhere except Pícaros, Pilar and Abuela.
- The clean filter drops windows with several deals, so the biggest movers are not in the table (e.g. t10's +4.8 between ticks 1000 and 1100). 09 lists every jump of 0.6 or more with its deals.
- Confounders:
  - `negotiating` is relative: when a top team moves, everyone's score shifts. In the 790→800 window the field median moved −0.47 with no deals at all.
  - Duel points also land in these windows during Duels II (ticks 1239–1431).
  - Completing a page inflates the buyer's side (t10's RET buys at ticks 1030–1040: +2.00).
- **Duels can't be attributed per team**, because `duel.closed` names no team.
  - Proxy (16): the sum of `negotiating` changes in windows where the team had no settlement, ticks 1240–1430.
  - Results: t09 +1.44, t10 +0.88, t17 +0.47, t08 +0.36, t01 −0.03; at the bottom, t15 −1.82, t06 −1.58, t18 −1.53, t05 −1.44.
  - This mixes duels with field drift: read it as a ranking, not as points.
  - Duels I (ticks 459 to ~630) predates the leaderboard capture, which starts at 610, so it can't be measured here.

Day totals, from the first snapshot (tick 610) to the last (tick 1430) (08):

| rank | team | score | Δ score | negotiating | Δ neg | market | Δ mkt |
|---|---|---|---|---|---|---|---|
| 1 | t10 | 37.58 | +8.37 | 25.08 | +7.94 | 12.50 | +0.43 |
| 2 | t18 | 31.26 | +2.01 | 23.76 | +2.01 | 7.50 | 0 |
| 3 | t05 | 30.49 | +1.87 | 22.99 | +1.87 | 7.50 | 0 |
| 4 | t12 | 30.42 | +0.80 | 23.17 | +6.05 | 7.25 | −5.25 |
| 5 | t03 | 29.67 | +9.19 | 23.59 | +6.73 | 6.08 | +2.47 |
| 6 | t06 | 28.76 | +4.95 | 16.90 | +4.73 | 11.87 | +0.23 |
| 7 | t14 | 27.67 | −3.10 | 18.37 | −2.68 | 9.30 | −0.42 |
| 8 | t17 | 25.82 | −0.17 | 17.19 | +0.10 | 8.64 | −0.26 |
| **9** | **t01** | **25.65** | +0.39 | 18.15 | +0.39 | 7.50 | 0 |
| 10 | t13 | 25.02 | −0.43 | 18.25 | −1.71 | 6.77 | +1.28 |
| 11 | t04 | 24.99 | +1.65 | 17.49 | +1.65 | 7.50 | 0 |
| 12 | t08 | 24.91 | +2.59 | 16.46 | +1.60 | 8.45 | +0.99 |
| 13 | t15 | 23.91 | +2.98 | 16.41 | +2.98 | 7.50 | 0 |
| 14 | t16 | 23.60 | +1.40 | 13.44 | −1.26 | 10.16 | +2.66 |
| 15 | t02 | 23.42 | −0.05 | 15.92 | −0.05 | 7.50 | 0 |
| 16 | t09 | 23.22 | +1.51 | 12.33 | −0.40 | 10.89 | +1.91 |
| 17 | t07 | 20.10 | +3.09 | 12.60 | +3.09 | 7.50 | 0 |
| 18 | t11 | 7.50 | 0 | 0 | 0 | 7.50 | 0 |

## 4. Patterns that worked, and patterns that didn't

1. **Flips: buy from Pícaros, resell to Pilar or on rastro (12).**
   - Saturday had 84 buy-then-resell round trips (same asset id), 46 of them positive, +604 P net.
   - By route:

     | Route | Trips | Net |
     |---|---|---|
     | Pícaros → Pilar | 20 | +497 P |
     | Pícaros → rastro | 8 | +277 P |
     | Abuela → Pilar | 13 | −12 P |
     | Chato → Pilar | 6 | −63 P |
     | Pícaros → Banco | 3 | −101 P |

   - Top margins:
     - t06 RET-11: Pícaros 137 → rastro 216 (+79), two ticks apart.
     - t18 SAL-11: 139 → Pilar 199 (+60).
     - t10 SAL-11: 155 → rastro 207 (+52).
     - t12 SAL-09: 48 → Pilar 80 (+32) in 9 ticks.
   - Per team: t10 6 flips (+181 P), t06 5 (+136), t14 3 (+90), t12 3 (+78), t08 5 (+56), t04 3 (+54).
   - Flips need L4 (Pícaros) and cash; most happened after tick 1000.
2. **Salamanca fever (ticks 939–1178, 11).**
   - Pilar paid **78.2** on average for SAL rares during the fever (n=15, 71–87), against 68.5 before (n=4) and 72.4 after (n=5).
   - SAL uncommons: 28.0 during, against 24.3 before.
   - The rare sellers were t08, t10, t12, t14, t16 and t17, mostly with cards just bought from Pícaros. Prices for other sets did not move.
3. **Payday (+400 P to every team at tick 1201, 18).**
   - Buys after Payday: t10 554 P, t04 551, t12 475, t06 436, t08 290 … t01 61.
   - Pícaros epics after Payday: 9 buys (t03 ×2, t04 ×2, t05, t06 ×2, t08 ×2, t10, t12, t13).
   - `negotiating` change from tick 1200 to 1430:
     - t10 +3.6, t12 +2.4, t04 +1.4.
     - t01 +1.9, from our LAT-10 board sale.
     - t06 −3.85: the Banco sale (−2.40), then RET-06 sold on rastro at 30 and bought back from Chato at 61 (−2.12 in the 1410→1420 window).
4. **Farming deal volume did not pay.**
   - t13 opened 209 dealer threads (65 of them Chato buys, for 3 deals), made 46 dealer deals and ended −1.71 `negotiating`.
   - t07 bought LAT-06 from Chato at 26 and sold it back to him at 14 seven ticks later (−12 P).
5. **Selling epics to dealers loses.** Banco paid 116–120 for epics that Pilar bought at 179–199 and teams bought at 195–216 on rastro. Both Banco sellers lost score (t16 −5.56, t06 −2.40).
6. **Workshop, gifts, eggs and badges** (15; counts only, since the logs-eggs session owns these):
   - Workshop crafts: t08 5, t17 5, t14 4, t01 4, t06 4, …
   - Abuela gifts: 39, spread across 16 teams (t01 got 3).
   - Eggs found: t08 5, t10 5, t02 4, t05 4.
   - Badges: "Sharp ear" (11 teams); "Trickster tricked" (6 teams between ticks 1227 and 1336: t18, t10, t05, t08, t02, t06); "Castizo" (t08, t02, t10, t05). t01 has no badge.
   - None of them has a visible score effect.

## What could not be verified
- Per-team duel results: the feed has no team on `duel.closed`. Duels I's score effect: the leaderboard capture starts at tick 610.
- Team message text: it is blank in the public feed, so tactics are read from prices and dealer replies only.
- Whether page completion or ladder share drove any single jump: the board shows only `negotiating`.
- The query-16 Duels II proxy mixes duels with relative drift.

## Files
All in this folder:
- `01_settlements_by_team_kind.sql`, `02_level_unlocks.sql`, `02b_dealer_items_by_side.sql`
- `03_dealer_curves_by_dealer_side.sql`, `04_dealer_curves_by_team.sql`: raw `dealer_curves`, superseded by 06 because of the fill bug
- `05_best_threads.sql`
- `06_dealer_threads_clean.sql` with `07_thread_stats.py` (modes: dealer, dealer_cls, team, team_dealer)
- `08_leaderboard_day.sql`, `09_neg_jumps.sql`, `10_epics_banco_payday.sql`, `11_fever_pilar.sql`, `12_round_trips.sql`
- `13_clean_attribution.sql` with `14_clean_summary.py`
- `15_misc_events.sql`, `16_duels2_residual.sql`, `17_team_message_style.sql`, `18_payday_spend.sql`
