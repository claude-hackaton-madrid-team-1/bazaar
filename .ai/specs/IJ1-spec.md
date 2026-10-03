# IJ1 — Prompt-injection attempts recorded with proofs

Source: Omar via the coordinator, 2026-10-03 ("save when another team's LLM or agent tries prompt injection, and
show it in bazaar-live with PROOFS"). Local backlog (no external tracker). The bazaar-live panel is its own PR.

## Rules check (vendor/bazaar-kit/RULES.md)
Prompt-injecting other agents is allowed and barely moves a negotiation. Reporting another team earns points only
if correct and costs points if wrong: this task only RECORDS. Nothing is sent to the game; no extra game request.

## Design
- Table `injection_attempts` (schema.sql = `injection_log.DDL`): world, tick, source (`feed`, `team_thread`,
  `duel`, `dealer_thread`, `offer_text`), event/thread/duel/message ids (0 when not applicable), from_team, to_us,
  tags, severity (`attempt` = a strong shape; `weak` = JSON, URL or a priced verb alone), the RAW text verbatim
  (only our own secret values cut, at most 2,000 chars), the folded text, our_response, `proof` (the endpoint and
  ids to check it against), seen_at. Natural key (world, source, ids, tags): every writer and the backfill agree.
- Tags: `llm.chooser.injection_flags` (NFKD-folded patterns, zero-width and homoglyph detection).
- Writers, all buffered in the tick and written after the sends (one bounded transaction, retry every 5 ticks):
  the taker (its feed window, our team-thread payloads the team desk read, our dealer threads), the duel runner
  (`/api/duels` messages not `from: "you"`).
- `bazaar injections [--backfill] [--json] [--weak] [--limit] [--us]`: the backfill reads feed_events, messages +
  threads and duels (read-only on the game). Output escapes markup and shows hidden characters as ⟨U+XXXX⟩.
- Schema setup runs before backfill reads or recorder ticks, in a separate transaction with 1.5 s lock and
  statement timeouts. Existing indexes are checked before DDL; backfill inserts acquire no schema locks.
- The read-only role (`bazaar_team_ro`) reads it through the default privileges (DataGrip).

## Acceptance criteria
1. Red-team payloads (override, role tag, sell-all, invisible, no digits, long) are recorded as `attempt`, raw verbatim.
2. Zero-width, combining-joiner, Hangul-filler and homoglyph hiding is tagged and kept visible in the raw text.
3. Organiser text and our own words are never recorded; JSON or a URL alone is `weak`.
4. A dealer message seen in the feed and in our thread read is one row (same key).
5. Writes happen after the tick's sends; a database failure never raises into the tick and keeps the buffer.
6. The backfill finds every channel once; a rerun adds nothing; the CLI lists ASCII JSON with proofs.
7. The read-only role can select the table.
8. Full gate and the sim smoke pass.

## Review repair report (#234, 2026-10-04)

Scope: the latest review's P1 merge conflict and two P2 index-lock findings.

| Criterion | Status | Evidence |
|---|---|---|
| Preserve main and IJ1 while resolving conflicts | ✅ verified | `git ls-files -u`: no output; `git diff --check`: exit 0. Main's guardrails, bench probe/capture and Workshop implementation remain unchanged. |
| Startup skips existing-index DDL and bounds missing-index lock waits | ✅ verified | `test_startup_and_live_flush_do_not_wait_for_backfill` and `test_missing_index_startup_has_a_bounded_lock_wait` passed in the full run below. The latter requires the failed connection to close within 2.5 s and proves a later open succeeds. |
| Backfill commits schema setup before reads/inserts and holds no DDL lock during storage | ✅ verified | `test_schema_setup_commits_index_locks_before_backfill` checks an idle transaction after setup. `test_startup_and_live_flush_do_not_wait_for_backfill` checks only AccessShareLock/RowExclusiveLock while storage is uncommitted and proves a live flush succeeds. Both passed below. |

Validation output, with `DATABASE_URL`, `BAZAAR_SIM`, and `BAZAAR_ENV_FILE` unset for the test run:

```text
uv run pytest -q --cov=src/bazaar_agent --cov-report=term:skip-covered
5375 passed, 1 skipped, 2 xfailed, 42 subtests passed in 159.08s (0:02:39)
TOTAL 30160 1354 96%
uv run ruff check .
All checks passed!
uv run ruff format --check .
677 files already formatted
uv run black --check src tests scripts
447 files would be left unchanged.
uv run mypy src
Success: no issues found in 207 source files
sh scripts/sync-ai-docs.sh
sync-ai-docs: regenerated (AGENTS.md inline; CLAUDE.md @import stub)
python3 scripts/readme_status.py
README.md status block updated
uv run bazaar rules
exit 0
BAZAAR_SIM_PORT=9234 uv run python scripts/sim_smoke.py
SMOKE PASSED in 55 s
```

Honest Implementation Metric for this repair: 3/3 = 100%.
Code and security reviews ran independently in parallel and found no further P0-P2 issues.
One worker owned the edits to avoid overlapping changes; the full Postgres suite ran once.
Unverified: deployed behavior; this repair made no production change.
Could-not-do: none within the requested repair scope.
