# LF1 implementation and live audit

Scope: [LF1-spec.md](LF1-spec.md). Presentation work remains withdrawn.

| Acceptance | Status | Evidence |
|---|---|---|
| Budget-aware dealer planning | Verified | `plan_dealer_buy` caps ordinary ladders and final prices to remaining room, skips known unaffordable negotiated fills, and preserves unknown opening-price concessions. Existing fresh send guards remain. |
| Budget regression and recovery | Verified | Independent `uv run pytest -q tests/test_dealer_plan.py tests/test_taker.py tests/test_taker_hard_dealers.py`: `79 passed in 0.61s`. Includes 232/250 spend, affordable alternative, pending cash and expired-spend recovery. Refund tests add 53 focused and 5 PostgreSQL passes; independent review ran 62 tests, including concurrent one-credit accounting. No duel policy changed. |
| Current reports and operating guidance | Verified | `SR1-report.md` records backend `56ec5b68` and Live `256c3738` deployment, schema readback and resumed writers. Points guidance uses 60 P human approval and no sole-page-copy exceptions. Context, briefing and spec replace the obsolete Sunday opening anchor. |
| Live score and voice evidence | Verified | Tick 1539: rank 9, score 23.63, cash 561, 40 cards and 3 complete pages. Voice: two `200 audio/mpeg` responses, narrator 4.08 s and Jev 2.8 s, both decoded and completed browser playback. Details below. |

Implementation metric: **4/4, 100%** for these bounded implementation and audit criteria.
The full merge gate and post-deployment outcome are separate checks, recorded in the PR.

## What the live evidence says

- The two Sunday dealer fills before tick 1540 were CHA-07 at 20 P on tick 1474 and CHA-10 at 52 P on tick 1489. Current-round raw values at 1540 were ladder 0.09, team negotiation 0, duels 0 and market-making 0; bench points were null. Weighted score includes earlier rounds. Saturday duel success does not establish Sunday saturation.
- The 232 P hourly ledger total included 72 P of taker spend and a legitimate 160 P maker bid. A fresh `/me/offers` read proved offer 20287 open for MAL-11, created 1467, expiring 1547. It was a human-approved target, not a stranded reservation. No cap or reservation was changed.
- Known unaffordable dealer conversations consumed ticks 1522–1529 and 1530–1537 before their 24 P and 23 P finals failed the remaining 18 P budget. The fix moves that decision into planning. Unknown future concessions can still fail a fresh final guard.
- Only LAV-04 was a duplicate at tick 1539. Its 10 P ask, offer 20856, was open. Protecting the other sole page copies is intentional. The pending team-swap proposal failed Jev confidence, so no evidence justifies lowering that gate for activity alone.
- `book_levels` contained zero rows. This is missing stored market evidence, not proof of an empty live market. The six bench evidence rows captured at 1516 were historical session-start replays; no Sunday synthetic book or settlement was proven yet.
- Read-only bounded logs showed healthy tick processing and no deadline/budget timeout errors. Recent Phoenix spans measured native Jev offer-accept decisions at 0.240 s median, 0.361 s p95 across 26 calls, and team-swap decisions at 0.238 s median, 0.372 s p95 across 20 calls. The configured 30-second minimum tick for the LLM decider sends these 15-second ticks through native Jev. A faster model is not supported as the first fix by this evidence. No model/provider/auth settings were changed.
- Real production Live events advanced ticks 1540 → 1541 → 1542. Clock, dealer and incident updates were live; team, Jev, duel, settlement and Market Test examples were historical and labelled accordingly. Two short TTS samples returned 66,499 and 46,019 bytes, with 1,254 ms and 908 ms response latency. Both completed browser playback. The check made zero game writes and deleted its temporary credential file.

## Refund correction and 15-second runtime check

The expired MAL-11 bid exposed a second defect: after a maker restart, the old refund helper dated the credit
using `max_tick_seconds=60` even though Sunday ticks are 15 seconds. Row 431 had the right -160 P amount
at h12.679166666666667, outside the current window; original listing/spend rows 421/422 were at h13.7208.
The new `refund_bid` operation checks and inserts atomically, recovers a unique persisted timestamp,
and refuses any duplicate credit. Ambiguous history retains conservative dating.

The coordinator applied the independently reviewed timestamp-only correction at tick 1592, h14.2417.
A transaction locked the ledger and asserted the exact three rows before changing row 431's date to row 422's.
Output: `hourly_spend_before: 250`, `hourly_spend_after: 90`, `rows_unchanged: true`.
Independent reconnect readback confirmed `[(422,13.7208,160),(431,13.7208,-160)]`.
No amount, row count, cash balance or guardrail was changed. Three temporary-table checks proved exact pairing,
repeat refusal and refusal if another refund exists. Fresh real holdings/offers and captured settlement history
showed no MAL-11 acquisition and no remaining offer 20287 before correction.

Live clock at tick 1583 reported `tick_seconds: 15.0`, `paused: false`, `doors: open`, round 3.
Bounded Railway logs from roughly 07:41–07:49 UTC showed no tick gaps:
- taker ticks 1552–1582, 30 slack samples, minimum 4.4 s, median 7.05 s left;
- maker ticks 1553–1582, 30 slack samples, minimum 4.8 s, median 5.95 s left;
- duels ticks 1551–1583, no gaps.
No deadline/time-out/rate-limit errors were observed in that window. This verifies ordinary 15-second operation,
not a future full-load Market Test or duel wave.

The first full gate exposed two stale continuation-test fixtures plus one local PostgreSQL connection timeout.
Fixtures now open the intended dealer thread while affordable, then reduce cash to exercise the original
18→20 fallback and failed-value-read guards: `19 passed`. The isolated holdings suite reran `26 passed`.
The original full run had `5597 passed, 2 failed, 1 error`, coverage `95.35%`; final passing evidence belongs
in the PR after the frozen integrated run.

## Remaining PRs reviewed

Backend #276 is an optional buyer/venue-routing experiment, not a missing trading path. It enables addressed ranking despite weaker historical fill rates and has conflicts. Backend #244 is mostly superseded by SR1, but its remaining defensive parsing changes are not fully incorporated; the observed failure skips Workshop work rather than allowing an unsafe craft. Live #37 and #46 add affinity/rival intelligence panels, not required voice or operator execution capabilities. Both have conflicts and need their own current-main validation. None is merged wholesale as part of this bug fix.

## Next event checks

Use the actual clock and schedule, not an inferred wall-time anchor. At `now_hours=14.037`, the schedule listed the hard Market Test at 14.65, regular test at 15.0 and Duels III at 15.367. Keep writers and broker running, and use the deploy guard before every merge. A new Market Test must produce fresh book/request/response/terminal evidence before claiming success.

## Unverified

- Post-fix production behavior, future trade conversion and score improvement until observed.
- Receipt and settlement of the next Sunday synthetic book; fresh occurrence of every narrated event category.
- Physical microphone and speaker interaction. Browser audio playback was verified.
- The patch avoids known unaffordable openings; learned class prices cannot guarantee the eventual price of every card.

## Could-not-do

- Republish the required Claude architecture artifact: the available account has read-only access. Its owner must republish the generated repository page.
- Prove future scheduled outcomes before those events occur.
