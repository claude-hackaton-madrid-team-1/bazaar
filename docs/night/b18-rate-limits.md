# B18 · Rate limits and timeouts (bites X20 high, X6 medium-high, X2 medium)

**Verdict: GO, merge with #72 and B17.** These are bug fixes: no guardrail changes and no new parameters. A refused accept
no longer burns the team's one accept of the tick. The team client never re-sends a refused call or a write. One
hung read no longer holds a loop for three Sunday ticks.

Stacked on B17 (#114), which is stacked on #72. B18 alone: `git diff origin/night/b17-restart-orphans...night/b18-rate-limits`.

## Before → after (r2's tests, `tests/bites/test_c1_request_budget.py`, xfail marks removed)

| Bite | Measured on #72 | Here |
|---|---|---|
| X20: the taker's accept is refused `429 rate_limited` | the slot stays reserved (`ledger accept items: ['LAV-08']`); no other candidate and no duel can use the team's accept this tick | reservation released (`[]`); no more accepts tried this tick (a duel or the next tick may use it) |
| X20: the accept is refused `offer_closed` / `insufficient_cash` / any other 4xx | slot burned | released, and the **next candidate is accepted in the same tick** |
| X20: the duel loop's accept is refused | slot burned | released (same codes) |
| X6e: another process wins the reservation race | one keyed `GET /api/clock` per proposal (3 in the test) | the accept loop stops: **1** clock read |
| X6c: the SDK re-sends a `429`-refused call | GET ×3, POST ×3 | **GET ×1, POST ×1** |
| X2: one hung keyed read | 15 s timeout × 3 attempts = **46.5 s** (3 Sunday ticks) | 4 s per attempt: **13.5 s** at 30 s ticks (2 network retries), **4 s** at ≤ 15 s ticks (no retry) |
| one hung keyed write | 15 s | 4 s, never re-sent |

## What changed

- `Recorder.refusal`: the last send's refusal code. `KEEPS_THE_ACCEPT = (wait_for_tick, network, bad_response)`.
  `wait_for_tick` means the team's accept of this tick is already used. With `network` and `bad_response` the
  accept may have landed. Every other refusal "costs nothing and moves nothing" (RULES.md), so its ledger
  reservation is released.
- `LedgerStore.release_accept(tick, item)`: the JSONL file appends a `release` row (it stays append-only) that
  `accept_items` / `accepts_in_tick` / `reserve_accept` subtract under the same file lock. Postgres deletes the
  newest matching reservation row (`kind` keeps its `spend|accept|listing` check; no schema change).
- Taker `_accept`: after a refused accept, the next candidate may take the freed slot. After a rate limit or a lost
  reservation race, no more accepts are tried this tick (each try costs a keyed clock read).
- Duel loop (`bazaar duel run`): a refused accept releases `duel:<id>`. This is the only edit there, kept small
  because B15 owns that loop. If the ledger is unreachable, the slot stays taken (fail closed).
- `sdk.team_client` returns `TeamBazaar`, a subclass of the vendored `Bazaar` (the vendored file is untouched):
  4 s per attempt, no re-send of any refused call (a `429` included) or of a write. A GET is re-sent only after a
  network error, and only while the last `/api/clock` answer says ticks are slower than 15 s. The public
  (keyless) client keeps the SDK's retries.

## Evidence

- r2's four xfails flip and the two safety checks still pass: a hung read never sends late; the taker stays
  within #78's ceiling of 16 keyed calls per tick.
- `tests/test_accept_release.py` (18 tests + 1 Postgres integration): file ledger release and re-reserve, a stray
  release is a no-op and no spend; `offer_closed` / `insufficient_cash` / `not_found` give the slot to the next
  candidate; `rate_limited` frees it and stops; `wait_for_tick` / `network` / `bad_response` keep it; a refused
  accept books no spend; the duel loop releases on `rate_limited` / `duel_closed` and keeps on `network`;
  `TeamBazaar`'s retries on slow vs fast ticks, writes and 429s sent once, the public client unchanged.
- Gates: `pytest` 931 passed, `ruff`, `black --check`, `mypy src` clean.

## Risks and what Marius must decide

- Fewer retries means more errors reach the loops. Each loop already treats a refused read as "nothing sent this
  tick" and decides again on the next tick (the taker logs `read refused ... nothing sent`). A transient blip on a
  15 s tick costs that tick's moves instead of 46 s of stall.
- A 4 s timeout on a write means a slow but successful accept is reported as `network`. It is booked as maybe
  landed (spend counted, slot kept), the same fail-safe path as today, just reached sooner.
- Postgres release with `accepts_per_team_per_tick` > 1: deleting slot 1 while slot 2 is held makes the next
  reservation collide on slot 2 and be refused. The limit is 1 today; revisit if the organisers raise it.
- Not covered: `bazaar dealer buy` (`negotiate()`) keeps its reservation on a refused accept (needs a release
  callback; follow-up). The keyless public client can still hang 31.5 s on one read (10 s × 3); worth the same
  treatment if Sunday shows hangs.
