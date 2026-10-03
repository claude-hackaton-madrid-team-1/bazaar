# B17 · Dealer threads orphaned by a restart (bite X3, high)

**Verdict: GO, merge with #72.** A pure bug fix, no new guardrail and no changed default. After a restart the taker
now books a deal struck on a thread of the process before it, and adopts or closes the old process's open dealer
threads instead of leaving them blocking a dealer and a thread slot. A thread a live `bazaar dealer buy` is driving is
never touched.

Stacked on `fix/cash-spend-accounting` (#72). B17 alone: `git diff origin/fix/cash-spend-accounting...night/b17-restart-orphans`.

## The bug (r2 X3, `tests/bites/test_bite_a1_restart_orphans.py`)

`Taker.convs` lives in memory. After a redeploy (every merge to main redeploys the Railway taker, r2 X16), the open
threads of the old process were only counted as busy: never read, never walked, and a deal the dealer struck on our
standing bid never reached `_finished`, so it was never booked as spend. The hourly cap undercounted and the dealer
stayed blocked until it idled the thread out (40 ticks, ~20 min at 30 s ticks).

## The fix

| Case after a restart | Before | Now |
|---|---|---|
| The dealer took our standing bid before the new process saw the thread | never booked | the new process finds the thread in the decisions log (`agent = taker`, `thread_id`, live rows of the last 40 ticks), reads it once, books the deal at its settled price (fallback: our highest bid there), dated now |
| Our old bid still stands in an open thread, nobody bid there for 3 ticks | dealer blocked, slot used | adopted with that bid as its whole plan (start = max): read every tick, a deal on it is booked by `_finished`, and it walks on its next move |
| Open thread, no bid of ours standing (real thread bids expire 2 ticks after they are made) | dealer blocked, slot used | closed after 3 quiet ticks (`orphan_after_ticks`), through `guardrails.check` (the kill switch holds it) |
| A live `bazaar dealer buy` on a laptop drives the thread (bids every tick) | left alone | left alone: its bid is always fresher than 3 ticks |
| Kill switch on | — | nothing is closed, and quiet ticks under the switch do not count (a held `dealer buy` sends nothing either) |
| Dry run | — | nothing adopted, read or closed |

Every wrapped-up thread now writes one `dealer_closed` decision, so the next restart does not book its deal twice.
`DecisionLog.thread_trails(agent, since_tick)` reads Postgres and this machine's JSONL (a write falls back to the
file while Postgres is down). The restart wrap-up reads at most `max_dealer_threads` threads per tick and retries a
refused read for 5 ticks; a refused read never stops the taker's tick.

Two new `TakerConfig` fields (not guardrails): `orphan_after_ticks = 3`, `restart_lookback_ticks = 40`.

## Evidence

- The two r2 bite tests flip (copied from `night/r2-bite-hunter` @ 51a9314 with their xfail marks removed): they fail
  on #72 (`ledger spend 0`, `orphan thread 40 never read nor closed in 3 ticks`) and pass here.
- `tests/test_taker_restart.py` (12 tests + 1 Postgres integration test): the live `dealer buy` thread is never
  read or closed over 6 ticks; the quiet close waits 3 ticks and restarts its count after a fresh bid; the kill switch
  holds it and its ticks do not count; dry run touches nothing; a deal booked by the old process (or by an adopted
  thread) is not booked again after one more restart; a thread that ended without a deal books 0 and is read once;
  restart reads are bounded to 3 per tick; a refused read is retried 5 ticks and the taker keeps trading;
  `thread_trails` skips dry-run rows, other agents, update rows, bad lines and rows before the lookback.
- Chaos run (real taker live over HTTP against the in-process simulator, 30 s ticks, 6 rivals, 240 ticks × 5 seeds,
  restart = a fresh `Taker` on the same ledger and decision log):

| restart every | restarts | dealer deals (P paid) | **unbooked deals (P)** #72 → B17 | orphan thread-ticks #72 → B17 |
|---:|---:|---:|---:|---:|
| never | 0 | 69 (1,109) | 0 → 0 | 0 → 0 |
| 10 ticks (5 min) | 115 | 65–66 (~1,055) | **1 (21 P) → 0** | 174 → 20 |
| 5 ticks | 235 | 66–68 (~1,075) | **17 (276 P) → 0** | 225 → 144 |
| 3 ticks | 395 | 58–59 (~985) | **26 (429 P) → 0** | 347 → 335 |

"Unbooked" = a dealer settlement of ours in the simulator's feed with no matching spend row in the ledger (what
`max_spend_per_game_hour` would miss). "Orphan thread-ticks" = per tick, our open dealer threads that no live
`Taker` owns. With restarts every 3 ticks the new process never sees 3 quiet ticks, so orphans stay open until the
dealer idles them out; their deals are still booked. Script: `docs/night/b17_chaos.py` (base run with
`PYTHONPATH` on a checkout of #72).

## Risks and what Marius must decide

- **Deploying it over a live pre-B17 taker:** the old code wrote no `dealer_closed` rows, so the FIRST restart onto
  this code books once more the deals of the last 40 ticks that the old process already booked (over-count, fail
  safe, gone after one game hour). Deploy it before the taker goes live, or accept a short over-count.
- Postgres down at restart: the new process remembers nothing from the old one (the JSONL on a fresh Railway
  container is empty): a deal struck while no process watched is still not booked (today's behaviour). Adoption and
  closes still work (they read the live `/api/me/threads` and `/api/me/offers`).
- Adoption reads the bid age from `created_tick` of our offers (present in every real offer in Friday's feed). An
  offer without `created_tick` is treated as fresh: never adopted.
- Closing a dealer thread is a walk the dealer remembers; the alternative (keep laddering with the playbook's plan)
  is a bigger change and was not needed for the bite.
- Not covered (follow-up): a thread of a crashed `bazaar dealer buy` that dealt before anyone saw it. The public
  `settlement` event (`persona`, `parties`, `price`, `tick`) would let a reconciler book any dealer deal of ours that
  has no spend row.

## Takeover review fixes (#140, 2026-10-03)

pr-reviewer and security-auditor found two ways a deal still went unbooked, and a hijack risk. Fixed:

- **Only the taker's own threads are touched.** A thread is the taker's when its decisions log names it (every
  open now writes a `dealer_opened` row with the thread id). A thread no taker decision names belongs to a
  `bazaar dealer buy` or the desk and is never read, adopted or closed, even when it goes quiet (a laptop paused
  by its own `.local/PAUSE` keeps its thread).
- **The old taker's open threads are adopted on sight**, whatever the bid's age: a "Deal!" that lands a tick
  after the new process started is booked by `_finished`. A fresh bid gets the usual `MAX_WAITS` for her answer;
  a stale one walks on its next move. A thread of ours with no price of ours is closed after 3 quiet ticks.
- **Every walk and orphan close writes `dealer_closed`**, and the wrap-up reads newest first with no overall
  cap (each thread is tried at most 5 times; a rate limit ends the tick's reads; an unreadable body is skipped,
  never stalls the tick).
- **The first deploy books nothing twice.** Each live taker writes a `process_started` row first. Only threads
  with a decision at or after the earliest one are read and booked: the process before the first such start
  booked its deals without a `dealer_closed` row. The old risk row ("the first restart books the last 40 ticks
  again") is gone.
- The chaos table above was measured before these fixes, with restarts only at tick boundaries and dealers that
  answer at the boundary; it cannot show the late-"Deal!" case (`test_a_deal_that_lands_after_the_new_process_first_tick_is_booked`).
