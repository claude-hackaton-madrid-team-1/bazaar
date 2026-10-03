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
