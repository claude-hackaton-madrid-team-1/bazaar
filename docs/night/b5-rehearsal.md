# B5: full dress rehearsal (night of 3 Oct 2026)

**DO NOT MERGE this branch.** It integrates every day PR and night PR to find merge conflicts and cross-PR bugs,
then runs taker + maker (with #71's venue keeper and broker) + duel player together, LIVE, against a **local**
`bazaar_sim`. Nothing touched the real game, Railway or the shared Postgres.

## Verdict
**GO for the merge sequence below, but only with the fix-ups.** Twelve PRs merge in this order with additive
conflicts only. Each of the 10 fix-ups listed here repairs a defect that exists only when two PRs are merged
together: each PR passes its own tests. Without them:
- `bazaar dealer buy --live` crashes on a ledger outage, or bids with the ledger down;
- the maker raises `AttributeError` every tick with #79 on #62;
- #79's per-counterparty cap silently never applies;
- a ledger outage costs every duel its move;
- a `trading_enabled = false` edit does not stop the venue keeper or the broker.

**Decision needed first:** #71 (force-pushed three times tonight by a teammate) is no longer build-only. It sets
`cash_floor` 270 → **100** and `allow_venue_open` = **true**, and the maker opens a real venue at h6.5. That
contradicts the 02:30 decision (no venue, no `cash_floor` change tonight). Once #71 merges and Railway redeploys,
the LIVE maker opens a board venue at h6.5 by itself.

## Merge order (frozen heads, 04:00–04:25) and what each step needs
| # | PR @ head | conflicts (all resolved additively; details in the merge commits) | needs this fix-up |
|---|---|---|---|
| 1 | #60 @ c6ccda4 | none | |
| 2 | #62 @ 90e0fea | README, cli.py (duel send site, agent banner) | drop #62's unused `kind` (F841) |
| 3 | #72 @ e4efc82 (contains #61 + #68; merge commit, not squash) | cli.py `dealer buy`, guardrails.py, test_ledger.py | **F1** |
| 4 | #71 @ ffb0877 | guardrails.py (Context, check), maker/taker imports, README, memory | **F2**; test pin in test_dealer_buy_cli |
| 5 | #86 @ f6f4435 | cli.py `duel run` (play_one + booking), guardrails.check | **F5** |
| 6 | #81 @ cce2de5 | taker.py (#72's reopen + `dealer=`), cli.py, guardrails.py | F6 (isort) |
| 7 | #79 @ 9e99763 (squash: private numbers in its history) | GUARDRAILS.md, seller.py, taker.py, guardrails.py, ledger_pg.py, STRATEGY.md | `_sell_context(live)`, **F7** |
| 8 | #84 @ 50eb905 (on #71 e489449) | none | |
| 9 | #87 @ 5b9deaf | render.py imports | **F8** |
| 10 | #78 @ 6ab5b1e | .env.example, conftest.py | test stub for #62's live ledger |
| 11 | #80 @ 1b8c1fc | none | |
| 12 | #77 @ e81a336 | none | **F9** (+ F9b) |

The rule for the merger: a fix-up belongs to **whichever PR of its pair merges second**. Every fix-up is a
separate commit on this branch (`git log --first-parent`), so it can be cherry-picked.

## Cross-PR bugs found (each passes alone, breaks together)
| id | pair | severity | what breaks once both are merged | fix commit |
|---|---|---|---|---|
| F1 | #62 × #72 | high | `dealer buy --live`: a ledger outage at the accept slot raises a traceback (#61 moved the reservation out of #62's guard). A plain `False` would make #72's `meet_ask` bid with the ledger down. Now the tick holds: 2 failures → 2 held ticks → accept | 86b3937 |
| F2 | #68 × #71 | high | the venue keeper and broker build `Context` without `stops=kill_switch()`. The keeper runs **before** the maker's hold check, so a `trading_enabled = false` edit does not stop a venue open or broker matches until restart | 6796d02 |
| F5 | #62 × #86, #68 × #86 | high | #86's free-slot read and v2 pre-booking sit outside #62's per-duel `try`. One ledger outage costs **every** duel its move, v1 included; #62's own test fails. The pre-booking also ignored a live kill-switch edit | ae5ca6e |
| F7 | #62 × #79 | high | `PgLedger.hands_off_ids` uses #62's removed `_conn`, and `FallbackLedger` lacks it, so the maker raises `AttributeError` every tick | bbedb67 |
| F7b | #60 × #79 | high (silent) | a positional `Action(..., your_value, counterparty)` lands in #60's duel `limit`, so `max_counterparty_share` never sees the team. #79's source moved to keywords tonight but its tests still did it. `Action`'s fields after `your_value` are now `KW_ONLY` | bbedb67 |
| — | #62 × #79 | high | `_sell_context()` (#79) lost #62's `live`, so `sell list/bid/swap` hit a NameError, even in dry run | merge 7525a0a |
| F8 | #79 × #87 | medium | both define `cli._json_file`; the later one silently replaced the earlier, so `bazaar affinity --me file` fails | merge 0c5047b |
| F9 | #71 × #77 | medium | #71's sim tests and `scripts/sim_market_test.py` import `bazaar_sim.broker._auto_bench/possible_gains`, which #77 moved | f481e9e, 5544cfb |
| F6 | #55 × #81 | low | ruff I001 in #81's `scripts/sim_e2e` (bazaar_sim is first-party on main); the hook refuses the merge | merge df93cd1 |
| — | #71 × #72/#87, #62 × #78 | test-only | tests that read the committed `cash_floor` 270 (#72, #87), or run `duel run --play` without a shared ledger (#78) | in the merges |
| low | #71 × #79 | low | `trade-plan`'s `Context` has no `has_venue`: after the venue opens it still plans against 370, not 100 (conservative) | not fixed |

## Rehearsal on a local simulator (interim: the usage limit cut the session at ~04:35)
Harness: `bazaar_sim` on a free `[::1]` port (127.0.0.1's ephemeral ports were exhausted by other sessions:
~14.8k TIME_WAIT). Taker `--live`, maker `--live` (venue keeper + broker inside) and `duel run --play` ran as
separate processes with `scripts/sim_guard` (loopback-only sockets), an empty env file and dead proxies.
The shared ledger, decisions and broker-key vault went to a throwaway `pgvector` container on [::1]:55491.
The only change to the committed files was `venue_open_after_game_hours`, lowered in a scratch copy of
GUARDRAILS.md so that the venue opens within the run.

| run | ticks | crashes (`Traceback`/`tick loop:`) | venue | Market Tests | notes |
|---|---|---|---|---|---|
| shakedown (3 s ticks) | 12 | 0 | opened tick 8 (400 → 130) | bench efficiency 0.985 | 2 duel deals (shares 0.51 / 0.84); 3 broker matches DROPPED (tick window closed) |
| A (4 s ticks, interim at tick ~130 of 300) | 130 | 0 | opened tick ~54 (372 → 102) | b1: 5 pairs, quoted 146, 0 refused; b2: 4 pairs, 137, 0 refused | the 370 effective floor held: taker buys stopped at 372 |
| B (30 s Saturday pace, interim) | ~20 of 40 | 0 | opened tick 5 | b1: 2 pairs, quoted 32, 0 refused | |

Findings from the runs so far:
- The guardrails compose across processes: the effective floor (100 + 270 until the venue opens) held in
  every `/me` sample, and no tick booked more than one accept.
- **Duel after the rival accepted our offer** (low): the payload still says `live`, so `duel run` sent an accept
  on a duel the rival had already accepted (refused `duel_closed`). That accept still booked the team's one
  accept slot for the tick.
- **Broker matches dropped at the tick edge** at 3–4 s ticks: the keeper's paced matches (0.2 s each) run before
  the maker's offers. Check with B's 30 s ticks before trusting it at 15 s on Sunday.

Not done (usage limit): the final numbers of runs A and B, run C with `duel_policy` v2 + `BAZAAR_BENCH_POLICY=edge`,
and r2's bite suite on this tree. The harness is reproducible: `_night/` notes in STATUS.md, scripts in this
session's scratchpad (`rehearse.py`, `run_agent.py`, `analyze.py`).

## Not integrated
Wave-2 PRs #89, #91–#119, #121–#130 (B-items, r1/r2, teammates' feed reader, holdings DB, etc.): outside this
rehearsal's frozen scope. Heads that moved after the freeze: #71 (ffb0877 applied as a delta). Pass 1 (old #71/#72
heads, 4 fix-ups) is kept as `night/b5-rehearsal-pass1`.

## What Marius must decide
1. #71 as it stands: auto-opening a real venue at h6.5 with `cash_floor` 100 (vs. the 02:30 decision).
2. #62 before merging: every LIVE Railway service needs `DATABASE_URL` set to the shared Postgres, otherwise
   taker/maker/duels exit at start ("refusing to trade"). #71's opening also needs Postgres (the claim and the vault).
3. Fold the fix-ups into the second PR of each pair (table above), or merge this branch's fix-up commits.

