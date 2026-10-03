# B4 · Rival profiles and the opportunity scanner (night of 3 Oct 2026)

- Draft PR on `night/b4-rival-scanner`, stacked on #79 (`night/w4-trade-desk`).
- Read-only: nothing touched the live game.
- Data: the public feed from the shared DB (ticks 0–159) and our `/me` at tick 149, the same 20 assets as tick 159.

## What was built

- **`rivals.listings(events)`** rebuilds each board offer's life from the feed.
  - Every offer is listed, then cancelled, filled or expired.
  - "Filled" means the settlement of its copy (an ask) or of the card it wanted (a bid), after it was listed. An accept on the offer's last tick settles one tick later.
  - Of the 44 one-card team settlements on Friday, 43 match an offer.
- **`rivals.board_at(rows, tick)`** gives the board at any tick. Nothing settles while the doors are closed, so **the board at Friday's last tick is the board that opens Saturday.**
- **`rivals.profiles(...)`** builds one profile per team:
  - its asks against the tape and against its own value from W4's affinity map;
  - how often its offers fill, its takes of others' offers and how fast it takes them, and its reprices.
  - Tags: `cheap seller`, `overbidder`, `fast taker`, `relister`.
- **`opportunities.score_offer` / `scan` / `replay`** score every standing offer for us:
  - an ask for a page card we miss: our worth − price − fee;
  - a bid for a card we hold: price − fee − our loss;
  - each carries the maker's side, a `snipe` or `overbid` tag, `guardrails.check` exactly as the taker would send it (counterparty cap included), and the W4 plan trade it competes with.
- **Commands:** `bazaar rivals` and `bazaar opportunities [--live] [--replay]`. Without `--live`, the board is rebuilt from the feed.
- **Scope split with W8:** W8's `arb scan` imports `score_offer`. W8 owns crossings and duplicate buys, so the scanner skips asks for cards we already hold.

## The 09:00 board (the close of Friday)

The board has 47 offers by other teams. 13 have surplus for us, and 7 of those pass the guardrails:

| # | we | card | price (fee) | maker | ours | theirs | note |
|---|---|---|---|---|---:|---:|---|
| 2503 | sell | LAT-09 | 62 (5) | t18 | +11.5 | +37.0 | open until tick 205 |
| 2612, 2632 | buy | MAL-02 | 3 (2) | t06 | +9.1 | +1.1 | better than W4's plan bid of 10 to t06 |
| 2671 | buy | SAL-05 | 9 (2) | t15 | +6.1 | −0.1 | snipe; competes with W4's swap for SAL-05 |
| 2633 | buy | MAL-04 | 7 (2) | t06 | +5.1 | −0.7 | snipe |
| 2462 | buy | MAL-04 | 8 (2) | t18 | +4.1 | +0.4 | |
| 2697 | buy | MAL-02 | 10 (2) | t17 | +2.1 | +6.5 | |

The other 6 are refused by the price caps:
- LAV-08 at 40 + 3 against `max_price_uncommon` 26;
- MAL-07 at 25 and at 30;
- SAL-07 at 32 and MAL-06 at 28;
- SAL-02 at 12 + 2 against `max_price_common` 12.

**Consequences for W4's plan:**
- Take t06's MAL-02 ask at 3 instead of bidding 10.
- Selling LAT-09 into t18's bid of 62 gets +11.5 P now. W4's ask at 70 had held LAT-09 back for the 25 % rule; this sale is the faster route.

## Friday replay: what the boards offered us

I scored every offer by another team once, with our current album. This is an approximation: our values moved little on Friday (4 dealer buys).

| | Friday |
|---|---|
| Plain offers by other teams | 702 |
| With surplus for us and allowed | 91 offers (one card relisted many times), **8 distinct cards, +160.8 P at the best price each** |
| Taken by other teams first | 4 offers, 3, 4, 5 and 10 ticks after listing (t17 ×2, t10, t04) |
| The two big ones | LAV-10 at 70 by t08: **+82 P** for us, taken by t10 3 ticks later. SAL-09 at 75 by t12: +40 P, taken by t17 5 ticks later |
| Left untaken (expired or cancelled) | 87 offers, mostly cheap commons and uncommons of MAL and SAL |

A taker scanning every tick would have caught both big snipes: each was a page card we miss, and each total (75 and 80) fits under `max_price_rare` and the 83 P above `cash_floor`. Rivals take good snipes within 3–5 ticks, so a scan only every few ticks would lose them.

## Rival profiles (Friday)

| team | top set | asks (filled) | ask / tape | ask / own value | bids | bid / tape | takes (median ticks) | tags |
|---|---|---|---:|---:|---:|---:|---|---|
| t02 | SAL 0.56 | 10 (0) | 1.57 | 0.94 | 14 | 0.42 | 0 | cheap seller, relister |
| t04 | LAV 0.58 | 13 (1) | 1.11 | 1.67 | 21 | 1.00 | 5 (4) | relister |
| t05 | LAV 0.78 | 33 (5) | 1.11 | 3.29 | 22 | 0.67 | 2 (10) | relister |
| t06 | SAL 0.52 | 193 (5) | 1.33 | 3.93 | 5 | 0.94 | 3 (7) | – |
| t08 | MAL 0.42 | 50 (1) | 1.41 | 2.91 | 66 | 0.67 | 3 (10) | – |
| t10 | LAV 0.72 | 15 (2) | 1.33 | 1.00 | 4 | 1.42 | 4 (7.5) | cheap seller, overbidder, relister |
| t12 | MAL 0.72 | 45 (2) | 1.33 | 0.66 | 9 | 1.12 | 7 (4) | cheap seller, overbidder |
| t13 | SAL 0.45 | 45 (2) | 1.19 | 1.43 | 17 | 0.67 | 1 (2) | relister |
| t14 | LAV 0.70 | 16 (0) | 1.18 | 0.81 | 1 | 2.25 | 4 (3.5) | cheap seller |
| t15 | LAT 0.52 | 37 (1) | 1.00 | 0.85 | 8 | 0.76 | 4 (2.5) | cheap seller |
| t17 | SAL 0.46 | 31 (0) | 1.11 | 12.3 | 22 | 1.05 | 4 (7.5) | overbidder |
| t18 | LAT 0.51 | 34 (6) | 1.11 | 2.26 | 10 | 0.94 | 1 (4) | relister |

What to do with these profiles:
- **Sell to the overbidders.** t10, t12 and t17 bid above the tape. They are where the maker's asks should go.
- **Watch the cheap sellers.** t12, t14, t15, t02 and t10 ask below their own expected value. Their asks are where snipes come from.
- **Asks rarely fill on Friday's board:** 28 of 536 asks (5 %); bids fill 15 of 199 (8 %). Asks sit above the tape (×1.0–1.6), and most teams relist a copy every few ticks at about −10 % to −18 %.
- **Corrected W4 claim:** W4 said 34 % of public asks filled against 7 % of addressed ones. Per copy listed it is 20 % (26/133) against 6 % (1/18); per listing, 5 % against 4 %. The posting choice stands, and the fix is on #79 (2d539b1).

## Verdict

| Item | Verdict |
|---|---|
| Profiles + lifecycle | **GO** (43/44 fills matched) |
| Read-only scanner | **GO**. It is consistent with the W4 plan (flags conflicts) and with W8 (shared `score_offer`, no overlap) |
| Live sell-side accepts | **Not built tonight** (see decisions) |

## Risks

- **The replay uses our tick-149 album for the whole day.** Its surplus figures are an upper bound for the early ticks.
- **Values for partly-known teams are wrong.** A profile's "own value" uses the team's expected multiplier and the copies we have seen it list. Starting hands are unseen, so for teams with few signals this is off (t17's ask/own of 12.3 says we under-count its copies).
- **El Rastro only.** The public feed showed no team-venue listings on Friday (W8 found the same). `--live` reads every venue we may trade on.

## Decisions for Marius

1. **Run the taker live from 09:00, scanning every tick.** On Friday it would have caught the two snipes worth +82 P and +40 P that rivals took within 3–5 ticks.
2. **Sell-side accepts:** the taker never accepts a bid. Today that leaves LAT-09 into t18's 62 bid (+11.5 P). Accepting bids would need a taker switch, default off.
3. **Price caps:** 6 of the 13 morning opportunities are blocked by them (all uncommons above 26 and one common above 12). Each is +2 to +13 P at our values. Values unchanged tonight.
4. **At 09:00:** run `uv run bazaar opportunities --live` (reads only) next to `uv run bazaar trade-plan --live`.
