# B14 · Expired maker bids booked as spend again (bite X15, high)

**Verdict: GO, merge with the stack (#72 → B17 → B18 → B14).** This is a bug fix: no guardrail changes and no new
parameters. A board bid that lapses unfilled now gives its spend back, dated at the spend. Each standing bid counts
once per hour, no longer once per TTL. The hourly cap stops filling up with phantom spend and blocking the taker.

Stacked on B18 (#116). B14 alone: `git diff origin/night/b18-rate-limits...night/b14-expired-bids`.

## The bug (r2 X15, `tests/bites/test_maker_expired_bid_spend.py`)

A maker bid's cash is booked as spend when it is posted, because it can fill on any later tick. Until now only a
cancel refunded it. A bid that lapsed at `offer_ttl_ticks` (40) simply left `/api/me/offers`. The maker then
reposted the target and booked the same cash again. On Sunday's 15 s ticks, one 65 P bid counted 130 P after two
TTLs, and the third repost was refused (`spend 130 + 65 > max_spend_per_game_hour 150`). The cap is team-wide, so
the taker's board accepts and dealer bids were refused too.

## The fix

| A board bid of ours leaves `/api/me/offers` … | Before | Now |
|---|---|---|
| before its `expires_tick` (it filled, or the taker's `_withdraw` / `bazaar flatten` cancelled it and booked the refund) | spend kept | spend kept |
| at or after its `expires_tick`, the card arrived (`/api/me`), or a settlement of it to us is in the feed | spend kept | spend kept (a fill) |
| at or after its `expires_tick`, no card, no settlement | **spend kept, repost booked again** | checked again on the next tick (an accept settles on the next tick), then **refunded, dated at its spend** |
| the maker's own cancel (reprice, no longer a target) of a bid it posted | refund dated by `refund_row` at the slowest pace (60 s/tick) | refund dated exactly at its spend |
| a bid posted by the process before a restart | n/a | refund at `refund_row`'s conservative date (never after the spend) |

- The maker remembers its board bids: the ones it posts, and the ones it sees open in `/api/me/offers`. This memory
  is lost on a restart; then no refund is booked, which over-counts as before.
- A refund dated at the spend row's own `(tick, t_hours)` leaves the hour's window together with the spend. This
  also fixes r1's medium on #72 for the maker's cancels: `refund_row` dates at 60 s/tick, so a bid cancelled more
  than ~30 min (30 s ticks) or ~15 min (15 s ticks) after it was posted got a refund dated outside the hour while
  its spend still counted.
- It is bookkeeping only, so it also runs while the kill switch holds. A dry run books nothing.

## Evidence

- r2's two bite tests flip: "the bid target stays on the board all hour" (5 posts in 50 min on Sunday, was 2) and
  "one standing bid counts once in the hour". The second one is adapted: it now checks the hour after the one-tick
  confirmation and bounds the transient at 130 (the new bid plus the lapse being confirmed). On #72 it ends at 130,
  with the third repost refused.
- `tests/test_maker_lapsed_bids.py` (11 tests). A bid seen gone is refunded one tick later, dated at its spend. No
  refund when it left before its expiry, when its card arrived (now or on the confirming tick), when a settlement of
  it is in the feed, or when it is listed again on the confirming tick. With no card and no settlement it is
  refunded. A bid from before a restart gets the conservative date. The kill switch does not stop the bookkeeping,
  and a dry run books nothing. The maker's own cancel at 15 s ticks is refunded at the spend, and only once.
- **The proof**: `docs/night/b14_maker_probe.py` runs the real maker alone, live against the in-process simulator
  with no rivals, so nothing fills its bids and they lapse. Start cash 900, 240 ticks, 3 seeds per pace, identical
  per seed:

| pace | | bids posted | **bid-ticks on the board** | lapses refunded | worst hour booked | most really committed at once | cap denials |
|---|---|---:|---:|---:|---:|---:|---:|
| 30 s | #72 | 4 | **164** | 0 | 140 | 140 | 316–556 |
| 30 s | B14 | 12 | **470** | 10 | 140 | 140 | 10–250 |
| 15 s | #72 | 2 | **82** | 0 | 140 | 140 | 398–638 |
| 15 s | B14 | 12 | **470** | 10 | 140 | 140 | 10–250 |

  (Seed 3 has one bid target: 6 posts / 240 bid-ticks with the fix vs 4 / 164 and 2 / 82 on #72.) On #72 the two
  70 P bids lapse at tick ~41. Their 140 P stays booked, so the reposts are denied (`140 + 70 > 150`), and the
  board has no bid of ours for the rest of the hour: 35 % of the run at 30 s, 17 % at 15 s. With the fix every
  lapse is refunded at tick E+2 (E = `expires_tick`) and reposted in the same tick. The ledger's worst hour never
  goes above the 140 P really committed. The remaining denials are the correct ones: a third 70 P target on top
  of 140 committed.
- **No regression**: `docs/night/b14_chaos.py` runs the real taker and maker together against the simulator with 6
  rivals (seeds 1, 2, 3, 7; 30 s and 15 s; and one world without rivals). Every bid filled before it lapsed, so the
  fix had nothing to do, and the result is identical to #72 row for row (paid = booked, same worst hours, same
  denials).

## Risks and what Marius must decide

- One tick of over-count per lapse, while the lapse is being confirmed. It is fail safe and keeps the repost going.
- If an operator runs `bazaar flatten` (or anything else cancels a bid without the card coming) in the bid's last
  two ticks, both flatten and the maker may refund it. That under-counts one bid for up to one hour. It is rare:
  2 ticks in 40, and only for an operator flatten.
- The real server's expiry tick (gone at `expires_tick` or one later) and whether it lists accepted offers until they
  settle are unverified; Friday's feed has no expiry events. Either way the one-tick confirmation and the card / feed
  checks keep the spend when in doubt.
- The stopgap from the backlog (`offer_ttl_ticks` ≈ one game hour) is not needed with this fix.

## Takeover review fixes (#142, 2026-10-03)

Both reviewers reproduced a double refund: `bazaar flatten` (the runbook: PAUSE, then flatten), the desk's
`sell_cancel` or a cancel in a bid's last tick or two books its refund, and the maker then saw the bid gone at
or after its expiry and refunded it again (the hour read −65 while nothing was committed). Fixed:

- A bid whose cancel shows in the feed (`offer.cancelled` with its offer id; the live server emits none for an
  expiry, the simulator marks one `reason: "expired"`) is never refunded by the lapse check.
- Under the kill switch a bid seen gone is not refunded at all: a true lapse then over-counts (fail safe).
- The maker's own cancel forgets the bid only after `_refunded`, so the tick's estimate uses the exact date.
- A settlement event with an odd tick, or an expiry tick that is not a number, no longer stops the maker's tick.

Left as is (reply on the PR): two live makers on one ledger would each refund a lapse (only one maker runs);
`/api/me/offers` is trusted to list only our offers.
