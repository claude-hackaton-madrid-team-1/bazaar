# W8 · Cross-venue arbitrage and duplicate buys (night shift, 3–4 Oct)

**Verdict.** Ship both switches **off** (`arb_enabled = false`, `dup_buy_enabled = false`). **NO-GO** for flipping
either one on Friday's evidence: the Friday market held **0** arbitrage opportunities and **0** worthwhile duplicate
asks. **GO** for the read-only `bazaar arb scan`: run it on Saturday's live boards and flip a switch only when the
conditions below show up in the scan.

PR #101 is stacked on #72 and includes #79 until #79 merges. W8 alone: `git diff origin/night/w4-trade-desk...night/w8-arbitrage`.

## Friday, measured (`bazaar arb study stream.jsonl`, ticks 0–149; full tables in `w8-arbitrage-study.md`)

| | count |
|---|---:|
| Public plain offers (all on El Rastro: no team venue traded on Friday, they open at +3 h) | 462 asks, 174 bids |
| Card-ticks with both an ask and a bid standing | 168, median ask − bid gap **+6 P** (p10 +4 P) |
| Ask/bid pairs for one card from different makers standing in the same tick | 68 |
| … crossed (bid > ask), gross | **2** (MAL-04 9 → 10, +1 P) |
| … net ≥ 3 P after El Rastro's 5 % + 1 P on both legs | **0** |
| … net ≥ 1 P if every fee had been 0 (a 0 bps team venue) | 2 pairs, 1 executable (bid still there the next tick), **1 P** |
| Tape exits (ask + fee below what a team had paid for the card before; no standing bid, so not executable) | 1, 1 P |
| Bids still standing 1 / 2 / 3 ticks after listing (an exit needs ≥ 2) | 99 % / 81 % / 66 % |
| Duplicate asks with surplus ≥ 3 P (or even ≥ 0 P), at any of the six affinity tiers, 2nd or 3rd copy | **0** of 462 |
| Best (least negative) duplicate surplus, top tier 1.6, 2nd copy | common −2, uncommon −14, rare −42 P |
| The same check at our own affinities and holdings (`--me`, tick 123 snapshot) | 0 |

## When a switch is worth flipping (watch for this in `bazaar arb scan`)

**Arbitrage.** A crossing needs the bid to beat the ask by at least this much for net ≥ 3 P (`arb_min_net_spread`):

| ask on → bid on | ask 10 | ask 25 | ask 70 |
|---|---:|---:|---:|
| 0 bps → 0 bps (t12, t02) | +3 | +3 | +3 |
| 0 bps ↔ 50/100 bps (t06, t13) | +4 | +4 | +4 |
| El Rastro → 0 bps, or 0 bps → El Rastro | +5 | +6 | +8 |
| El Rastro → El Rastro | +7 | +9 | +14 |

Friday's book sat a median 6 P on the wrong side (ask above bid). Flip `arb_enabled` when the scan lists crossings
of +3 P or more, with known makers, more than once an hour; each one uses two of the team's accepts (the ask, then
the bid the next tick), shared with duels.

**Duplicates.** One more copy is worth book × affinity × 0.25. The highest ask that still leaves 3 P (`dup_min_surplus`):

| tier | rare (2nd copy worth) | rare ask on 0 bps / El Rastro | uncommon ask on 0 bps / El Rastro | common |
|---:|---:|---:|---:|---|
| 1.6 | 28.0 | ≤ 25 / ≤ 22 | ≤ 7 / ≤ 5 | never |
| 1.3 | 22.8 | ≤ 19 / ≤ 17 | ≤ 5 / ≤ 3 | never |
| 1.1 | 19.2 | ≤ 16 / ≤ 14 | ≤ 3 / ≤ 1 | never |
| 0.9 | 15.8 | ≤ 12 / ≤ 10 | ≤ 2 / never | never |

Friday's cheapest rare ask was 65 P and the median 84 P. Marius's example (a rare at 15 in the 1.6 set: +13) needs a
rare at under a quarter of its usual price. Flip `dup_buy_enabled` only if the scan shows such asks (a seller
dumping, or a team leaving). Epics and legendaries stay blocked anyway: `max_price_for` has no cap for them, so
`check()` refuses every epic buy.

## What shipped (all off by default; existing tests unchanged; 869 tests green)

- **GUARDRAILS.md** (no existing value changed): `arb_enabled` false, `arb_min_net_spread` 3, `arb_max_inventory_p` 60,
  `arb_party_cooldown_ticks` 240, `dup_buy_enabled` false, `dup_min_surplus` 3, `dup_max_spend_per_hour` 40.
  `guardrails.check()` enforces them:
  a buy claims its exception (`Action.held_buy`, `exit_net`, `next_copy_value`) and still meets every other rule
  (cash floor, price caps, hourly spend, accept slot, kill switch, W4's per-counterparty cap on both legs).
- **Ledger**: tagged spend rows in the existing `ledger` table (no schema change, read only when a switch is on):
  `dup:REF`, and `arb:REF:ASSET:BID:VENUE:PRICE:SELLER:BUYER`. Inventory = rows whose exact copy (asset id) is still
  ours; the ring guard and pending exits are rebuilt from these rows after a restart or on another machine.
- **Taker**: duplicate asks; arbitrage buys whose exit bid is on another venue or from another maker, both makers
  resolved to team ids from the feed. Ring guard: neither team was on either side of one of our arbitrages in the
  last 240 ticks. The exit bid must still stand next tick (`expires_tick`). The exit leg's own guardrails (sell floor,
  W4's counterparty share for the bid's maker) are **checked before the buy**. The exit bid is **re-read just before
  the buy**. Once that exact copy is in `/me`, the exit **takes the accept
  first** and hands over that copy (`accept(bid, assets=[id])`). A vanished bid, or no exit within 3 ticks, leaves the card
  to the maker's sell flow (never below `sell_min_value_ratio`).
- **Maker**: no new ask for a copy with a pending exit (4 ticks), so the exit accept cannot fail on a listed copy.
- **Reviews**: an independent review found that exits and inventory were keyed on copy *counts* (wrong copy sold,
  exit never firing, closed rows revived). Fixed by tracking the asset id, with 4 regression tests. r1's review
  (defaults byte-identical over a 400-run taker differential and 20k `check()` calls) found three mediums. All three
  are fixed with tests: the exit bid's expiry, a buy whose exit would be refused, and a ring guard per team rather
  than per pair. Jev is not asked about held-card buys: it values cards we keep, and would veto every duplicate and
  arbitrage.
- Exits run first in the tick, before the dealer desk and the board reads (tight on 15 s ticks).
- **CLI**: `bazaar arb study [STREAM] [--me FILE]` (no file: the agents' feed history, read-only) and
  `bazaar arb scan [--near N]` (reads only; lists the closest misses and whether the taker would take each one).

## End to end in the local simulator (#55, in-process on 127.0.0.1; `docs/night/w8_sim_e2e.py`)

| run | tick 0 | tick 1 | cash |
|---|---|---|---:|
| ask 6 on El Rastro (t02), bid 16 on a 0 bps venue (t03) | bought MAL-01, asset 16 | sold **asset 16** into the bid | **+9** |
| the same, the bid cancelled after tick 0 | bought | re-read: bid gone → the card is left to the maker | −7 |
| ask 6, bid 10 (net below 3) | nothing | nothing | 0 |
| duplicate: LAV-08 (one more worth 4.38) asked at 1, switch off / on | nothing / bought for 3 | | 0 / −2 |
| two bids (t03 16, t05 14); at tick 2 resting t03 asks 5, fresh t06 asks 7 (`w8_sim_two_bids.py`) | t02's copy → t03; then t06's copy → t05 (t03 skipped) | | **+15** |

The taker's value of one more copy equals the sim's `/api/me/value` (4.38). The sim rounds fees where the real tape
rounds up, so our cost estimate (8) was 1 P above the sim's charge (7): conservative. The two-bid run caught a bug the
unit tests had missed. The one-card-one-bid pairing ran before the ring filter, so a resting team's cheaper ask took
the bid and left the eligible ask unpaired. Now the filters run first, with a regression test.

## Risks and open questions

- **Scoring per leg is unverified.** The buy leg of a held card usually scores negative on its own (a common at 1.6
  bought for 7: 4 − 7 = −3; resold for 16: +12). Its net (+9) holds only if the organisers don't clip trades one at a
  time. The scan flags crossings where a leg is negative (⚠). RULES.md says nothing about per-trade caps; only
  "ring flags" (an even split for a pair that keeps handing one side the whole pie) are documented, in the openapi.
- The exit sells even below the buy's cost (a fee raised in between) as long as it clears the sell floor: the cost
  is sunk, and the resale scores `proceeds − your_value`, which beats keeping a duplicate.
- **Who pays the fee on a bid accept** is verified on the tape only for ask accepts (the accepting side pays). For
  the exit (accepting a bid) we assume the same and charge ourselves the fee. If the bid's maker pays instead, our
  proceeds are higher: the assumption errs on the safe side.
- **Rate budget** (W5W6: burst NO-GO at the tick edge): with `arb_enabled` on, each arbitrage adds one board re-read before the buy and one before the exit, and a pending exit runs at the top of the tick (after the duel grace, up to 2 s). Weigh that before flipping.
- Duplicate buys don't see a copy that is accepted but not yet settled (bounded by `dup_max_spend_per_hour`).
- The exit uses the accept slot one tick after the buy; a duel that takes that slot delays it, up to 3 ticks.

## What Marius decides

1. Keep both switches off at 09:00 (recommended), and run `uv run bazaar arb scan` a few times on Saturday morning once
   the team venues trade.
2. If the scan shows repeated crossings of +3 P or more (or a rare at ≤ 25 in our top set): flip the matching switch,
   with `arb_max_inventory_p` and `dup_max_spend_per_hour` as the loss bounds (60 P and 40 P/h).
