# B9 · Packs EV: sealed packs as inventory vs ladder cash (night shift, 3 Oct 2026)

Draft PR on `night/b9-packs-ev`, stacked on W7's `night/w7-page-economics` (#87). Nothing went live.
- **Inputs:** Friday's public feed (local capture merged with the shared DB, read-only, ticks 0–159), our tick-149 `/me` (aggregates only here), the catalog fixture, and W4's chaser map (#79, P(top set) ≥ 0.5).
- **Recompute:** `uv run bazaar plan packs` (read-only; every input can come from a file).

## Verdict: no. Pack cash loses to ladder deals at the margin.
| use of cash | round points per prima | source |
|---|---|---|
| Abuela best three (W3, share ~0.95) | **0.044** | W7 §4, W5 ladder model |
| three Chato uncommons (needs `dealer_price_caps = chato:uncommon=31`) | 0.021 | W7 §4 |
| W4's seven trades, model fills (+80 P) | 0.016–0.049 | W7 §4 |
| W4's seven trades at Friday's fill rates (+4.8 P) | 0.001–0.003 | W4 §3 |
| a 4th Abuela deal (outside the best three) | **0** | RULES.md: best three per level |
| **one `sobre_barrio` at 20 P, cards resold at Friday's fill rates** | **0.0012–0.0036** | this model |
| one `sobre_barrio` at 20 P, every chased card sold (optimistic what-if) | 0.006–0.017 | this model |

- **At Friday's liquidity** a pack is worth **12–37× less per prima** than the ladder's best three.
- **In the optimistic what-if** (every card whose set has a chaser sells), it is still **2.6–7.8× less**.
- **After the best three** the ladder's own marginal is 0, so spare cash should be compared with the Chato caps or W4's trades. A pack loses to both, except W4 at Friday's fill rates, where both are near zero.
- **Packs cannot even be bought today.** Abuela's pack limits sit at 20–24 against `max_price_pack` 20, so most threads never fill (W3: deal rate 0.12 on the replays).
- **As a ladder deal**, a pack (share ~0.90 uncapped) never displaces a common (~0.975) from Abuela's best three.

## Why a pack scores so little
A pack scores only two ways. First, as a dealer deal: its ladder share, if it is among the best three, which it never is. Second, through its cards sold to other teams: the trade surplus `price − our private value` (W7 §1). Holding cards, pack luck and cash score nothing.

**Pack contents** (catalog): `sobre_barrio` = common, common, (common 0.75 / uncommon 0.25), expected book 33.8. Assumption: each slot draws uniformly from the released sets' cards of that rarity (LAV, MAL, LAT, SAL, plus RET on Saturday). Printed-out cards are skipped, and a printed-out rarity gives the next one down.

**Friday's team tape:** single cards for cash, team to team. *Listed* counts distinct cards (a reprice of the same card counts once), and *sold* counts those that later changed hands between two teams. The median is over every single-card team-to-team sale.

| rarity | listed | sold | fill rate | median price |
|---|---|---|---|---|
| common | 103 | 21 | 20 % | 9 |
| uncommon | 30 | 8 | 27 % | 24.5 |
| rare | 8 | 5 | 62 % | 70 |
| pack | – | 0 | – | – |

**Per pack, on Saturday's five sets:**

| | value |
|---|---|
| expected book | 33.8 |
| private value of its cards if kept (scores nothing; includes the page-bonus share of cards we lack) | 24.6 |
| scored resale surplus at the tape's fill rates | **1.45 P** |
| scored resale surplus if every chased card sells (W4's map) | 6.8 P |
| cash back from resale at the tape's fill rates | 2.6 P |

Only cards worth less to us than their net sale (price − El Rastro's 5 % + 1 P) are listed; the rest are kept. With Friday's four sets: 1.81 P, and 8.5 P in the what-if. At the uncapped median price of 22 P the per-prima figures are 10 % lower.

## What to do with packs on Saturday (consistent with W7's cash plan)
1. **Buy none from Abuela.** W7's spare ~88 P is better held for the Chato caps (W3 #81) or the venue decision (W7 decision C).
2. **Open the free packs** (the grant's and the welcome pack). Opening is free, and luck does not score (W7 step 1).
3. **List only the duplicates whose net sale beats our value**, addressed to W4's chasers through W4's `bazaar sell list … --to tNN` (#79), which respects the per-counterparty cap. That is +1.5 to +6.8 P of scored surplus per pack, at zero cash.

## Risks and what is unverified
- The tape is Friday's: 24 common sales across 18 teams. Saturday's liquidity may differ. The what-if row is the ceiling if every chaser buys.
- The draw is assumed uniform over sets and cards; a pack may weight its own neighbourhood.
- The round-point conversion uses W5's assumed trade weight (5) and a top-3 trade raw of 100–300 P (W7 §4). The comparison rows (ladder, Chato, W4) are **Friday constants** from W7's table, not recomputed by `plan packs`. Re-check them with `bazaar plan pages` once the best three are spent.
- Rares traded mostly through bids: the tape's rare fill rate (5 of 8 listed) rests on very few listings.
- Pack resale between teams has never happened (0 pack trades on Friday), so selling *sealed* packs to chasers has no price evidence at all.
