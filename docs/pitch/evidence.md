# Evidence to capture before scores freeze

Draft as of Sat 3 Oct, ~06:00 Madrid. Every command is taken from `src/bazaar_agent/cli.py` and the schema on `origin/main` at 9206093
(read, not run). Where something is on an open PR or unconfirmed it says so. **Run `uv run bazaar db tables` first**: it shows which
tables actually have rows.

## Ground rules

1. **Captured evidence stays out of git.** Write it under `.local/evidence/<UTC stamp>/` (gitignored). Only sanitised numbers go into
   `claims.md`. Never commit a transcript with a limit, a key, or `DATABASE_URL`.
2. **Never print `.env`.** Load it in a subshell: `( set -a; . ./.env; set +a; <command> )`. Never echo `DATABASE_URL` or `BAZAAR_KEY`.
3. **Read-only.** Nothing below writes to the game. Items that write to our own store are marked **W**.
4. **Respect the budget:** all our processes share 5 req/s per key and 6 live-stream slots. Capture serially, not in a loop.
   Do not start a second `monitor` or `feed capture` if one already runs.
5. The target is the real game unless `BAZAAR_SIM` is set. **Never capture evidence with `BAZAAR_SIM=1`**; check that each command prints
   `target: real game`.

## 1. When to capture

| When | Why | Sets |
|---|---|---|
| Saturday 12:00 (first draft) | Fill C13 candidates and Friday-vs-now numbers | §3, §4 |
| Right after any deal worth showing | The thread, settlement and trace are freshest, and the demo recording R1 is made now | §3, `demo.md` R1 |
| Saturday 18:00 (before Duels II) and 22:30 (before the 23:00 close) | Round scores, ladder, duels, a leaderboard snapshot | §4, §5 |
| Sunday 08:30 | Re-check every PENDING row | §4 |
| Sunday 13:30, before the final | Last snapshot, then freeze | §4, §5 |
| Immediately after the final / before 15:00 close | Everything in §5 that can disappear | §5 |

## 2. What can be lost (and why we capture it)

| Source | Risk | Capture |
|---|---|---|
| `GET /api/feed` | Capped at 500 events, no cursor; older events are gone | `bazaar feed capture` or `monitor` running; check `bazaar feed stats` |
| Team-only SSE events (`duel.message`, scope `team:t01`) | Not in the public feed; exist only in `.local/feed/team_events.jsonl` while `monitor --stream` runs | Copy that file |
| Finished duels | Only `GET /api/duels?done=true` | `bazaar duel done` (stores in Postgres) |
| Live thread transcripts | Reachability after the event is UNVERIFIED | `bazaar thread <id> --json` for each deal now |
| The leaderboard | A snapshot refreshed every few minutes; we keep no history; survival after the final is UNVERIFIED | `curl` it ourselves each time (§4) |
| Our `/api/me` | Live numbers only | `bazaar status`; Postgres `me_snapshots` keeps rows |
| Phoenix traces | Roots appear when a negotiation ends; retention UNVERIFIED | Screenshots and `bazaar obs spans` |
| Public `/state` and `/events` | In-memory rings (last 50 decisions / 200 events) reset on redeploy; not an archive | Screenshots; the archive is Postgres `decisions` / `executions` |
| `.local/` on a laptop | Gitignored, per machine | Copy it off the laptop after the final |

Doors (Madrid): Friday 19:00–23:00, Saturday 09:00–23:00, Sunday 09:00–15:00. Closing alone loses nothing, since the loops just wait; the
loss risks are the windowed feed, the leaderboard, and anything the organisers take down after the event.

## 3. Per-deal evidence set (do this for every candidate deal for slide 3 and the demo)

Pick the deal first: `thread_id` and `settlement_id` come from query A3 / A below. Then:

```sh
STAMP=$(date -u +%Y%m%dT%H%M%SZ); OUT=.local/evidence/$STAMP; mkdir -p "$OUT"
T=<thread_id>       # from query A3 below
( set -a; . ./.env; set +a
  uv run bazaar thread $T              > "$OUT/thread-$T.txt"      # the transcript, human readable (needs BAZAAR_KEY)
  uv run bazaar thread $T --json       > "$OUT/thread-$T.json"
  uv run bazaar curves --dealer <dealer> --threads 20 --ours > "$OUT/curves.txt"   # keyless; --ours needs our team id
  uv run bazaar tape --limit 30        > "$OUT/tape.txt"            # keyless; settlements incl. buyer, seller, price, fee
)
```

Then the SQL for the same deal (read-only; pass the URL through the environment, never on the command line):

```sh
( set -a; . ./.env; set +a
  psql "$DATABASE_URL" -c "\copy (select settlement_id, tick, venue, persona, buyer, seller, card_id, price, fee from tape where buyer='t01' or seller='t01' order by tick desc) to '$OUT/our-settlements.csv' csv header"
  psql "$DATABASE_URL" -c "\copy (select id, tick, payload from feed_events where type='settlement' and payload->'parties' ? 't01' order by id) to '$OUT/our-settlement-events.csv' csv header"
  psql "$DATABASE_URL" -c "\copy (select thread_id, dealer, item, opening_ask, final_ask, fill_price, outcome, steps, ticks from dealer_curves where ours is true or team='t01' order by thread_id) to '$OUT/our-dealer-threads.csv' csv header"
)
```

Notes:
- The team id `t01` comes from the docs. **Confirm** with `select distinct team from me_snapshots` before relying on it.
- `threads`, `messages` and `offers` tables are defined but no code inserts into them (UNVERIFIED). Use `feed_events` (type
  `thread.message`) and `bazaar thread <id>`.
- The transcript JSON may contain our bids and the dealer's text; it must not go into the repo. Quote only the lines on the slide.
- The Phoenix side: open the `negotiation` root for that thread (it exists once the negotiation ended), take a screenshot of the tree and
  of the `ladder_share` annotation, and note the `trace_id` from `outcomes` (query C3).
- A **settlement** from a dealer's "Deal!" on our bid lands at the tick boundary; our own accept settles at the next tick. If the
  settlement is not in `tape` yet, wait one tick. [C12]

## 4. Scoreboard and evals (capture each time in §1)

```sh
( set -a; . ./.env; set +a
  uv run bazaar clock                                                   > "$OUT/clock.txt"      # keyless
  curl -s https://bazaar.causaprima.ai/api/leaderboard                  > "$OUT/leaderboard-$STAMP.json"   # keyless; no CLI exists
  uv run bazaar status                                                  > "$OUT/status.txt"     # BAZAAR_KEY; cash, level, score, album
  uv run bazaar feed stats                                              > "$OUT/feed-stats.txt" # local file
  uv run bazaar db tables                                               > "$OUT/db-tables.txt"
  uv run bazaar duel done                                                                       # W: stores finished duels in Postgres
  uv run bazaar evals run --since-tick <N> --no-phoenix                                         # W: writes outcomes; use Phoenix on only if PHOENIX_API_KEY is set
  uv run bazaar evals report --json                                     > "$OUT/evals-$STAMP.json"
  uv run bazaar evals report                                            > "$OUT/evals-$STAMP.txt"
  uv run bazaar llm --last 50                                           > "$OUT/llm-choices.txt"
  uv run python -m bazaar_agent.jev report --json                       > "$OUT/jev-report.json"   # run on the host that holds .local/jev-decisions
)
```

What to read from them:
- `evals report`: `eval_scorecard` (per target and day: duels, dealers, ladder), `eval_ladder` (best three per level), worst five,
  `jev_calibration` (right / wrong / unknown per question). These fill **C30, C36, C41, C53**.
- The leaderboard JSON is the only place the other teams' scores live. Keep one per capture; there is no history anywhere else.
- `jev report` counts the decision log per question on **that machine**. Each Railway service has its own volume, so run it where the
  agent runs, or use the `decisions` table (query E).
- Friday's baseline (C30, C36) is already in the database; re-run `evals report --json` and compare the `fri` rows to the new `sat` and
  `sun` rows. It is not a controlled comparison (different dealers, rules, rivals), and the slide must say so. [C41]

SQL for time series and refusals (all read-only):

```sql
-- C3. Eval rows with the trace to open in Phoenix
select target, subject, score, label, day, recorded_tick, realized_surplus, ladder_share, jev_question, jev_verdict, jev_right,
       trace_id, span_id, annotated_at, explanation
  from outcomes order by recorded_tick desc nulls last;

-- D. Our score and cash over time (no leaderboard table exists)
select tick, cash, level, score from snapshots order by tick;
select tick, read_by, cash, level, score, read_at from me_snapshots where world = 'real' order by tick;

-- B. Guardrail refusals / rejected decisions (what the controls stopped); reason may show [redacted] (C55)
select id, tick, agent, kind, status, dry_run, reason, policy_checks->>'guardrail' as guardrail, thread_id
  from decisions
 where status in ('rejected','failed','expired') or policy_checks->>'allowed' = 'false'
 order by id desc limit 200;
select agent, kind, status, dry_run, count(*) from decisions group by 1,2,3,4 order by 5 desc;

-- B3. Game-side refusals (429, persona_quota, insufficient_cash, ...)
select e.tick, e.sdk_method, e.error_code, d.agent, d.kind
  from executions e left join decisions d on d.id = e.decision_id
 where e.error_code is not null order by e.id desc limit 200;

-- E. Jev decisions with floats. Run `select jev from decisions where jev is not null limit 1` first to confirm the key names (UNVERIFIED)
select id, tick, agent, kind, status, jev->>'verdict' as verdict, jev->>'value' as value,
       jev->'probabilities' as probabilities, jev->>'reason' as undecided_reason
  from decisions where jev is not null order by id desc;
select jev_question, jev_verdict, jev_right, count(*) from outcomes where jev_question is not null group by 1,2,3;

-- F. Feed completeness
select min(id), max(id), count(*), min(tick), max(tick) from feed_events;
select type, count(*) from feed_events group by 1 order by 2 desc;
```

## 5. Deceptive-offer evidence (proof 2)

| Evidence | Command | Needs | Notes |
|---|---|---|---|
| The bait test, recorded | `uv run pytest tests/test_accept_gate.py tests/test_inspector.py -v` | #146 merged | SIMULATED. Record it (`demo.md` R2) |
| Hostile text suite | `uv run pytest tests/test_hostile_text.py -q` | #152 merged | SIMULATED |
| Red team | `uv run pytest tests/test_redteam_injection.py -q`; report `docs/night/w5w6-score-redteam-morning.md` §2 | #78 merged | 168 cases. Not 129 |
| The hook | `uv run pytest tests/test_runtime_hooks.py -v` | on main | |
| 0 false flags on real offers | `uv run bazaar flags precision --json` on the Friday capture | #146/#152 | REAL data through new code |
| **A real one, if it happens** | In the taker log and `decisions` (query B): a `refused` row with the inspector's finding; then `bazaar thread <id> --json` for the offending thread | #146 live | **Capture the instant it happens**: thread, decision row, the dealer's text, the structured offer. This turns C21 REAL |

Do **not** capture `decisions.jev` for a hypothetical input as if it were observed (the 0.83 flag-enable answer was computed on an
invented Level 4 case).

## 6. Measured-improvement evidence (proof 3)

| Evidence | Command | Needs | Notes |
|---|---|---|---|
| Baseline, Friday duels | `uv run bazaar evals report --json` → `eval_scorecard` target `duel`, day `fri` | DB | C30 |
| Simulator v1 vs v2 table | `uv run python scripts/duel_sim_proof.py run --label v2 --decay 0.08 --sessions 16 --out .local/duel-proof`, then `... table .local/duel-proof` | #150 + #151 merged; a free port (patch `LOCAL_SIM_URL`) | SIMULATED. Another worker's simulator may own 8765; never kill it |
| 16,800-duel tournament + 12-duel replay | `uv run python scripts/duel_zoo.py` | #151 | Offline, no network. SIMULATED |
| Ladder replay (W3) | `uv run bazaar ladder floors --source feed` | the W3 branch (#81 is closed, not merged) | Not shipped; chart only |
| The learner on real data | `uv run bazaar learnings --lessons --save`, `--query "..."`, `--policy` | #89 → #96 → #112 | C38, C39 |
| Real-game delta | `evals report --json`, `sat`/`sun` rows vs `fri` | DB | C41; only the evals may fill it |

## 7. Capture checklist (copy into the day's notes)

```
[ ] clock + leaderboard + status + evals report saved (stamp: ______)
[ ] feed stats: no GAP POSSIBLE; team_events.jsonl copied
[ ] duel done run; finished duels present in `duels`
[ ] each deal for the deck: thread json + settlement row + Phoenix screenshot + /state screenshot
[ ] recording R1 made (real deal), watched once, no secret in frame
[ ] recording R2 made (bait refused)
[ ] PENDING rows in claims.md re-checked against `gh pr view <n> --json state`
[ ] .local/ copied off the laptop (after the final)
```

## 8. Not verified

- Whether `threads`, `messages`, `offers` are ever filled (no insert found); the exact keys inside `decisions.jev`; `bazaar traders`;
  whether `db load` also loads `dealer_curves`; Phoenix retention and the live filter names; whether the game API and the leaderboard stay
  up after the final; whether #4 and #5 of bazaar-live are deployed.
- `evals score-sim`, `cockpit` and `timeline` are not on main (PRs #78/#128, #102). Do not put them in a runbook until merged.
