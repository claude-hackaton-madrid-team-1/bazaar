# Night B7: duel v2's within-tick order risk and the Jev path

Draft PR stacked on #103 (`night/b11-endgame`, which contains #86). Offline only. No new parameters and no changed defaults.
Reproduce with `uv run python scripts/duel_tournament.py` (the "Within-tick order" sections). W2a's zoo numbers come from
PR #97's harness with `team_first` drawn per scenario.

## The risk
v2 holds while the rival concedes and takes its offer late, so it is sensitive to **when within a tick we read**. If we move
before the rival, we see its tick-t offer only at t + 1. On W2a's zoo that cost v2 about 0.1 of lift (0.08/0.10: 1.56 →
1.45). The question was how often that happens in the real game.

## What the real payloads say (`duel_arena.tick_order`)
The practice payloads keep each duel's messages in chronological order. In the 49 ticks where both sides priced, **we
priced first in 27 (55 %) and the rival in 22**. The order is a property of the rival, not of the tick:

| duel | we first | rival first | reading |
|---|---|---|---|
| 85, 86 | 5, 6 | 0, 0 | a reactive rival: it answers our message within the same tick |
| 202, 268 | 7, 3 | 2, 2 | mostly reactive |
| 96, 263, 273, 274 | 1, 1, 1, 1 | 5, 4, 3, 6 | an early mover: it acts at the start of the tick, before our loop |

So the real game is a mix, about 55 % "we move first". The arena now draws each rival's order with that probability
(`tournament(..., team_first_share=0.55)`).

## v2 under the real mix (lift over #60's v1, 3 seeds for W2a)
| harness | rival first | **55 % we first (real mix)** | 100 % we first |
|---|---|---|---|
| W2a zoo, 0.06/0.08: v2 | 1.428 | **1.380** | 1.335 |
| W2a zoo, 0.06/0.08: v2 + B11 settings (endgame ticks 1, min share 0.3) | 1.437 | **1.411** | 1.388 |
| W2a zoo, 0.08/0.10: v2 | 1.564 | **1.507** | 1.453 |
| W2a zoo, 0.08/0.10: v2 + B11 settings | 1.572 | **1.540** | 1.508 |
| our arena, 0.08/0.10 (6 duels per deadline): v2 | 1.42 | **1.45** | 1.48 |

In our arena moving first hurts v1 more than v2. On W2a's zoo it hurts v2 more.

## Mitigations tried
| mitigation | effect (real mix) | verdict |
|---|---|---|
| **B11's settings** (`duel_endgame_ticks` 1, `duel_endgame_min_share` 0.3, PR #103) | W2a +0.03 at both decay pairs; halves the we-first penalty (1.335 → 1.388, 1.453 → 1.508) | **use**: it is already the B11 recommendation |
| free offers wait 3 ticks for the rival to open; last offer at D − 3 *and* D − 2 (both in #86) | W2a team-first replay 140 → 153 P; tit-for-tat deals 0.87 → 0.999 | in |
| a second, accept-only `/api/duels` read mid-tick (catch a reactive rival's same-tick answer) | our arena: 1.450 → 1.424 (it takes same-tick offers that waiting would have improved) | **not built** |
| `duel_accept_margin_ticks` = 0 (accept on D − 1 too) | W2a we-first 1.44 → 1.54 at 0.08/0.10 (measured earlier tonight on an earlier head) | strongest, but waits on one real D − 1 accept that settles |

## Reconciled with B15 (#115) and the Jev path
- **Accepts before Jev.** v2's planner accepts are now booked **and sent** before Jev is asked about the other duels.
  B15 does the same for v1's forced endgame accepts. This replaces #86's book-before/send-after pass, so a slow Jev can no
  longer strand a booked slot. Jev's only legal move for such a duel is that accept anyway.
  Test: `test_under_v2_the_planners_accept_books_the_slot_before_jev_is_asked`, which checks reserve → send → Jev.
- **Merge recipe with #115.** Gate B15's forced v1 pass (`tick_moves` / `forced_pick`) on `duel_policy == "v1"`. Under v2
  the early pass here sends the planner's accepts, earliest deadline first and the slowest rival first when the queue binds.
  `forced_pick` calls `legal_moves(..., v2=None)` and would otherwise take the slot in API order.
- **Jev under v2** (since #86):
  - `DuelJev.pick(v2=params)` uses v2's planned move as the default.
  - The planner's accept is Jev's only legal move when the planner gives it.
  - Otherwise accept is not legal, and counter is legal only within the round cap.
- **Tests:**
  - Jev's counter under v2 stays inside our limit, and Jev may hold back a v2 counter;
  - Jev cannot hold back a planned accept (6 duels on one deadline, Jev always says hold);
  - Jev reads v2's default;
  - the CLI cases.

## Verdict
- **GO with B11's settings.** At the real tick-order mix v2 + B11 clears 1.40× on W2a at both decay pairs (1.41, 1.54).
  It clears it on our arena too (1.45).
- v2 alone at 0.06/0.08 under the real mix is 1.38 on W2a, under the bar. Decay 0.06 is no longer on the schedule.

## Risks
- The 55 % comes from 10 duels against practice rivals (bots or teams that may change by Saturday). The per-rival split
  matters more than the average.
- Accepting on D − 1 is the biggest remaining lever, and it is unverified.

## What Marius must decide
1. Run v2 with B11's settings (the tick-order mitigation comes with them).
2. Verify one D − 1 accept, then consider `duel_accept_margin_ticks` = 0.
