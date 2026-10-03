# B27: the duel stack, consolidated

Night backlog B27, 4 Oct 2026. Branch `night/b27-duel-stack`, a draft integration PR that is **not for merging**: Marius merges the PRs one by one in the order below. The one-page summary for Marius is [duel-settings-card.md](duel-settings-card.md).

## 1. The chain and its merge order

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
See the [settings card](duel-settings-card.md). Recommended for both sessions: `duel_policy` = v2, `duel_endgame_ticks` = 1, `duel_endgame_min_share` = 0.3; everything else at today's value. For Duels II, also `duel_days_auto` = true.

## 3. End to end (local sim)
Run by `scripts/duel_e2e.py`, which refuses any target but 127.0.0.1, any real key, the live flag and any database. Each run:
- **Sim**: `bazaar-sim`, 3 seller/buyer pairs per team per session (6 concurrent duels on one deadline), rivals drawn from the zoo plus the two exploiters, decay 0.08, sessions every 13 ticks, odd sessions price-only and even ones two-issue.
- **Agents**: `bazaar duel run --play --no-jev` and `bazaar agent taker --live --no-jev` against that simulator, sharing one ledger (JSONL) and so the team's one accept per tick.

_Runs in progress at the time of this commit: the table lands in the next commit._

## 4. What Marius must decide
1. **Merge the chain** (#60 → #86 → #103 → #113 → #115 → #130) before Duels I, then set the card's values and restart `duel run`.
2. **`duel_endgame_min_share`**: 0.3 (both harnesses agree it is safe) or 0.5 (stronger against exploiters on W2a's zoo, but fails the deal-rate bar in W2b's arena).
3. **`duel_days_auto` for Duels II**, and read the first real two-issue payload yourself.
