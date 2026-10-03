# B16 · Unsettled accepts count as held (bite X18, medium)

**Verdict: GO, merge with the stack (#72 → B17 → B18 → B14 → B16).** A bug fix that needs no new parameter and
changes no guardrail. An accept from the last two ticks that `/api/me` does not show yet now counts as held, and its
cash as spent. So a read that lands before settlement can no longer buy a second copy or slip under the cash floor.

Stacked on B14 (#126). B16 alone: `git diff origin/night/b14-expired-bids...night/b16-unsettled-accepts`.

## The bug (r2 X18, `tests/bites/test_taker_unsettled_duplicate.py`)

The guard context took held cards and cash from `/api/me` alone. An accept settles on the next tick. If that tick's
`/api/me` read lands before the server settles (r2 modelled this; the real timing is unverified), the card still
looks missing and the cash still looks full. The taker then accepts a second copy from another seller: a duplicate
worth 0.25× to us, bought at full price. The shared ledger already holds the accept row for that card.

## The fix

- `LedgerStore.accept_rows(tick)` returns the `(item, price)` of a tick's accepts. Accepts released after a refusal
  (B18) are left out: the JSONL ledger subtracts its release rows, and Postgres deleted the row.
- `seller.unsettled_accepts(me, ledger, tick)` collects the team's accepts from the last 2 ticks: from any process
  (taker, `bazaar dealer buy`, a second machine), whose card or pack `/api/me` does not show yet. It returns them as
  `Commitments`, the same shape as an open offer: the card counts as held and its cash as gone.
  - A copy `/api/me` already shows counts as settled and is counted once.
  - Duel accepts move no card and are skipped.
  - An accept still missing after 2 ticks never landed, so it stops counting.
- The taker reads it once per tick (2 ledger reads) and adds it to every guard context. The maker adds it to its base
  context; without it, the maker would post a bid for the card the taker has just taken.

## Evidence

- r2's bite test flips: `bought LAV-08 twice` on #72 → bought once.
- `tests/test_unsettled_accepts.py` (9 tests + 1 Postgres integration):
  - what `/me` lacks is counted (card and pack); what it shows is settled and counted once;
  - duel rows and released accepts do not count;
  - another process's accept from last tick blocks the duplicate;
  - after 2 ticks without the card the accept stops counting;
  - an unsettled 120 P accept counts against the 270 floor, and a settled one is not counted twice;
  - the taker does not buy its own last-tick card twice;
  - the maker does not bid for a card the taker took last tick.
- No simulator number. The simulator settles every accept at the tick boundary, so the bite needs a modelled lag.
  I ran the real taker and maker with every `/api/me` answer one tick stale (start cash 400). The one completed seed
  bought no duplicate and its lowest cash was 271 (floor 270). The other seeds died on local port exhaustion
  (`Errno 49`, both before and after the fix) and are not reported. The targeted r2 test is the evidence: two
  copies of LAV-08 listed, `/api/me` read before settlement, bought twice on #72 and once here.

## Risks and what Marius must decide

- In the 1–2 ticks after a buy, the cash floor and the duplicate rule are stricter by that buy's price and card,
  until `/api/me` shows it. That is fail-safe, and it lasts at most 2 ticks.
- An accept whose card arrives and is sold again within the same 2 ticks would be counted as unsettled (cash
  over-counted for one tick). This is rare and fail-safe.
- The spend cap is unchanged: board accepts were already booked at accept time. A dealer-thread accept is booked when
  its deal shows; until then `_commit` counts it inside the tick.
