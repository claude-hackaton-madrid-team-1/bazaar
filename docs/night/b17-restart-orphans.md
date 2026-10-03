# B17 · Dealer threads orphaned by a restart (bite X3, high)

**Verdict: GO, merge with #72.** A bug fix: no guardrail changes, no changed default. After a restart, the taker
books a deal struck on a thread of the process before it (once, even with two takers overlapping), and closes that
process's threads once they go quiet, instead of leaving them blocking a dealer and a thread slot. A thread the taker
never drove (a laptop `bazaar dealer buy`) is never touched. The one new write is the quiet close, and only for the
taker's own threads.

Stacked on `fix/cash-spend-accounting` (#72). B17 alone: `git diff origin/fix/cash-spend-accounting...night/b17-restart-orphans`.

## The bug (r2 X3, `tests/bites/test_bite_a1_restart_orphans.py`)

`Taker.convs` lives in memory. After a redeploy (every merge to main redeploys the Railway taker, r2 X16), the open
threads of the old process were only counted as busy: never read, never walked, and a deal the dealer struck on our
standing bid never reached `_finished`, so it was never booked as spend. The hourly cap undercounted and the dealer
stayed blocked until it idled the thread out (40 ticks, ~20 min at 30 s ticks).

## The fix (reworked after r1's review)

The taker **watches** its own dealer threads that no `Conversation` drives:
- on start, the threads the process before it drove (the decisions log: `agent = taker`, `thread_id`, live rows of
  the last 40 ticks, not yet wrapped up);
- every thread it walks from (the dealer may take our bid at the same boundary).

| Case | Before | Now |
|---|---|---|
| A watched thread leaves the open list (deal, idle, walked) | deal never booked | read once, newest first (3 per tick); a deal is booked at its settled price (fallback: our highest bid there), dated now (over-counts briefly, never under-counts) |
| The dealer takes the bid **after** the new process's first tick (r1 #1) | never booked | still watched while open, booked when it leaves the open list |
| Two takers overlap during a redeploy and both see the deal (r1 #2) | booked twice | booked once: whoever claims the thread's one `dealer_closed` decision books it (Postgres: partial unique index `decisions_thread_closed`, created by `schema.sql` when an agent opens the ledger; JSONL: a file lock) |
| 16 walked threads plus one recent deal (r1 #3) | deal read last or never | newest first: the deal is booked on the first tick; a thread is given up only after 5 refused reads of it |
| A watched thread stays open with no bid of ours for 3 ticks (`orphan_after_ticks`) | dealer blocked ~40 ticks | closed through `guardrails.check` (the kill switch holds it; quiet ticks under it do not count) |
| A thread the taker never drove (a laptop `bazaar dealer buy`, even paused) (r1 #4, #5) | left alone | left alone: never read, never closed |
| Dry run | — | nothing watched, read or closed |

The never-firing "adopt a stale standing bid" path of the first version is gone (r1 #4: real thread bids lapse 2
ticks after they are made). `TakerConfig`: `orphan_after_ticks = 3`, `restart_lookback_ticks = 40` (not guardrails).

## Evidence

- The two r2 bite tests flip (copied from `night/r2-bite-hunter` @ 51a9314 with their xfail marks removed): they fail
  on #72 (`ledger spend 0`, `orphan thread 40 never read nor closed in 3 ticks`) and pass here.
- `tests/test_taker_restart.py` (18 tests + 1 Postgres integration test): r1's three cases (a deal after the new
  process's first tick is booked; two overlapping takers book it once, 18 not 36; a deal behind 16 walked threads is
  booked on the first tick); a thread the taker never drove is never read or closed, even quiet for 8 ticks; a
  watched thread with fresh bids is left alone; the quiet close waits 3 ticks; the kill switch holds it and its ticks
  do not count; dry run touches nothing; a walked thread that dealt at the boundary is booked; no double booking
  across restarts; a thread that ended without a deal is read once; 3 reads per tick; a refused read is given up
  after 5 and the taker trades on; `decide_once` writes one closing row per thread; `thread_trails` filtering.
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
- The quiet close reads a bid's age from `created_tick` (present in every real offer in Friday's feed); an offer
  without it counts as fresh. The partial unique index is created by `schema.sql` on the next agent start; until it
  exists, two overlapping takers can still book a deal twice (over-count, as before).
- Closing a dealer thread is a walk the dealer remembers; the alternative (keep laddering with the playbook's plan)
  is a bigger change and was not needed for the bite.
- Not covered (follow-up): a thread of a crashed `bazaar dealer buy` that dealt before anyone saw it. The public
  `settlement` event (`persona`, `parties`, `price`, `tick`) would let a reconciler book any dealer deal of ours that
  has no spend row.
