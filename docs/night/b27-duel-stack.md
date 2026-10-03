# B27: the duel stack, consolidated

Night backlog B27, 4 Oct 2026. The one-page summary for Marius is [duel-settings-card.md](duel-settings-card.md).

**Update (06:30): superseded by #150 + #151.** A teammate took the chain over as two PRs:
- #150 (`ogarciarevett/takeover-duelsv2`): the duel player.
- #151 (`ogarciarevett/takeover-duel-sim`): the simulator side.

The six night PRs below are closed, and the integration PR #159 conflicts with #150. This report, the card and `scripts/duel_e2e.py` now come as a PR into #150; #159 should be closed (left for Marius). Section 1 is kept as the record of how the chain was made linear.

## 1. The chain and its merge order (historical: now inside #150)

| # | PR | branch | what | base |
|---|---|---|---|---|
| 1 | #60 | fix/duel-offers-inside-limit | v1 offers strictly inside our limit; guard `duel_inside_limit` | main |
| 2 | #86 | night/w2b-duel-v2 | policy v2 behind `duel_policy` (default v1); ledger fail-closed (9239070) | #60 |
| 3 | #103 | night/b11-endgame | B11 squeeze knobs (`duel_endgame_min_share`, `duel_jitter`, missed-tick cap) | #86 |
| 4 | #113 | night/b8-days-wiring | B8 `duel_days_auto`: one `rules_t` for policy and guard; latch on real evidence | #103 |
| 5 | #115 | night/b15-duels-first | B15: v1's forced endgame accepts booked and sent before Jev | #113 |
| 6 | #130 | night/b7-order-jev | B7: one early pass (v2 planned accepts, else v1 forced picks); tick-order evidence | #115 |
| tools | #80 → #97 → #117 | night/w2a-duel-zoo → b11-exploiters → b8-days | zoo, replay, gate, exploiters, days scenarios; sim knobs | main |

`night/b27-duel-stack` is main plus all of the above merged in this order, plus this PR's sim knobs and the e2e script. It has no conflicts, and its gates pass: pytest 1,112 passed with 1 strict xfail (B15's 5 s tick), and ruff, black and mypy are clean.

### How the chain got linear tonight
Each PR was first built on an older snapshot of its base, so three rounds of rebuilds made every PR sit on the previous head:

| conflict | where | resolution (kept in the PRs) |
|---|---|---|
| #113 × #103's ledger fail-closed | `cli.py` duel tick | #113 merged #103's head: the try/except `slots` read and the per-duel pre-booking try, with `rules_t` at every `gr.check` and `V2Params.from_rules` |
| #115 × #86's v2 pre-booking | `cli.py`, `test_jev_journal.py` | b15 rebuilt #115 on #113: the v1 forced pass runs only when `params is None`, inside the per-duel try, guarded on `rules_t` |
| #130 × #115 | `cli.py` early pass | W2b rebuilt #130 on #115: one early pass (v2 planned accepts, else v1 forced picks, nearest deadline first), one `done` set |
| main × #113 | `cli.py` (main's `DuelStore` / `save_finished`) | keep both. Main's own `?done=true` read also feeds the days latch, at no extra request. A test pins main's read off where it proves the days read stays off by default (fix-up f26f65c) |

**Merging into main:** main has moved on since #60 was cut. The main × #113 conflict above recurs when #113 is merged, with the same two-block resolution. b5's rehearsal (#120) carries the cross-PR fixes with the day PRs (#62, #68, #72, #79).

## 2. Config matrix
See the [settings card](duel-settings-card.md). #150 has the same GUARDRAILS lines with the same defaults.
- Recommended for both sessions: `duel_policy` = v2, `duel_endgame_ticks` = 1, `duel_endgame_min_share` = 0.3; everything else at today's value.
- For Duels II, also `duel_days_auto` = true.
- Jev `duel_move`: undecided (every run here used `--no-jev`).

## 3. End to end (local sim)
Run by `scripts/duel_e2e.py`, which refuses any target but 127.0.0.1, any real key, the live flag and any database. The runs used `night/b27-duel-stack` @ 51eece0, the closed chain.
- #150 has the same duel tick (one early pass, `rules_t`, fail-closed ledger).
- It adds a stricter days latch and malformed-row handling.
- On a ledger outage it holds every duel, v1 included.

None of that touches a price-only or a sim run, so these numbers stand for #150. To rerun on #150 + #151, merge both and use the same command. Each run:
- **Sim**: `bazaar-sim`, 3 seller/buyer pairs per team per session (6 concurrent duels on one deadline), rivals drawn from the zoo plus the two exploiters, decay 0.08, sessions every 13 ticks, odd sessions price-only and even ones two-issue.
- **Agents**: `bazaar duel run --play --no-jev` and `bazaar agent taker --live --no-jev` against that simulator, sharing one ledger (JSONL) and so the team's one accept per tick.

Both 15 s runs used the same simulator seed, so the same 36 duel scenarios (6 sessions × 6 concurrent duels; the first is practice, half are two-issue) and the same rivals:

| 80 ticks, 15 s | v1 (today's GUARDRAILS) | **v2 recommended** (v2, endgame 1, min_share 0.3) |
|---|---|---|
| deals / duels | 31 / 36 (0.86) | 30 / 36 (0.83) |
| sim points, scored sessions | 79.1 | **118.8 (1.50×)** |
| mean pie share per duel, after decay | 0.242 | **0.398 (1.64×)** |
| mean gain per deal (P, before decay) | 24.4 | 29.5 |
| rounds per deal | 6.94 | **1.33** |
| deals outside our limit | 0 | 0 |
| duel accepts / taker accepts (one shared ledger) | 20 / 5 | 23 / 5 |
| duel accepts refused by the one-per-tick cap | **11** (several duels on one deadline, first come) | **0** (the planner queues them) |
| missed ticks, skipped duels, slot taken by the taker | 0, 0, 0 | 0, 0, 0 |
| sends refused by the sim: the rival had just accepted that duel (settles next tick) | 13 | 5 |
| sends lost to a local network error (Errno 49, socket exhaustion) | 1 offer | 0 |

**At 30 s ticks** (v2 recommended, same seed and scenarios, 80 ticks): identical to the 15 s run.
- 30 / 36 deals, 118.8 sim points, 0.398 pie share per duel, 1.33 rounds per deal, 0 outside our limit.
- 23 duel and 5 taker accepts, 0 lost to the cap, 0 missed ticks or skipped duels.
- 5 sends refused because the rival had just accepted, and 1 clock read lost to Errno 49.

The duel tick never ran short of time at 15 s or at 30 s, so the outcome does not depend on the tick speed in that range.

**What it shows end to end:**
- v2 with the B11 settings keeps the zoo's lift on the live loop: 1.5× the points with a near-equal deal rate, about 5 fewer rounds per deal, and no outside-limit close.
- With 6 duels per deadline, v1 lost 11 accepts to the team's one-per-tick cap; v2 lost none.
- The taker and the duel loop shared the ledger without one slot clash.
- The machine had about 22,000 TIME_WAIT sockets (≈30 night sessions), so a few local requests failed with Errno 49. The loop logged "clock read failed … continuing" (1 tick in the v2 run) and lost no duel.

## 4. What Marius must decide
1. **Merge #150 (and #151)** before Duels I, then set the card's values and restart `duel run`. Close #159 (superseded).
2. **2fe2a40** (b15: v1 skips v2's slot read, one ledger round trip sooner) is missing from #150. It conflicts with #150's rule that a ledger outage holds every duel, v1 included, so #150's owner should decide.
3. **`duel_endgame_min_share`**: 0.3 (both harnesses agree it is safe) or 0.5 (stronger against exploiters on W2a's zoo, but fails the deal-rate bar in W2b's arena).
4. **`duel_days_auto` for Duels II**, and read the first real two-issue payload yourself.
