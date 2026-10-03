# W7 · Page economics: what finishing pages is worth, what it costs, where the cash goes

Night of 3 Oct 2026. Draft PR #87, base `main`. Nothing touched the live game.

**Data:** the public feed in Postgres (read-only SELECT, 3,735 events, ticks 0–159) and our `/me` at tick 149 (cash 353, the same as the tick-159 snapshot). Also the catalog, dealers and schedule fixtures, W3's ladder plans (#81), and W4's trade plan and affinity map (#79).

**Tool:** `uv run bazaar plan pages` (read-only) recomputes every number below. In this report, LAV is our ×1.6 set (STRATEGY.md) and SAL our ×1.3; real per-card values for the other sets are in the PR body.

## Verdict

| Question | Answer |
|---|---|
| Is finishing a page worth it, for score? | **No, not under today's caps.** All 7 missing rares fail at `max_price_rare` 80. Chato fills at 82–93. The team holders' reservations are 94–106 (book × W4's expected multiplier). |
| Which page, if the caps move? | **LAV.** It is 7/10 and needs LAV-08 plus two rares. With a Chato rare cap of 93 (W3's `dealer_price_caps` line), the page costs about 204 P for +370 P of private value (+106 of it the bonus). That value scores nothing by itself. `--what-if-caps chato:rare=93` gives the timing under floor 270 with no venue: W4's plan takes LAV-08, LAV-09 comes from Chato on Saturday, LAV-10 on Sunday (the cash floor), and Saturday's spend is 225 P. |
| Cash at Saturday 09:03 | 353 + 150 grant = **503 P**. The 150 P grant and the pack are verified in the `/api/schedule` fixture; Sunday adds another 150 P. With `cash_floor` 270, **233 P can be spent**. |
| Venue Saturday morning | **Refused by our own guardrails.** PR #71's check makes the venue need bond + fee + floor = 540 P, and we have 503. |
| What the 233 P should buy | W4's 7 trades (82 P, expected +80 P of trade surplus), then W3's three best Abuela deals on page cards W4 doesn't buy (~54 P). Hold the rest (~88 P Saturday, ~238 P by Sunday) for decision B (see the decisions section). |

## 1. How page value scores
- RULES.md "Scoring" has no album term. The page bonus (`page_bonus` 0.25 × Σ book × our multiplier over the 10 page cards) only raises private value.
- **That private value scores only as surplus in a team trade.** A dealer purchase scores its ladder share, and only if it is one of our best three deals at that level.
- So the plan picks a source by its scoring channel:
  - **team:** chosen when the card's value minus price ≥ `min_buy_surplus`;
  - **dealer:** chosen while our best three at that level still have room;
  - **otherwise:** the cheapest fillable source.
- The page's last card is bought from a team, so the bonus can count as surplus on that trade. **Unverified:** whether the server credits the bonus to the trade that completes the page. Cheap check: `GET /api/me/value?card=<last missing card>`, once the others are held.

## 2. Missing page cards (LAV and SAL), with W4's affinity map
| card | value / + bonus share | minted ≥ | dealer (fills) | teams (cheapest holder) | verdict |
|---|---|---|---|---|---|
| LAV-08 U | 40 / +16 | 13 | Abuela ~22 (17–29) | t03 ask 24 + fee 3 | buy: W4 bids 22 to t03 |
| LAV-09 R | 112 / +45 | 4 | Chato ~90 (82–93): **cap 80** | t07 wants ~97: **cap 80** | blocked |
| LAV-10 R | 112 / +45 | 5 | Chato ~90: **cap 80** | t07 paid 91 → 97: **cap 80** | blocked |
| SAL-09 R | 91 / +29 | 4 | Chato ~90: **cap 80** | t13 wants 98: **cap 80** | blocked |
| SAL-10 R | 91 / +29 | 4 | Chato ~90: **cap 80** | t18 wants 94: **cap 80** | blocked |
| SAL-07 U | 32.5 / +10 | 10 | Abuela ~22 | t10 28 (cap 26) | Abuela, ladder best three |
| SAL-08 U | 32.5 / +10 | 10 | Abuela ~22 | t04/t05 ~27 + fee | W4 bids 24 to t04 |
| SAL-02 C | 13 / +4 | 13 | Abuela ~10 | t10 11 + fee 2 | Abuela, ladder best three |
| SAL-05 C | 13 / +4 | 14 | Abuela ~10 | t15 ask 9 + fee | W4 swap with t12 |

Notes on the table:
- **minted ≥** is a lower bound: the highest serial seen in the feed. The catalog fixture is stale; our own LAV-03 is #17, while the fixture says 15 minted.
- **Holders** come from settlements, listings and gifts. A holder's reservation is the larger of what it paid and book × its multiplier (W4's posterior; without it, the top multiplier for a chaser and the mean for anyone else).
- **Sensitivity:** with the feed's cruder chaser guess, SAL turns finishable (rares from t13 at ~80, page bonus 86) and LAV-09 is buyable from t07 at 77. With W4's map, neither is.
- **LAT (×0.5):** skip. Its 6 missing cards cost about 166 P for 108 P of value.

## 3. The cash plan (`bazaar plan pages --ladder-plan … --trades … --affinity …`)
The walk goes hour by hour and respects:
- every grant in the schedule;
- `max_spend_per_game_hour` 150;
- `cash_floor` (bond + fee kept on top of it until a planned venue opens, as #71 does);
- W3's ladder slots (a slot that names a card W4 already buys is flagged as a duplicate);
- W4's trades, whose cash is committed at the open.

| scenario (floor 270 unless stated; venue rows apply PR #71's rule, unmerged, unless stated) | venue | ladder deals | W4 trades placed | trade surplus | Saturday spend | end cash |
|---|---|---|---|---|---|---|
| no venue | – | 3 | 7 on Sat | **+82 P** | 144 | 510 |
| venue at the open (h4) | **refused** (503 < 540) | 3 | 7 on Sat | +82 P | 144 | 510 |
| venue at h9 (14:00) | **refused**: the 540 P reserve freezes the morning | 0 (all held) | 7, after 14:00 | +82 P | 90 | 563 |
| venue Sunday (h18) | opens (653 ≥ 540) | 0 (all held) | 7 on Sunday | +82 P | 0 | 293 |
| venue at the open, **without #71's floor rule** | opens (503 − 270 = 233) | 0 | 7 on Sunday | +82 P | 270 | 293 |
| venue at the open + planned sells (LAT-09 and 2 more, +93 P) | opens | 0 | 4 Sat, 3 Sun | +82 P | 325 | 386 |
| venue at the open, **what-if `cash_floor` 0** | opens | 3 | 7 on Sat | +82 P | 414 | 240 |

W3's page-card plan and W4's 09:00 plan both buy **LAV-08, SAL-05, SAL-08, MAL-07 and MAL-08**. Five of W3's eight Abuela slots are duplicates. W7's call is that W4 keeps them, because a team buy scores and a fourth Abuela deal does not.

## 4. What each use of cash buys (round points, W5's score model #78; weights for trades and the bench are assumed)
| Use | Cash | Round points (Saturday round ≈ 0.4 final points per round point) |
|---|---|---|
| Abuela best three at ~0.95 share (our Friday mean is 0.73) | ~54 P | **+2.4** (ladder 8.3 → 10.7 at Friday's top-3 mean of 1.11) |
| Three Chato uncommons at ~0.8 share (needs a cap of 31) | ~87 P | **+1.8** (level 2 then hits the 12.5 cap) |
| Two Chato rares at ~0.43 share (needs a cap of 93); completes LAV | ~181 P | +1.6, plus +330 P of private value that does not score |
| W4's trades, E +80 P of trade surplus | 82 P | +1.3 to +4.0 (top-3 trade raw of 300 to 100 P, weight 5) |
| Venue (20 P sunk + 250 P bond locked) | 270 P | +0.45 a round if our free stall already scores (W1b: 0.53 vs 0.50); +7.5 to 8 a round if it does not |
| Page bonus through a team trade | – | 0 today: no seller at or under the cap |

## 5. Saturday 09:00 steps (within 1 accept per tick, 12 listings, 6 conversations, Abuela 8 deals an hour, 150 P an hour)
1. **Open** the welcome pack (asset 425) and the pack in the grant. Opening is free and luck does not score. The welcome pack's rare slot gives a LAV rare with p ≈ 0.4 × 2/10. Then re-run `bazaar plan pages`.
2. **Post W4's plan:** 4 bids and 3 thread swaps, 82 P committed, every counterparty under 25 %.
3. **Run W3's best three Abuela deals** on SAL-02 (C), SAL-07 (U) and MAL-06 (U): about 54 P at 21→25 / 8→12. Drop W3's other slots; they score nothing.
4. **Take t06's MAL-04 ask** #2633 (7 P + fee 2, +2 P), if it is still open.
5. **Hold about 88 P.** Re-plan after the h5 Market Test (10:00): if `/me` shows `bench_points` > 0 with no venue, the free stall scores for us, and the venue is worth ~0.45 a round, so do not lock 270 P in it.

## Decisions for Marius
A. **`cash_floor` after the venue decision.**
   - With #71's rule, 270 makes the venue need 540 P, which Saturday 09:03 does not have.
   - If we never open a venue, 270 P sits idle all weekend.
   - Either way, after the decision the floor should drop to a small buffer (e.g. 30). Not changed tonight.

B. **Chato** (W3's `dealer_price_caps`). Uncommons at a cap of 31 give the level-2 ladder points for about 87 P. Rares at a cap of 93 cost 181 P for about the same points, plus a completed LAV page (not scored). `bazaar plan pages --what-if-caps chato:rare=93,chato:uncommon=31` plans either one without touching GUARDRAILS.md.

C. **Venue timing.** Decide after the 10:00 Market Test result.

## Risks and what is unverified
- How the server credits the page bonus to trades.
- Whether the free stall scores for us, and whether the ladder restarts each round (W5).
- The score weights for trades and the bench.
- **Data age:** prices, holders and minted counts are Friday's, and RET enters Saturday. Re-run with the live feed at 09:00; every input can also be passed as a file.
- **Team prices are models, not fills:** reservations come from book × multiplier and what the holder paid.
