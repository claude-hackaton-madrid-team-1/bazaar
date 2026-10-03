# B19: price the announced fee (bite X8)

**Verdict: GO.** This is a pure bug fix with no new parameter. It only ever makes the taker and maker more cautious
for the 1–2 ticks before an announced fee hike takes effect.

## The bug
`/api/venues` carries `pending_fee: {fee_bps, fee_per_card, effective_tick}` once an owner announces a fee
change. `venues_from` dropped it, so the taker priced an ask at today's fee. An accept made at T settles at T+1. If
the change takes effect at T+1 and the server charges the fee in force at settlement, we pay more than we priced
and can go past `max_price_*`. Example: a common LAV-02 ask at 10 P on a 0-fee venue that announced the capped
fee (10 % + 5 P) priced at 10 ≤ `max_price_common` 12 and was approved. It would settle at 16.

## The fix
- `agents/market.py`: `Venue.pending_fee = (fee_bps, fee_per_card)` is kept when `effective_tick ≤ tick + 1`, or
  always when no tick is given (the conservative side). `Venue.fee()` returns the **higher** of today's fee and the
  announced one, because which of the two the server charges at settlement is unverified (see Risks). An announced
  cut is never priced in early. A `pending_fee` that can't be read is ignored, which is today's behaviour.
- `agents/runtime.py`: `read_snapshot` passes `clock.tick`, so the taker (`ask_candidates`) and the maker
  (`best_venue`, the log line) both see it.

| ask price | overpay if a capped hike (1000 bps + 5 P) applies at settlement | Friday's v03 hike (100 bps) |
|---|---|---|
| 10 P | 6 P | 1 P |
| 30 P | 8 P | 1 P |
| 65 P | 12 P | 1 P |
| 100 P | 15 P | 1 P |

With the fix, the 10 P case totals 16 > `max_price_common` 12, and the taker no longer proposes it.

## Evidence from the Friday capture (`stream.jsonl`, ticks 0–149)
- Two fee announcements, both on v03 (t13) with **2 ticks' notice**: 100 → 0 bps (announced at 134, effective
  at 136), then 0 → 100 bps (announced at 145, effective at 147). That is the bait shape (cut, then hike), just
  small.
- **0 settlements on team venues** on Friday (140 dealer settlements, 44 on El Rastro). So the real server's
  order (old or new fee at the settlement tick) **cannot be checked from data**. The sim charges the OLD fee
  (`settle_due` runs before `venue_tick`). Taking the max is right under either order.

## Tests (each regression test fails on main and passes here)
- `tests/bites/test_c2_fee_at_settlement.py`: r2's file at its current head with the three X8 xfails dropped:
  `venues_from` keeps the fee, the taker totals 16, and the taker dry run no longer approves the accept. All 3
  fail on main with assertion errors.
- `tests/test_agents_market.py`: 5 new tests.
  - Fail on main (main's `venues_from` given a no-op `tick` argument so the failures are assertions, not
    TypeErrors): effective by settlement → priced in; effective later → not yet; no tick → priced in.
  - Guards that main passes trivially: an announced cut never lowers today's fee; an unreadable `pending_fee` is
    ignored.
- Gates: 838 passed / 34 skipped / 1 xfailed (X8b, below); ruff, black and mypy are clean.

## Not done: the sim's fee rounding (X8b, optional)
The sim rounds fees half-to-even, while the tape rounds up (El Rastro charged 5 on 65 P, and `round` gives 4). I
tried changing `market.venue_fee` to round up. It broke a broker test, because the sim has **four more** `round()`
fee formulas in `bazaar_sim/broker.py` (brokered, auto and bench matches). Fixing them all would shift every
sim-calibrated bench number in the night PRs (#77, B1, B2) by ≤ 1 P per trade, and a merge redeploys the shared
Railway sim. That is out of scope for a small fix, so it is reverted and X8b stays an xfail. It is worth a separate
item if bench numbers need to match the tape to the P.

## Risks / for Marius
- **The `tick + 1` window assumes our accept lands in the tick we read** (r1, low). An accept that slips into the
  next tick would settle at T+2. Using `effective_tick ≤ tick + 2` is one more tick of caution. I'm leaving it
  out: the taker drops a decision once the tick's action budget is spent (`action_budget_s`).
- **Cost of being conservative:** while a hike is pending (1–2 ticks per announcement), we price that venue's
  asks at the higher fee and may skip a fill that would have settled at the old fee. On Friday this would have
  cost ≤ 1 P per trade.
- **Maker:** `best_venue` sees a pending hike only once it is effective by T+1, even though our offers live up
  to 40 ticks. No money is at risk (the accepting side pays the fee), only a tick or two of venue scoring. #111 covers the
  maker's longer horizon.
- **Cross-PR:** #101 builds `Venue(...)` positionally (8 args), which is safe because the new field has a default
  and comes last. The read-only CLIs in #79, #98 and #101 call `venues_from` without a tick, so they show prices
  with any pending hike included. No overlap with #72; #71 only logs `pending_fee`.
- **Stack:** this PR is based on `night/b13-wake-opening` (#106). On its own it touches only
  `agents/market.py`, `agents/runtime.py` and tests.
- **Merge note:** `tests/bites/test_c2_fee_at_settlement.py` and `tests/bites/strictness.py` are copies of r2's
  files (`strictness.py` is byte-identical). If `night/r2-bite-hunter` is merged too, keep this version of the c2
  file.

## Takeover review fixes (#144, 2026-10-03)

pr-reviewer APPROVED; security-auditor had no P0/P1. The cheap P2/P3s are in:

- **`effective_tick ≤ tick + 2`** (`SETTLE_SLACK_TICKS`): an accept that slips into the next tick settles a tick
  later, and Friday's announcements came with exactly 2 ticks' notice. One more tick of caution per announcement.
- **A rival's unreadable announcement is priced at the RULES cap** (10 % + 5 P), never ignored; an unreadable
  `effective_tick` counts as unknown (priced in); a missing `fee_per_card` keeps today's; values are clamped to
  the cap.
- **A venue row we cannot read is skipped** (inf, huge ints, odd types), instead of raising out of `venues_from`
  and costing the taker's and maker's tick.
