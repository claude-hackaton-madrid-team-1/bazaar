# LF1 implementation and live audit

Scope: [LF1-spec.md](LF1-spec.md). Presentation work remains withdrawn.

| Acceptance | Status | Evidence |
|---|---|---|
| Budget-aware dealer planning | Verified | `plan_dealer_buy` caps ordinary ladders and final prices to remaining room, skips known unaffordable negotiated fills, and preserves unknown opening-price concessions. Existing fresh send guards remain. |
| Budget regression and recovery | Verified | Independent `uv run pytest -q tests/test_dealer_plan.py tests/test_taker.py tests/test_taker_hard_dealers.py`: `79 passed in 0.61s`. Includes 232/250 spend, affordable alternative, pending cash and expired-spend recovery. No duel policy changed. |
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
