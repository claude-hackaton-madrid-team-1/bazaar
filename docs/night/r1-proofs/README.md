# r1 proof tests

Each file is a pytest module that proves a review finding against the PR head named in its first line. They
live here, not in `tests/`, because they import code that only exists on that PR's branch. To run one:

```sh
git worktree add --detach /tmp/pr origin/<pr-branch> && cp docs/night/r1-proofs/<file>.py /tmp/pr/tests/test_r1_proof.py
cd /tmp/pr && uv sync && env -u DATABASE_URL -u BAZAAR_API_KEY uv run pytest -q tests/test_r1_proof.py
```

| File | PR @ head | Proves | Expected today |
|---|---|---|---|
| `pr71_e82ba8d.py` | #71 @ e82ba8d | starter stall drops the floor to 100; reopen after restart; a landed-but-timed-out open loses the broker key | all pass (bugs) |
| `pr71_e489449.py` | #71 @ e489449 | B fixed; C (lost key), D1/D1b (auto-reopen after a lost answer), D2 (one Postgres blip locks the vault out for the life of the process) | B fails, C/D pass |
| `pr62_x_pr79_hands_off.py` | #62 merged with #79 | `PgLedger`/`FallbackLedger` lack `hands_off_ids` after the merge (the maker raises every tick) | pass once the patch in REVIEWS.md is applied |
| `pr60_rival_price_int.py` | #60 @ c6ccda4 | a non-integer rival price is truncated before the inside-limit check | fail on c6ccda4 (bug), pass when fixed |
| `pr93_flag_precision.py` | #93 @ e93b3bd | honest substitutions never flagged; tricksters flagged (foil case is a documented miss) | pass except the foil case |
| `pr93_dealer_sell_guard.py` | #93 @ e93b3bd | `dealer sell` holds under the kill switch and books the sale as negative spend | pass |

## Second batch (takeovers and new PRs, 05:50 to 07:00)
Each file's docstring or first test names its target. `proof_` files pass while the bug is present, unless they say otherwise.
| File | Target | Proves |
|---|---|---|
| `proof_x105_pr71_stall.py` | main (#105) + #71 | a stripped `starter_broker_key` makes the free stall read as our venue (fixed at #71 50edddb) |
| `proofs_pr71_delta2.py` | #71 @ 1696789 | D2' fixed; D3, D4, D5 bugs (D3/D4 fixed at 50edddb, D5 open) |
| `proof_pr158_thread_slots.py` | #158 @ 7819ae9 | lift 0 opens threads by cash room instead of slot count (fails = bug) |
| `proof_pr113_days.py` | #113 @ 784a224 (also #150/#159) | days-latch HIGHs (fixed at 52a590a; the done-GET case now comes from main's DuelStore) |
| `proof_pr114_restart.py` | #114 / #140 | late deal after restart; two takers double-book (#140 still 36 vs 18) |
| `proof_pr116_5xx.py` | #116 / #141 | a 5xx released the slot (fixed; expected spend 22 = ask + fee) |
| `proof_pr131_dealer_bluffs.py` | #131 @ 40535d9 | bluffs to a price-only dealer never stop |
| `proof_pr137_outage.py` | #137 @ 8ab71d5 | ledger-outage ticks count toward max_ticks (timeout instead of deal) |
| `proof_pr123.py` | #123 @ 128d56d | card/ref/fee on public /state |
| `proof_pr142_lapse.py` | #142 / #143 | pre-restart bid phantom spend; two makers refund one lapse twice |
| `proof_pr159_b7.py` | #159 / #150 | B7 one-accept-per-tick (3 pass); stranded slot, extra v1 read, missing Jev context (3 fail) |
| `proof_b17_*.py`, `proof_b18_compose_tracked.py` | night/b17 @ 16905c9, night/b18 @ 8424880 | port evidence for #140/#141 |
| `b18_on_72_sdk_resolution.diff` | #141 onto #72 | TeamBazaar(TrackedBazaar) composition (1,059 pass) |
