# B15: duels first, really (r2 bite X17)

**Goal.** The team has one accept per tick (RULES.md:109), shared through the ledger, "duels first". The taker
waits `duel_grace_s` (2 s, capped at 15 % of the tick) and then takes the slot unless a `duel:` row is booked.
The v1 duel loop booked and sent its accept only after `DuelJev.pick` returned, and `pick` waits for every Jev
question of the tick (3 s each, bounded only by the tick budget). On a slow Jev tick the taker could take the
slot. On a duel's deadline tick that loses the deal, and the duel scores 0.

**Base.** The top of the duel chain: `night/b8-days-wiring`. The order is main ← #60 ← #86 (v2) ← #103 ← #113
← this PR, and w2a's B27 puts B7 after it. I first built it on #72, as the backlog asked; I moved it here
on w2a's B27 request, and the #72 version is kept as the local branch `b15-on-72-backup`. No taker code
changes, so #72 is not needed. v2 already books its planner's accept before Jev (4417c54). This PR adds the
same guarantee for v1, which is the default policy, and goes one step further: the accept is also *sent*
before Jev.

## What changed

| file | change |
|---|---|
| `agents/duel_jev.py` | `forced_pick(duel, tick, start, anchor, floor, endgame_ticks) -> DuelPick \| None` returns the exact `DuelPick` that `DuelJev.pick` makes ("jev not asked", with its state) when today's v1 move is an accept AND the duel's only legal move (an inside-limit offer within `duel_endgame_ticks`). |
| `cli.py` duel `on_tick` | **v1 only** (`params is None`; v2 keeps its planner's booking untouched). After the v2 booking block and before the Jev call, every forced accept goes through the unchanged `play_one` (guardrails with `rules_t`, `reserve_accept`, send, decision row), nearest deadline first. The main loop then skips those duels. `pick` still sees every duel, so outcome calibration stays complete. With `--no-jev`, rows carry no Jev context, as before. A row that makes `forced_pick` raise is logged and goes the usual way. `play_one`'s definition moved above the Jev call; its body changed only in the forced branch. |
| `taker.py` | **No change.** The alternative fix, "the taker skips while a duel is in its endgame", needs a duel store or an extra `/api/duels` read per tick, which the Sunday request budget can't afford (X6: 70 of 75). |
| tests | 6 duel-loop CLI tests (one fails a forced accept closed when the ledger is down), 4 `forced_pick` tests, and r2's bite test **replaced** by a measured invariant (`tests/bites/test_duel_accept_priority.py`). One `forced_pick` test asserts equality with `pick()` over 4 Jev verdicts × 4 deadlines × 5 prices; another replays the exposure split below on the fixture. |

No GUARDRAILS.md values and no new parameters. This is a pure bug fix in the order of existing steps.

## Evidence

**The regression tests fail on the chain's own `cli.py` (`night/b8-days-wiring` @ 053e30c, which carries #103's ledger fail-closed 9239070 and r1's 52a590a) and pass here:**

| test | before | after |
|---|---|---|
| `test_a_forced_endgame_accept_is_booked_and_sent_before_jev_is_asked` | order `jev, reserve, accept`: FAIL | `reserve, accept, jev`, Jev not asked, row keeps `legal_moves: [accept]` |
| `test_the_taker_claiming_the_accept_while_jev_thinks_no_longer_costs_the_deadline_deal` (the taker reserves while Jev thinks about a second duel) | the deadline duel sends nothing: FAIL | accept sent, ledger `[duel:95]`, the taker is refused |
| `test_with_one_accept_for_two_forced_duels_the_nearest_deadline_goes_first` | API order, the deadline duel loses: FAIL | the duel ending this tick wins |
| `test_an_accept_jev_may_still_overrule_is_booked_after_jev`, `…slot_taken…`, `…without_jev…` | pass | pass (unchanged behaviour) |

r1 (REVIEWS.md, PR #115 @ 847a135) re-proved the fix independently with a real-time threaded race: 4/4 on head,
fails on base.

**Timing invariant** (`tests/bites/test_duel_accept_priority.py`). The taker's grace is measured through
`Taker.on_tick` with the tick just landed.

The duel loop books by:
- settle 0.3 s
- loop clock read 0.2 s
- duels read 0.2 s
- **6 ledger round trips**: `accepts_in_tick`, then `reserve_accept` = BEGIN, advisory lock, count, insert,
  COMMIT. Counted on a throwaway local Postgres statement log.

The round trip was measured read-only, laptop → shared Postgres `select 1`: p50 42 ms, p90 44 ms, max 108 ms.
Locally, `reserve_accept` p50 is 3.8 ms and p99 11.5 ms. Inside Railway the round trip is unmeasured and lower.

| tick | taker grace (measured) | duel books by, before | after (laptop worst case) | margin |
|---|---|---|---|---|
| 60 s / 30 s / 15 s | 2.00 s | ≥ 3.95 s (Jev) | 0.95 s | 1.05 s |
| 10 s | 1.50 s | ≥ 3.95 s | 0.95 s | 0.55 s |
| 5 s (fastest pace in the rules) | 0.75 s | ≥ 3.95 s | 0.95 s | **negative: still a race** (strict xfail) |

The taker's own reads before its first accept usually take another 1–3 s, so the grace is a floor.

**Exposure on real data.** I replayed v1 at this base on the 26 practice payloads (`duels_done.json`),
treating the rival's recorded moves as unilateral, starting at the first message (12 ticks before the deadline
for a silent duel), with endgame 2. It is reproduced by
`tests/test_duel_jev.py::test_on_the_real_practice_payloads_v1_ends_seven_duels_on_a_forced_accept`. #60's
limit handling makes it 7/11/8 here; it was 6/12/8 on #72.

| v1 outcome | duels | before → after |
|---|---|---|
| forced endgame accept (all first reached at D-2) | 7 | the slot could go to the taker on each of D-2, D-1 and D → **booked and sent before Jev** |
| accept that Jev may overrule | 11 | unchanged: still books after Jev. If the slot is lost, nothing is sent, no round is spent, and the duel retries next tick. In the endgame it becomes forced. |
| never accepts | 8 | n/a |

Gates: ruff, black and mypy are clean; pytest 802 passed, 1 xfailed (the 5 s residual).

**Ledger outage:** the forced pass keeps the chain's fail-closed rule. Each forced duel is played inside the
per-duel `try`, and its guard reads the ledger on `rules_t`. `test_a_ledger_outage_fails_a_forced_accept_closed`
shows the result: skipped this tick, nothing sent.

## Verdict

**GO** for v1, low risk. Only the order changes, and only for a v1 move that `pick` can never change.

## Risks and limits

- **Residual race:** the taker can still win if the clock and duels reads plus six ledger round trips run past
  the grace. From a laptop that leaves 1.05 s of slack at ≥ 15 s ticks and none at 5 s ticks; the 5 s case is a
  strict xfail. Today's pace is 60/30/15 s. A floor on the taker's grace (e.g. 1 s) would close the 5 s case;
  that is a taker change, not made here.
- **A refused forced accept keeps its slot** (the rival withdrew, a 429). That is the existing
  reserve-then-send behaviour. #116 (night-hard-taker, B18) adds the release, and r1 checked that B15 + #116
  merge clean (945 pass).
- **The forced send now runs before Jev and before every other duel.** A hung POST (the SDK's 15 s timeout)
  would cost the other duels their move this tick; #116's 4 s timeouts mostly cover that. It also shrinks
  Jev's budget for the other duels by about 0.5 s.
- **Non-forced v1 accepts still book after Jev.** There is no ledger release on this base. With #116's release
  this could extend.
- **v2 is unchanged.** Its planner's accept is still booked before Jev and *sent* after. A hung Jev call can
  strand that slot; w2b's B7 addresses it.
- **Cross-PR notes:**
  - #107 has an add/add on `tests/bites/test_duel_accept_priority.py`; keep this one (r1).
  - On `night/r2-bite-hunter` the same file is r2's xfail original; keep this one.
  - **B7 (#130, w2b)** removes #86's `booked`/`fresh` and plays v2's planned accepts early (`early`, `done`,
    `play_safely`) at the same spot, so stacking B7 on #115 conflicts there. Resolution agreed with w2b:
    - one early pass: `early` = v2's planned accepts when `params is not None`, else this PR's v1 forced picks;
      either way nearest deadline first, through `play_safely`;
    - keep `play_one`'s `if did in forced` branch and add the forced ids to `done`;
    - one skip set in the main loop; the v1 gate (`params is None`) stays as is.

## For Marius

- Merge order (w2a's B27): #60 → #86 → #103 → #113 → **#115** → B7.
- Decide whether to extend send-before-Jev to non-forced accepts once #116's release path is in.
