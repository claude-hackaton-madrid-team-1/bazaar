# SA1 — Sentinel autonomy: the Workshop, dealer sells on news, levels to agents

**Source:** local backlog (`.ai/specs/02-plan.md`). Omar, Sat 3 Oct ~19:15 Madrid: "Keep in mind Don Ernesto, the
taller and more things that the sentinel MUST be taking the news alone and preparing alone the agents to do it
without me saying it." And: "Making more money between trades don't really give us more points. YOU MUST FOCUS ON
THE scoring." And: "not to sell the album, [sell] each repeated [card] on our main collection".

## Why
- RULES.md scoring: the dealer ladder scores the share of each dealer's range we capture, our best three deals per
  level, a missing one counts 0, higher levels weigh more; a dealer SELL is a ladder deal too. Cash scores nothing.
- `/api/levels` (keyless, 19:10): The Workshop (`taller`) active since game hour 7.2; Don Ernesto (`banco`, L5,
  kind banker: buys only epics and legendaries) active since 9.4, open to all at 10.4. Nothing in our agents acted on
  either: a human had to say it.

## What
1. **The Workshop.** `POST /api/taller {"assets": [a, b, c]}` (the level's own `how`; the route is not in
   `docs/api/openapi.json`): three spare copies of one rarity become one card of the next; the pull is luck, never
   scored. `agents/taller.py` ranks triples of FREE spares (held − copies in our open offers or a recent sell − one
   kept per card): a pull that may fill a missing page slot first, then the highest dealer level that buys the
   result, then the cheapest to give up; never a triple worth more to us than the result's book. `guardrails.check`
   action `taller`: `taller_enabled` (false), `max_taller_per_game_hour` (2, this process), one free copy of each
   card kept (any set), the kill switch, and `max_score_loss_per_move` for copies a team trade brought us. The taker
   crafts at most one triple per tick once the level watch shows the level active; `bazaar taller [a b c] [--live]`.
2. **Dealer sells on news.** `dealer_sell_enabled` stays false. The maker's sell desk is made safe to turn on:
   candidates ranked by ladder scoring (a level with < 3 scored deals today first, then the highest level), a
   trickster's FINAL read as an ordinary bid, thread offers counted as busy copies, a copy that stops being a spare
   walks, a kill switch raised mid-step holds. Each property has a test (`tests/test_dealer_sell_readiness.py`).
3. **Sentinel → agents.** `level_watch.LevelWatch` diffs the `/api/levels` answer the news sentinel already reads
   once per window (no new request) and writes one learnings row (kind `announcement`) per level going active or
   open to all, naming the agent behaviour it enables. The taker asks `news.levels.active("taller")` each tick.

## Acceptance
- A1: no craft while `taller_enabled` is false, the level is not active, or the kill switch is on; one craft per
  tick at most, at most `max_taller_per_game_hour`; never a card's last free copy (tests/test_taller.py).
- A2: the readiness properties of the sell desk hold with the committed GUARDRAILS.md (tests/test_dealer_sell_readiness.py).
- A3: a level going active / open to all writes one learnings row, once (tests/test_level_watch.py).
- Gate: full `uv run pytest` (DATABASE_URL unset), ruff, ruff format, black, mypy, `scripts/sim_smoke.py`.

## #244 integration follow-up, 2026-10-04

The P1 merge-conflict review is resolved against main `ceef0f74`. Preserve TL1's shared hourly booking,
stronger CLI busy checks, fresh holdings reads, unnamed-settlement hold and event guard. Before-send asset
reservations survive exceptions; definite refusals release assets but retain the shared hourly booking.
Independent integration and security reviews found no additional P0-P2 issues.

### Honest Implementation Report

| Criterion | Status | Evidence |
|---|---|---|
| Preserve TL1 integration behavior | Verified | `tests/test_taller.py tests/test_taller_harden.py`: `43 passed in 0.62s`; `git ls-files -u`: empty |
| Reserve assets before send; release only on definite refusal | Verified | Same run, including `test_a_failure_after_the_post_still_promises_and_counts_the_craft` and `test_a_refused_craft_is_taken_back_and_rests`: `43 passed in 0.62s` |
| Dealer-sell interlock, copy validation and thread-input hardening | Verified | Same run, including guard and dealer-thread regressions: `43 passed in 0.62s` |
| Full required gate | Blocked | Full `uv run pytest -q` with coverage exited `134`, `Fatal Python error: Aborted`, in native psycopg; no completed coverage report |

Other gate evidence: ruff `All checks passed!`; format `672 files already formatted`; black `443 files would
be left unchanged`; mypy `Success: no issues found in 205 source files`; `bazaar rules` exit 0; sync script
`sync-ai-docs: regenerated`; README generator `README.md status block updated`; simulator `SMOKE PASSED in 55 s`.

Honest Implementation Metric: 3/4 criteria verified, 75%.

Unverified: full-suite completion, coverage, live Workshop response and settlement timing.
Could-not-do: obtain a green full test gate after the native abort without exceeding the user's one-run limit.
The branch must remain unpushed until that gate is completed.


## #244 final verification, 2026-10-04

Merged `origin/main` at `50832d70` in `f3c7f594`; preserved both memory appendices and regenerated README
and architecture output. Independent read-only review confirmed that #259 does not supersede the remaining
before-send reservations, dealer-sell interlock, copy validation, thread-input checks and output sanitization.
This verification clears the earlier push block. Tests ran with DATABASE_URL, BAZAAR_SIM and BAZAAR_ENV_FILE
unset, with no competing Postgres suite. No psycopg abort or retry occurred.

### Honest Implementation Report

| Criterion | Status | Evidence |
|---|---|---|
| Preserve TL1 integration behavior | Verified | Full suite: `5406 passed, 1 skipped, 2 xfailed, 42 subtests passed in 101.57s (0:01:41)`; `git ls-files -u`: empty |
| Reserve assets before send; release only on definite refusal | Verified | Same full suite includes both reservation regressions in `tests/test_taller.py` |
| Dealer-sell interlock, copy validation and thread-input hardening | Verified | Same full suite includes the guard and dealer-thread regressions in `tests/test_taller.py` |
| Requested gate | Verified | Ruff: `All checks passed!`; format: `683 files already formatted`; Black: `450 files would be left unchanged.`; mypy: `Success: no issues found in 208 source files`; sync: `sync-ai-docs: regenerated`; README check and rules: exit 0; port 8974: `SMOKE PASSED in 55 s` |

Honest Implementation Metric: 4/4 criteria verified, 100% for this requested gate.

Unverified: skipped and expected-failure cases, coverage percentage (not requested in this gate), live Workshop
response and settlement timing. Could-not-do: none for the requested merge, verification and branch push.
