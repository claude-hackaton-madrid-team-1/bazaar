# B6: Saturday hour-by-hour playbook

Night of 3 Oct 2026, 03:11–. Draft PR, base `main`. Nothing touched the live game.

**Data:**
- `tests/fixtures/api/get_api_schedule.anon.json` and `get_api_clock.anon.json`;
- the shared Postgres `feed_events` and `duels` (read-only SELECT; ticks 0–159, 3,735 events);
- RULES.md, the openapi `Clock` and `ScheduleItem` schemas;
- W5's score model (#78, `score_sim.py`, imported unchanged) and the night PRs' verdicts in STATUS.md.

**Deliverables:**
- `docs/night/saturday-playbook.md`: the operator's page.
- `docs/night/saturday-schedule.json`: machine-readable, generated.
- `docs/night/saturday-plays.json`: the hand-written steps, gates and points.
- `bazaar timeline`: read-only, 13 tests.

## Finding: Saturday 09:00 is probably game hour 2.65, not 4.0
| Fact | Source |
|---|---|
| `t_hours` = "game hours since the opening; the clock stops overnight" | openapi `Clock` |
| Friday ticks were 60 s, `t_hours` = tick / 60 | stream capture, clock fixture (tick 31 = 0.5167) |
| Tick 150 arrived at 22:50:48 and tick 159 at 22:59:47 Madrid; there is no tick 160 | `feed_events.received_at` |
| So tick 0 was ≈ 20:21, and the clock froze at **h 2.65** | arithmetic |
| The h 3 Market Test never fired; there is no `bench` event on Friday | `feed_events` types |
| The practice duels froze mid-session: our 26 of 34 had started, 6 were live | `duels` table |

Unless the organisers jump the clock to h 4.0 at the opening, every Saturday event lands 1 h 21 min after the
published calendar:

| Event | resume | jump |
|---|---|---|
| h 3 Market Test | 09:21 | overdue |
| Round 2, El Retiro, 150 P grant | 10:21 / 10:24 | 09:00 / 09:03 |
| Duels I | 12:51–14:27 | 11:30–13:06 |
| Duels II | 19:21–20:57 | 18:00–19:36 |
| Hard Market Test | 22:21 | 21:00 |
| h 17 Market Test | Sun 09:21 | 22:00 |

Under resume, 09:00–10:21 still belongs to Friday's round, and cash at 09:00 is **353 P, not 503** (83 P above the
floor). Other plans that assume 503 P at 09:03 or h 4 = 09:00 (W3, W4, W7, B2) shift with this. That was posted to
STATUS.md at 03:32.

**Validation:** anchored at the clock fixture (h 0.5167 at 20:52), the tool puts the practice duels at 22:20:59.
The feed has them at tick 120 = 22:20:48.

## Points per action (W5's model; 1 Saturday round point = 0.40 final)
| Lever | Round pts | Final | Note |
|---|---|---|---|
| Duels I + II at the top-3 level | 12.5 | 5.0 | weight assumed; 0.12 per duel (0.18 / 0.09 if sessions weigh equally) |
| 8 Market Tests at stall level | 7.5 | 3.0 | if the free stall does not score for us; otherwise a venue adds ~+0.19 final (B2) |
| Abuela best three at 0.95 | 10.7 (7.9 at a top-3 mean of 1.5) if the ladder restarts per round, +2.44 if it carries over | 3.2–4.3 / 1.0 | ~54 P; under resume also +0.49 final in Friday's round |
| `duel_policy` v2 | +3.6 if v1 sits at 0.70 of the top-3 | 1.4 | W2's 1.42× lift |
| Three Chato uncommons at 0.5 | +2.82 | 1.1 | ~87 P, needs the cap |
| W4's trades (+80 P) | +1.3 to +4.0 | 0.5–1.6 | 82 P |

## Verdict (no criteria in PLAN.md for B6; these are mine)
| Criterion | Result |
|---|---|
| Every Saturday schedule event has a wall time in both columns and a step | **GO**: 17 of 17 events before h 18 have a play attached (tested) |
| The tool reproduces a real Friday event time | **GO**: 11 s off on the practice duels |
| Every command in the playbook exists, with the PR it needs | **GO**: taken from the PR diffs, not from memory |
| Points per action from #78 without new formulas | **GO**: `component_points`, `ladder_marginals`, `final_points_per_round_point` |
| The clock column is known | **NO-GO until 08:55–09:05**: gate G0 (`uv run bazaar clock`, `bazaar timeline --from-api --compare …`, then the feed) |

## Risks
- **Weights.** Four of the five component weights are assumed (W5), and so is the duel split between sessions.
- **Clock pace.** If `t_hours` does not follow the wall clock at 30 s ticks, both columns are wrong. The API and the
  simulator both say it does.
- **Organiser actions.** The organisers can fire sessions by hand or change the pace: `bazaar timeline --from-api`
  re-anchors in one command.
- **Duel windows** are upper bounds.

## What Marius must decide
1. **At 08:55 (G0):** which column the day runs on.
2. **Merges.** They redeploy the live services (r2's X16), so do them before 09:00 or between duel sessions:
   - #61 → #68 → #72 before 09:00 (r2 X7: main's taker can break the floor and the hourly cap within one tick);
   - #60 before Duels II;
   - #62 before Duels I;
   - #86 (v2) before Duels I, or not at all.
3. **Under resume: the 83 P before the grant.** Ladder best three (counts for Friday's round too) or a 09:00 venue
   (B2's floor 50). Not both: the playbook orders ladder → G3 → grant → venue → trades.
4. **G5 Chato caps:** worth +2.82 round points, and probably level 3's early unlock.
