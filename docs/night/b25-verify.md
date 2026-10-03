# B25 · Morning assumption verifier (night shift, 3 Oct 2026)

Draft PR on `night/b25-verify`, stacked on B21 (`night/b21-levels`, #119). Nothing went live.
- **Inputs:** every night report (`docs/night/*.md`), PLAN.md, REVIEWS.md and BITES.md, read by four read-only harvest passes and merged by hand. Friday's public feed and the API fixtures for the dry run.
- **Run:** `uv run bazaar verify` (fixtures), `uv run bazaar verify --live` (tomorrow; GETs only; the feed checks start at today's first tick, worked out from the clock, or pass `--since-tick`). Add `--priority high` for the short list, `--json` for a machine-readable copy.

## What it is
**112 assumptions, one catalogue entry each** (`src/bazaar_agent/data/assumptions.json`). Each entry has:
- the assumption, in one sentence, and the reports it comes from;
- the read-only check after doors open: endpoint, field, and the time it can first be read;
- the PR or decision it flips, and the default to keep if it stays unknown;
- a priority: 27 high, 43 medium, 42 low.

**18 checks run automatically** on a snapshot (`/clock`, `/schedule`, `/me`, `/levels`, `/dealers`, `/venues`, `/leaderboard`, `/duels`, `/catalog` and the feed). The other 94 say how to settle them by hand. A check says PASS or FAIL only when every field it needs is there: a missing or renamed field, a run too late to tell (a ladder or cash check after our first Saturday deal), or a check that throws is UNKNOWN with the reason, and never hides the others. Time-dependent checks (the clock's anchor, the tick numbering) subtract the real time since the doors opened, so a late run reads the same as one at 09:00.

**Read-only by construction.** `snapshot_live` calls only GET methods: public endpoints without a key, `/me` and `/duels` with the team key. `--live` was **not** run tonight (BRIEF).

## Dry run on Friday's data (fixtures + Friday's feed)
`uv run bazaar verify --feed <Friday's feed>`: **PASS 6, FAIL 1, UNKNOWN 105** (94 of them manual).
- **PASS:** limits unchanged, the 150 P grant on the schedule, Market Tests on the schedule, and Abuela's menu, openings (by rarity: commons 12 in 30 threads and 7 in 4; uncommons 29 in 56 and 17 in 4) and floors (median fills: uncommons 23, commons 9.5).
- **FAIL:** `chato-unlocked`. Expected: the fixture `/me` is from tick 31, before Chato opened. Tomorrow's `/me` decides it.
- **UNKNOWN:** every Saturday-only fact: the clock's pace and anchor, cash at the open, the ladder reset, stall scores, El Chato's menu (the fixture predates him), L3.

## The timed checklist (Saturday 09:00–11:30)
The clock froze at h2.65. At 09:00 it either **jumps** to h4 (grant 09:03, h5 Market Test 10:00, Duels I 11:30) or **resumes** at h2.65 (h3 Market Test ~09:21, grant ~10:24, Duels I ~12:51). The 09:00 row decides which column applies. `AUTO` marks a check `bazaar verify` runs; the others are by hand.

### 08:55 (doors still closed)
| check | read | PASS → | FAIL → |
|---|---|---|---|
| `closed-clock-payload` | `GET /api/clock` → `next_opens` / `next_tick_in` | #106 wakes the loops at 09:00 | restart the services after 09:00:00 |
| `market-test-schedule` AUTO | `GET /api/schedule` → Market Test entries | #118/B20 venue timing as planned | re-time the venue plan |
| `duel-schedule-and-decay` | `GET /api/schedule` → Duels I/II | merge-freeze windows as planned | move the freezes |
| `schedule-retimed` (again 09:01) | `GET /api/schedule` → `at_hours` | the hand columns hold | use only `bazaar timeline --from-api` |
| `orphan-threads-at-open` (again 09:00:05) | `GET /api/me/threads`, `/me/offers` | none open: nothing to do | B17 (#114) before the taker goes live, or close orphans by hand |
| `venue-keys-table-shape`, `railway-flags-unset` | read-only SQL / Railway variables | #71, #89, #91 merge safety | do not merge them before 09:00 |

### 09:00 (doors open)
| check | read | PASS → | FAIL → |
|---|---|---|---|
| `clock-pace-30s` AUTO | `GET /api/clock` → `tick_seconds` | W3 slots every 6 ticks = 3 min | re-time W3, W7 and the B6 playbook |
| `clock-anchor` AUTO | `GET /api/clock` → `t_hours` ≈ 4.0 | **jump column**: grant 09:03, round 2 from 09:00 | **resume column**: grant ~10:24, h3 Market Test ~09:21 |
| `limits-unchanged` AUTO | `GET /api/clock` → `limits` | 1 accept/tick, 12 listings/tick, 6 conversations | re-share the accept slot (desk, maker, duels) |
| `chato-unlocked` AUTO | `GET /api/me` → `level`, unlocked dealers | B21's L2 best three as written | L2 plan waits for Chato |
| `cash-at-open` AUTO | `GET /api/me` → `cash` = 353 (503 once the grant fired), before any settlement of ours | 83 P of headroom above the 270 floor | re-run W7's cash plan (#87) |
| `grant-150-saturday` AUTO | `GET /api/schedule` (then `/me` cash at 09:03 or ~10:24) | 233 P spendable after it (W7) | spend only the 83 P above the floor |
| `abuela-menu`, `chato-menu` AUTO | `GET /api/dealers` → Abuela 8 deals/h, pack 30, 3 packs/h; Chato 6 deals/h, uncommons 30, rares 90 | ladder quotas and the Chato cap proposal as modelled | re-run `bazaar ladder plan --dealers` |
| `practice-duels-resume-and-take-accepts` | `GET /api/duels` | (resume only) duels hold the accept slot until ~09:17 | the ladder has the slot |
| `venue-mechanism-fixed-at-opening`, `page-bonus-in-your-value`, `opening-board-equals-friday-close`, `team-fill-rates` | see the catalogue | | |

### 09:00–09:05 (before the first Abuela deal settles)
| check | read | PASS → | FAIL → |
|---|---|---|---|
| `ladder-resets-per-round` AUTO | `GET /api/me` → `score.ladder_points` ≈ 0 | 3 new deals per level today (W3, W7, B21 as written) | Friday's best three stay: only deals beating 0.73 count; shift spend to Chato |
| `thread-bids-in-me-offers` | `GET /api/me/offers` after our first dealer bid | #72's cash accounting holds | keep few dealer threads at once |
| `me-cash-subtracts-open-bids` | `/me` cash vs `/me/offers` after our first open bid | plans subtract bids themselves | re-run W7 (82 P subtracted twice) |
| `accept-settles-next-tick`, `thread-bids-expire-2-ticks`, `thread-view-carries-settled-offer`, `price-range-is-opening-to-limit` | first accept / first dealer deal | | B16 urgent; B17's orphan rule; #72 booking |

### ~09:05–09:20 (Abuela's first threads and fills)
| check | read | PASS → | FAIL → |
|---|---|---|---|
| `abuela-openings` AUTO (3 card buy threads) | feed → her opening asks today, by rarity: median 29 (uncommons), 12 (commons) | W3 ranges 21→25, 8→11 | `bazaar ladder floors --since-tick <today>` |
| `abuela-floors` AUTO (4 card fills) | feed → today's fill prices | `ladder_floor_quantile` (#81) as fitted | re-fit with `--since-tick` |
| `one-dealer-round-per-tick-and-abuela-patience` | feed, ~09:15 | `ladder_floor_quantile = 0.5` (#81 decision 2) | leave it off |
| `unlock-excludes-opening-price-deals` | `/me`, `/levels` after the first ladder deals | #81 never takes the opening ask (fd37ff6) | – |

### ~09:21 (resume only): the h3 Market Test
| check | read | PASS → | FAIL → |
|---|---|---|---|
| `stall-scores` AUTO | `GET /api/me` → `score.bench_points` | the stall earns: a venue is worth ~0.45/round, keep the 270 P (W7 decision C) | it doesn't: a venue is worth ~7.5–8/round (#71, B2/B20) |

`starter-stall-me-shape` (at h3: 09:00 under jump): how `/me` shows the free stall decides #71's `effective_cash_floor`. #71 stays unmerged until it is read.

### 10:00 (jump): the h5 Market Test and Abuela's next hour
- `stall-scores` AUTO, as above, if it was not read at 09:21.
- `abuela-allotment-resets-per-clock-hour`: a 9th Abuela deal is offered at 10:00, not 60 min after the first deal (#81 slot timing).
- `bench-arrivals-spread`, `stall-crosses-all-pairs-fee-0` (if watched): `GET /api/broker/book`; they set the value of `BAZAAR_BENCH_POLICY=edge` (#84/#92).
- `rival-affinity-map-persists`: the morning's chaser flows still match W4's map (#79 `chaser_min_p`).

### ~09:15–10:30: El Chato
- `chato-limits-28-32-rares-82-93`: after the first Chato settlements (anyone's, in the feed). If they fill at 28–32 for uncommons, **#81 decision 1** (`dealer_price_caps = chato:uncommon=31`) is what opens L2's best three and the early L3 unlock (#119). Unknown: caps stay off, L3 arrives at open-to-all.

### ~10:24 (resume): the grant
- `grant-150-saturday`: `/me` cash rises by 150. Then W7's 233 P plan and the Chato slots start.

### Any time: L3
| check | read | PASS → | FAIL → |
|---|---|---|---|
| `l3-announced` AUTO | `GET /api/dealers`, `level.announced` | B21 step 3: read its `unlock` and menu | no L3 yet |
| `l3-unlock-rule` AUTO | `GET /api/dealers/<id>` → `unlock` | 3 negotiated Chato buys (B21) | re-plan the L3 path (`bazaar plan levels`) |

At `level.activated`: `collector-l3-buys-cards` (#93 dealer sell vs buy). At the next `level.unlocked`: `unlock-excludes-dealer-sales` (#93 decision 2).

### 11:30 (jump) / ~12:51 (resume): Duels I and our venue
`duel-accepts-share-team-slot`, `duel-standing-offer-stays-acceptable`, `duel-within-tick-order`, `duel-accept-adds-round`, `rivals-concede-unilaterally`, `days-meaning-sign` AUTO (Duels II), `server-refuses-second-venue`, `rate-limits`, `our-venue-replaces-stall-in-bench`, `cash-floor-is-budget-not-safety`. Each names its PR (#86, #103, #71, #78, #84, #92, #118) in the catalogue.

## What flips, by PR
| PR / decision | the checks that settle it |
|---|---|
| #81 ladder: `dealer_price_caps`, `ladder_floor_quantile`, `ladder_level_deals` | `ladder-resets-per-round`, `abuela-openings`, `abuela-floors`, `chato-limits-28-32-rares-82-93`, `one-dealer-round-per-tick-and-abuela-patience`, `clock-pace-30s` |
| #119 levels (L2 best three, L3 early) | `chato-unlocked`, `l3-announced`, `l3-unlock-rule`, `unlock-excludes-dealer-sales` |
| #87 W7 cash plan | `clock-anchor`, `cash-at-open`, `grant-150-saturday`, `me-cash-subtracts-open-bids` |
| W7 decision C, #71, B2/B20 venue | `stall-scores`, `starter-stall-me-shape`, `market-test-schedule`, `server-refuses-second-venue` |
| #93 personas (`allow_flags`, dealer sell) | `trickster-lie-is-in-structure`, `wrong-flag-cost-and-repeat-flag-scoring`, `collector-l3-buys-cards`, `thread-message-id-key` |
| #109 packs | `abuela-menu`, `dealer-deal-scores-only-in-best-three` |
| #86 / #103 duels | `duel-*`, `days-meaning-sign`, `exploiters-back-off-when-refused` |
| #79 W4 trades | `team-fill-rates`, `rival-affinity-map-persists` |

## Decisions for Marius
1. **Run `bazaar verify --live` from 09:00?** It is GETs only and never writes, but it is a keyed read of `/me` and `/duels`. Without a key it reads only the public endpoints.
2. **Which column, jump or resume,** is the 09:00 `clock-anchor` row. Everything timed after it follows.

## Risks
- **Hand-merged catalogue:** about 156 harvested items were merged into 112 by hand. Two near-duplicates may survive under different ids, and a claim may be worded more strongly than its source. Each entry lists its sources.
- **Unverified field names:** the automatic checks read fields from the fixtures and the simulator (`score.ladder_points`, `score.bench_points`, `tick_seconds`, `unlock`). If Saturday's payload renames one, its check says UNKNOWN (tests cover the missing-field cases).
- **Feed checks** start at today's first tick. With `--live` it is worked out from the clock (tick, pace, time since the open). If that fails it is the current tick, so the checks wait for today's threads rather than read Friday's. Without `--live`, pass `--since-tick`.
- **The checklist times** follow the jump and resume columns. A re-timed schedule (`schedule-retimed`) overrides both.
