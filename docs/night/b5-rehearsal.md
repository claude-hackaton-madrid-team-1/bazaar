# B5: full dress rehearsal (night of 3 Oct 2026)

**DO NOT MERGE this branch.** It integrates every day PR and night PR to find merge conflicts and cross-PR bugs,
then runs taker + maker (with #71's venue keeper and broker) + duel player together, LIVE, against a **local**
`bazaar_sim`. Nothing touched the real game, Railway or the shared Postgres.

## Verdict
**GO for the merge sequence below, but only with the fix-ups.** Integrated, the taker, maker (venue + broker)
and duel player ran 580 ticks LIVE together on a local simulator: 0 crashes, 0 floor breaches, 0 double accepts,
11 Market Tests with 0 refused matches. Twelve PRs merge in this order with additive
conflicts only. Each fix-up listed below repairs a defect that exists only when two PRs are merged
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

**Review of the fix-ups** (an independent agent, 633 targeted tests + mypy):
- One confirmed bug, low severity, in F1: a ledger-outage hold could fire on a later tick when `negotiate`
  returned early. It is fixed in 9a51731, where a hold expires with its own tick.
- No other bugs. Every `Action(` call is keyword-safe; with the free-slot read failing to 0, no duel accept is
  booked; `venue_close` is still blocked by the kill switch alone.
- Known limits:
  - A dealer-buy *guard-time* outage still walks, as #62 chose.
  - If a v2 pre-booking commits but its reply is lost, that duel loses its accept for the tick (#86).
  - `sell list/bid` raise if the live ledger drops after it opened (from #62).

## Rehearsal on a local simulator
**Harness** (`scripts/rehearsal/`: `rehearse.py`, `run_agent.py`, `analyze.py`).
- `bazaar_sim` serves on a free `[::1]` port. 127.0.0.1's ephemeral ports were exhausted by other sessions'
  local servers (~14.8k connections in TIME_WAIT), so new connections failed with "Can't assign requested address".
- The taker (`--live`), the maker (`--live`, with #71's venue keeper and broker inside it, as on Railway) and
  `duel run --play` run as separate processes.
- Every process gets `scripts/sim_guard` (loopback-only sockets), an empty env file, dead proxies and an
  allow-listed environment.
- The shared ledger, decisions and broker-key vault go to a throwaway `pgvector` container on [::1]:55491.
- Only scratch copies of GUARDRAILS.md differ from the committed one: `venue_open_after_game_hours` is lowered
  so that the venue opens within the run. In run C, `duel_policy` = v2 as well.

| | A: accelerated (4 s ticks) | B: Saturday pace (30 s ticks) | C: night switches on (4 s, v2 + edge) |
|---|---|---|---|
| ticks handled per agent | 300 / 300 / 300 | 40 / 40 / 40 | 240 / 240 / 240 |
| crashes (`Traceback`, `tick loop:`), exits | 0, all exit 0 | 0, all exit 0 | 0, all exit 0 |
| venue | opened at tick 54: cash 372 → 102 | opened at tick 5 | opened at tick 54: 372 → 102 |
| Market Tests | 5 sessions, 22 matches, 0 refused, efficiency 1.0 | 2 sessions, 5 matches, 0 refused, 0.971 | 4 sessions, 18 matches, 0 refused, efficiency 1.0 |
| cash vs effective floor (370 before the venue, 100 after) | 0 breaches in 101 samples (min 102) | 0 breaches in 19 samples (min 105) | 0 breaches in 80 samples (min 102) |
| ticks with more than 1 accept booked (all processes) | 0 (9 accepts) | 0 (3 accepts) | 0 (8 accepts) |
| duels | 10/10 deals, 8.3 rounds per deal, mean share 0.48 | 4 deals + 2 live at end, 8.3 rounds | 8/8 deals, **0.12 rounds per deal**, mean share 0.50 |
| guardrail denials | 148 venue reserve, 18 rarity cap, 9 spend cap | 16 rarity cap, 12 cash floor, 12 venue reserve | 172 venue reserve, 18 rarity cap, 9 spend cap |
| server refusals | 6 `duel_closed`, 2 `asset_locked` | 3 `duel_closed`, 2 `asset_locked` | 0 `duel_closed`, 2 `asset_locked` |
| sim score (rank) | 80.4 (1st of 8) | 32.9 (1st) | 79.3 (1st), in 240 ticks |

**Findings from the runs.**
- **The guardrails compose across the three processes.**
  - The effective floor held in every `/me` sample. Before the venue opened, taker buys stopped at 372 against
    the 370 floor; the opening then left 102 (≥ 100).
  - No tick ever booked two accepts.
  - No hold, kill-switch or ledger error occurred.
- **`duel_closed` (low, `duel run`, any policy).** When the rival accepts our offer, the payload still reads `live`
  until the next tick. On that tick our endgame accept is refused (6 in A, 3 in B), and that accept still books
  the team's one accept slot. Each one cost nothing here: the deal had closed at our better price.
- **`asset_locked` (low, maker).** The maker re-lists a card whose sale is still settling (2 per run), and each
  refused post uses one of the 12 listings for that tick.
- **Duel policy v2 inside the integrated loop (run C vs run A, same seed, the same 8 duels).**
  - Rounds per deal: v1 8.0, v2 0.12 ("silence is free": it holds while the rival concedes).
  - Our gain: v1 219 P, v2 244.5 P (+11.6 %; mean share 0.45 → 0.50).
  - `duel_closed` refusals: v1 6 in A, v2 0 (v2 does not counter, so the rival never accepts our offer mid-tick).
  - v2 had 0 planner failures and 0 guardrail refusals.
  - The lift against the simulator's bot is smaller than W2a/W2b's zoo lifts (1.4–1.55×). This is one seed at
    decay 0.06; it is not evidence against the gate.
- **Edge broker (#84, `BAZAAR_BENCH_POLICY=edge`) inside #71's keeper.**
  - It runs without errors: 0 refused, efficiency 1.0, as exact does on the simulator's static book.
  - It picks different pairs (b2: 5 pairs, quoted 126, vs exact's 4, 137); it cannot beat a 1.0 stall here.
- **Tick window.** At 3 s ticks the keeper's paced matches (0.2 s each, before the maker's offers) dropped 3 matches
  at the tick edge (5 sent). At 4 s ticks, 0 were dropped; at 30 s ticks, 0. Watch Sunday's 15 s ticks with many
  bench pairs.
- **r2's X15 (expired maker bids counted twice) was not exercised.** After the venue opened (cash ~100), the
  maker could not afford a bid, so none expired. Its bite is still open (below).

**r2's bite suite** on this tree (`tests/bites` from `night/r2-bite-hunter` @ d40426a):
- 41 passed, 36 xfailed (still open), 31 xpassed (fixed by the integrated PRs), 0 failed.
- On pass 1 (old heads) r2 counted 43 open and 22 fixed, so the current heads fix 9 more.
- Still open:
  - B3 accept slot (3);
  - B4 skipped ticks (4);
  - B6 restart mid-duel (3);
  - X15 expired-bid spend (2);
  - doors-open wake-up (2);
  - C1 request budget (4);
  - C2 fee at settlement (4);
  - A1–A3 restart orphans and lost replies (5);
  - sealed packs, silent failure, unsettled duplicate (1 each).
- The duel bites B2b and B3 fail identically on #86 alone (f6f4435), so the integration did not cause them.

## Moved after the freeze (checked 05:55)
| PR | frozen → now | what changed | effect on the fix-ups |
|---|---|---|---|
| #86 | f6f4435 → 9239070 | "a ledger outage holds every v2 duel instead of killing the duel tick (b5 rehearsal with #62)" | adopts **F5** on the PR |
| #79 | 9e99763 → 552490f | `_json_file` renamed `_payload_file`; "merge-proof sell context and dealer accept hold (for the #62 merge)" | covers **F8** and the `_sell_context(live)` resolution; F7 (`hands_off_ids` on #62's ledgers, `KW_ONLY`) still to check |
| #87 | 5b9deaf → 77f0777 | helper renamed `_plan_input` | F8 now cannot recur |
| #71 | ffb0877 → 1696789 | "no Postgres answer never ends the opening; mark only a listed venue; time-based backoff" | F2 (`stops=` in broker/venue contexts) still needed |
| #72 | e4efc82 → 30d005d | five dealer fixes (timeout close, lost answers, reopen) | F1 still needed (the `reserve` hook) |
| #84 | 50eb905 → 84ebb29 | rebased on #71 @ 1696789; unknown `BAZAAR_BENCH_*` ignored loudly | |

Re-run the rehearsal on the new heads before merging: `scripts/rehearsal/rehearse.py` then `analyze.py`
(about 20 minutes per run; the commands are in rehearse.py's docstring).

## Not integrated
Wave-2 PRs #89, #91–#119, #121–#130 (B-items, r1/r2, the teammates' feed reader, the holdings DB, and so on):
outside this rehearsal's frozen scope. For #100 (B12, on #81), its author's re-apply notes after #72: `may_close` →
`may_take`; `counter_below` targets `ask - neg.base_step`; `reopen_start` reads a jittered `bids[0]`.
Pass 1 (old #71/#72 heads, 4 fix-ups) is kept as `night/b5-rehearsal-pass1`.

## What Marius must decide
1. #71 as it stands: auto-opening a real venue at h6.5 with `cash_floor` 100 (vs. the 02:30 decision).
2. #62 before merging: every LIVE Railway service needs `DATABASE_URL` set to the shared Postgres, otherwise
   taker/maker/duels exit at start ("refusing to trade"). #71's opening also needs Postgres (the claim and the vault).
3. Fold the fix-ups into the second PR of each pair (table above), or merge this branch's fix-up commits.

