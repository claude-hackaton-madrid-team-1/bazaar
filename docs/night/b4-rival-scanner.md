# B4 · Rival profiles and the opportunity scanner (night of 3 Oct 2026)

- Written for draft PR #98 (`night/b4-rival-scanner`); the work landed through the takeover PR #138, stacked on #137 (the #79 takeover).
- Read-only: nothing touched the live game.
- Data: the public feed from the shared DB (ticks 0–159) and our `/me` at tick 149, unchanged at tick 159.

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
- **Taker `accept_bids`** (`bazaar agent taker --accept-bids`, default off): the taker also sells into standing bids for cards we hold that beat what the copy costs us by `sell_min_surplus`.
  - It hands over the least valuable copy (`accept(offer, assets=[id])`) and never sells a copy that is in one of our open offers.
  - Same gates as a buy: `guardrails.check` (sell floor, counterparty share), the duel grace and the shared accept quota.
- **Scope split with W8:** `score_offer` is offered to W8's `arb scan` (W8 said it would call it; not wired yet). W8 owns crossings and duplicate buys, so the scanner skips asks for cards we already hold.

## The 09:00 board (the close of Friday)

Our holdings, values and per-card surplus are private: the per-offer list is in `_night/b4-private-numbers.md` (outside the repo), and `bazaar opportunities` prints it.

| | 09:00 board |
|---|---|
| Offers by other teams | 47 |
| With surplus for us | 13 |
| Allowed by the guardrails | 7: one sale into a standing bid and six buys of commons; two of them are snipes |
| Refused by the price caps | 6: five uncommons above `max_price_uncommon` 26 (asks of 25–40 plus fee) and one common above `max_price_common` 12 |
| Against W4's plan | 5 of the allowed buys are for two cards W4 also plans to trade for: when the scanner shows the conflict, take the standing ask (it fills now) and drop the plan's trade |

## Friday replay: what the boards offered us

I scored every offer by another team once, with our current album. This is an approximation: our values moved little on Friday (4 dealer buys).

| | Friday |
|---|---|
| Plain offers by other teams | 702 |
| With surplus for us and allowed | 91 offers (cards relisted many times): **8 distinct cards** (the surplus at our values is kept out of the repo) |
| Taken by other teams first | 3 offers, 3, 4 and 5 ticks after listing (t10, t17, t04) |
| The two big ones | two rare asks well below our value. Rivals took them 3 and 5 ticks after they were listed |
| Left untaken (expired or cancelled) | 88 offers, mostly cheap commons and uncommons |

A taker scanning every tick would have caught both rare snipes. Rivals took them within 3–5 ticks, so a scan only every few ticks would lose them.

## Rival profiles (Friday)

All 43 one-card team fills on Friday match an offer at its own price (asks 26, bids 17). Asks fill 26/536 (5 %), bids 17/199 (9 %). Per copy listed, 25/133 public asks sold (19 %) against 1/18 addressed ones (6 %).

| team | top set | asks (fill) | ask / tape | bids | bid / tape | takes (median ticks) | reprices (step) | tags |
|---|---|---|---:|---:|---:|---|---|---|
| t02 | SAL 0.56 | 10 (0 %) | 1.57 | 14 | 0.42 | 0 | 7 (+8 %) | relister |
| t04 | LAV 0.58 | 13 (8 %) | 1.11 | 21 | 1.00 | 5 (4) | 4 | relister |
| t05 | LAV 0.78 | 33 (15 %) | 1.11 | 22 | 0.67 | 2 (10) | 9 (−10 %) | relister |
| t06 | SAL 0.52 | 193 (3 %) | 1.33 | 5 | 0.94 | 4 (6) | 26 (−17 %) | – |
| t08 | MAL 0.42 | 50 (2 %) | 1.41 | 66 | 0.67 | 3 (10) | 11 (+8 %) | – |
| t09 | LAV 0.38 | 3 (0 %) | 0.93 | 0 | – | 0 | 0 | cheap seller |
| t10 | LAV 0.72 | 15 (13 %) | 1.33 | 4 | 1.42 | 4 (7.5) | 6 (−14 %) | overbidder, relister |
| t12 | MAL 0.72 | 45 (4 %) | 1.33 | 9 | 1.12 | 7 (4) | 3 (−14 %) | overbidder |
| t13 | SAL 0.45 | 45 (4 %) | 1.19 | 17 | 0.67 | 1 (2) | 13 (−18 %) | relister |
| t14 | LAV 0.70 | 16 (0 %) | 1.18 | 1 | 2.25 | 4 (3.5) | 1 | – |
| t15 | LAT 0.52 | 37 (3 %) | 1.00 | 8 | 0.76 | 3 (2) | 8 (−13 %) | cheap seller, fast taker |
| t17 | SAL 0.46 | 31 (0 %) | 1.11 | 22 | 1.05 | 3 (5) | 0 | overbidder |
| t18 | LAT 0.51 | 34 (15 %) | 1.11 | 10 | 0.94 | 2 (3.5) | 13 (−10 %) | relister |

What to do with these profiles:
- **Sell to the overbidders.** t10, t12 and t17 bid above the tape, so the maker's asks belong in front of them.
- **The fast taker is t15** (median 2 ticks over 3 takes). Teams with a single take (t13) say little.
- **"Cheap seller" is measured against the tape, per copy:** a team's latest ask on each distinct copy, median below the tape over at least 3 copies (relisting one copy does not count). Own value is shown, not used for the tag, because it depends on copies we may not have seen. On Friday t09 and t15 qualify.
- **Asks sit above the tape** (×1.0–1.6), and most teams relist a copy every few ticks at −10 % to −18 % per step.

## Verdict

| Item | Verdict |
|---|---|
| Profiles + lifecycle | **GO** (all 43 fills matched at their own price) |
| Read-only scanner | **GO**. It is consistent with the W4 plan (flags conflicts) and with W8 (no overlap; `score_offer` offered) |
| Taker sell side (`accept_bids`) | **GO as code, default off.** At 09:00 it would sell into the one standing rare bid that beats our value |

## Risks

- **The replay uses our tick-149 album for the whole day.** Its surplus figures are an upper bound for the early ticks.
- **Own value is rough.** A profile's "own value" uses the team's expected multiplier and the copies we have seen it list. Unseen duplicates make the real value lower, and a low expected multiplier makes it tiny, so the ask/own ratio can be huge (t17: 12.3). It is shown, never acted on.
- **El Rastro only.** The public feed showed no team-venue listings on Friday (W8 found the same). `--live` reads every venue we may trade on.

## Decisions for Marius

1. **(Superseded: the taker already runs live on Railway; any change goes through the coordinator.)** Scan every tick. On Friday it would have caught the two rare snipes that rivals took within 3–5 ticks (the largest surplus of the day).
   - Its buys use the same cash above `cash_floor` as W4's bids and W3's ladder. Decide the split: the taker takes standing offers that fill at once, while W4's addressed bids fill about 6 % of the time on Friday's evidence.
2. **`--accept-bids` stays OFF** (takeover decision, #138) until the reviews' open items are closed: the maker does not read the taker's `sell:` reservations yet, and `venues_from` drops `pending_fee` (#110). The taker then also sells into standing bids that beat our loss by `sell_min_surplus`, through the same guardrails, duel grace and accept quota.
3. **Price caps:** 6 of the 13 morning opportunities are blocked by them (five uncommons above 26, one common above 12). Values unchanged tonight.
4. **At 09:00:** run `uv run bazaar opportunities --live` (reads only) next to `uv run bazaar trade-plan --live`.

## Reviews, all fixed

- **`/code-review high`** (10 findings) and **r1** (2 high, 3 medium, 4 low):
  - A settlement now fills the offer at its own price: the named copy first, card-only matches flagged inexact and left out of fills, takes and the replay. A buyer taking an ask no longer fills its own bid. All 43 Friday fills now match exactly; per copy listed, the fill rate is 19 % public against 6 % addressed.
  - The scanner now applies the taker's buy filters: released page cards only, the bidder's next copy, the sell floor net of the fee, and no price for an unknown venue.
  - The taker reserves a copy it sold into a bid as `sell:<id>`, so the copy is not sold again the next tick. It sells a free copy when the cheapest is in our own ask.
  - With a key, the scanner counts our open offers and this hour's spend.
  - This report no longer carries our private numbers (they are in `_night/b4-private-numbers.md`).
- **Accept slot:** buys and sells share the one accept per tick, ranked by surplus. A dealer's final offer still goes first.
- **Merging:** this branch's early commits carried our private numbers in this report. Squash-merge it after #79.
