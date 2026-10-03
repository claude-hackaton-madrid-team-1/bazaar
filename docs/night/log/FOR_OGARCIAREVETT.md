> Sanitised copy of `_night/FOR_OGARCIAREVETT.md` (night shift of Fri 2 → Sat 3 Oct 2026, file state at ~07:15 Madrid).
> `[private]` marks redacted private game values (cash, card values, affinities, our price caps and bid
> ladders, album state). Numbers inside test descriptions (`tests/bites/*`, chaos runs, proof tests) are
> fixture values, not our real ones. See [../SUMMARY.md](../SUMMARY.md) and [../INDEX.md](../INDEX.md).

# Notes for ogarciarevett's coordinator (from Team 1's night shift, 2026-10-03)

His coordinator closed several night PRs overnight and salvaged them into #137–#157. These gaps remain:

1. **#145 (salvage of B26)** carries the PRE-review version. Missing fixes are on night/b26-sunday @ 95c9b47 (ab26c6e):
   - A zero-minted card that a dealer mints on demand must not count as scarce (`supply.scarce` False, scarcity urgency 0). Otherwise, with the switch on, ~12 new-set commons fill `max_moves` and push out genuinely scarce buys.
   - `test_the_strategy_file_keeps_todays_default_for_dealer_mints_unminted` pins the live STRATEGY.md value, so flipping the switch on Saturday evening turns CI red. Parse the file with the switch on/off/absent instead.
2. **#154 (salvaged reports):** `docs/night/b25-verify.md` predates the review fixes (bbaa5bd). Refresh it from night/b25-verify's tip. The `bazaar verify` CODE was not salvaged; it lives only on night/b25-verify (#132 closed).
3. **B2's code was not salvaged** (`broker probe --auto`, `evals/saturday.py`, the keeper's bench log line); it lives on #84 / night/b2-venue-runbook. #154's runbook doc is current.
4. **#105 merged over r1's BLOCKER**: `holdings_from_db` defaults ON and changes the live taker/maker/MCP decision inputs. Verify on Railway or revert (see REVIEWS.md).
5. **#71**: r1 says do not merge as is (process blocker; it opens a 270 P venue at h6.5 and moves cash_floor 270 → 100; conflicts with main in status.py). See REVIEWS.md.
6. **#89**: learning taker defaults ON → the live taker starts skipping dealers on redeploy (r1 HIGH).
7. **#58 test**: `tests/test_evals_db.py::test_the_cli_imports_a_duel_runner_log` expects "upserted" but the CLI prints "imported" (fails only with Postgres). **#59 replay**: if the listing row is written but the spend row fails, the retry writes the listing again; and `pending-ledger.jsonl` is never read back after a restart.
8. **#140–#143 are takeovers of our #114/#116/#126/#133 (closed).** Our session pushed r1's review fixes onto the closed branches AFTER the takeover (06:10, stack rebased on #72 @ 2f01a6f): #114 → undriven threads watched and booked after the first tick, a once-per-thread `dealer_closed` booking (partial unique index in schema.sql + JSONL file lock) so two overlapping takers book a deal once, watched threads read newest-first and given up after 5 refused reads; #116 → a 5xx keeps the slot and books the spend, and a read is re-sent once after a 429. Port these into #140/#141 (branches night/b17-restart-orphans, night/b18-rate-limits and stacked) before the 09:00 merge, or merge our versions instead.
