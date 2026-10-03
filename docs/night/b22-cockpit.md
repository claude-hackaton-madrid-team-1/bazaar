# B22: `bazaar cockpit`, Saturday's read-only operator screen

Night of 3 Oct 2026, 04:14–. Draft PR stacked on `night/b6-saturday-playbook` (#102). Nothing touched the live game:
the command was run only against the in-process simulator and fixtures.

## What it is
`uv run bazaar cockpit` prints one screen. `--watch` refreshes every `--every-ticks` ticks (default 2), at
mid-tick. `--json` prints the same panels as data. Each line carries `ok`, `WARN`, `BAD`, or nothing (info), and
the header carries the worst of them.

| Panel | Shows | WARN / BAD when |
|---|---|---|
| Clock | doors, tick, game hour, tick length, round, the limits in force, next opening | doors closed |
| Next (playbook) | the next 4 events from B6's timeline, with wall time and minutes to go. While the doors are closed: the resume column first, the jump time beside it. Each event's play, first sentence | – |
| Cash | cash; open bids, counted as the guardrails count them (`seller.open_commitments`: open and queued; every list in `/api/me/offers` plus our dealer-thread offers, one per offer id; unreadable → headroom BAD, never a guess); the floor (`cash_floor`, plus #71's `venue_bond_reserve` while no venue is open, if that rule exists); headroom; spend and packs in the last game hour (ledger); sealed packs (r2 X21) | headroom < 30 P / < 0; spend at the cap; packs at the cap; sealed packs; `trading_enabled = false`; this checkout's PAUSE |
| Ledger | where it is (Postgres, shared; or the file, this machine only); accepts and listings this tick; **which processes wrote the shared ledger in the last 120 ticks** (`ledger.source`) | the file ledger (BAD); a shared ledger nobody writes |
| Agents | each `/health`: mode (live or dry), target, tick lag behind the server | unreachable, `ok: false`, or > 2 ticks behind with the doors open |
| Caps | open offers and threads vs the clock's limits; each open thread | at the cap (BAD), one below (WARN) |
| Duels | live duels sorted by deadline: role, item, issues, ticks left, rounds, the rival's price | ≤ 2 ticks left and we have not offered |
| Ladder | `ladder_points`, the official deal count; our settled dealer deals per dealer and level (price) | fewer than 3 deals at a level |
| Market Test | our venue, `bench_points` / `bench_efficiency` / `bench_venue` (gate G3), the next session | – |
| Alerts | the monitor's last 5 alerts (`alerts.jsonl`) | – |

## Read-only, by construction
- **Game:** only GETs. Keyless: `/api/clock`, `/api/schedule`, `/api/dealers`. With the key: `/api/me`,
  `/api/me/offers`, `/api/me/threads`, `/api/duels`. `--no-key` drops the four keyed reads.
- **Postgres:** `pgconn.connect` with `default_transaction_read_only = on`. It never calls `db.connect_ready`, which
  runs the schema DDL (and its ALTER could queue behind other sessions' locks, as r1 found on #91). With no
  Postgres, it reads this machine's JSONL ledger.
- **Agents:** `GET /health` on the public Railway URLs (docs/services.md). Override with `--health name=url`.
  bazaar-duels has no HTTP route, so its panel line points at `railway logs`.
- **Test:** `test_the_command_against_a_local_simulator_reads_only` runs the real command over HTTP against the
  in-process simulator, with an open dealer thread and live duels. It asserts that the simulator's offer and thread
  counts are unchanged afterwards.
- **One source fails, the rest still renders:** each source is read on its own, and a failure is shown as `BAD`
  with its error (tested).

**Request cost per refresh:** 3 keyless + 4 keyed GETs. With `--watch` every 2 ticks at 30 s, that is 4 keyed
calls per minute, about 0.07 req/s against the 5 req/s key budget. The calls go out mid-tick, away from the
tick-edge burst that W5 measured.

## Sample (fixtures, 08:55, doors closed at h 2.65; made-up team numbers)
```
Team 1 cockpit · Sat 08:55:00 · overall WARN
[-] Next (playbook)
        h4 Saturday opens: Sat 09:00 (resume, in 5 min) · jump 09:00 · Doors open at 09:00 on the calendar …
        h3 The Market Test: Sat 09:21 (resume, in 26 min) · Market Test: 16 ticks (8 min at 30 s)
        h4 Saturday · Gran Vía: Sat 10:21 (resume, in 86 min) · jump 09:00 · Round 2 starts: Saturday's round …
[WARN] Cash
        cash: 353 P · open bids 35 P
    ok  headroom: 48 P above the floor after open bids
  WARN  sealed packs: 1 (assets 425): open them by hand
[ok] Ledger
    ok  where: postgres ledger table (shared across machines)
        writer taker: last row at tick 158 (1 ago)
```

## Verdict
| Criterion (B22's line) | Result |
|---|---|
| One read-only command with every item in the backlog line | **GO**: 10 panels |
| Fixture-driven tests, no writes | **GO**: 12 tests (11 pure on fixtures, 1 end-to-end on the local simulator with a no-write assertion); suite 851 passed, ruff/black/mypy clean |
| Never touched the live game | **GO**: run only on fixtures and `127.0.0.1` |

## Limits and risks
- **Ladder:** the panel shows our deal prices, not W5's "share of the dealer's range", which needs every team's
  fills (`bazaar evals score-check`, #78). It also does not separate this round's deals from Friday's.
- **Agents:** the duel loop has no `/health`. Railway's PAUSE files live inside the containers, so the cockpit sees
  only this checkout's.
- **Ledger:** it tells whether the cockpit's own machine reaches the shared ledger, and which sources wrote it.
  It does not tell whether a given Railway service has `DATABASE_URL` set. A service missing from the
  writers during play is the hint.
- **Merging** touches `src/**`, so it redeploys the live services: merge only in a window (B6 § 3 step 8). The
  cockpit runs from this branch without any merge.

## For Marius
- Run it on the laptop that runs the monitor: `uv run bazaar cockpit --watch`.
- Nothing to decide: it changes no behaviour and adds no guardrail.
