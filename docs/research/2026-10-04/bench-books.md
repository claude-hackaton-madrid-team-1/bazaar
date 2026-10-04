# Why `bench_books` is empty in prod (Sun 4 Oct 2026)

Symptom: the dashboard's `/venue` page shows no synthetic Market Test orders because `bench_books` has no rows in
prod. Question: is the recorder missing from the deployed maker, or failing?

## TL;DR

- **Neither.** The recorder was merged and deployed **after Saturday's last Market Test**, and the maker ran **no
  game tick** between that deploy and this morning. `bench_books` is empty because the code has never had a bench
  book to record. No bug in the writer or the reader.
- Writer: commit `39bb2932` (Sat 00:09 local), merged in **PR #261** (`2c8b726d`, Sun 00:17 local = 22:17 UTC Sat).
  First maker deploy that carried it: `5cec6578` at 22:17 UTC Sat. Saturday's last `bench.started` (session 6, run
  b103) was at **20:37 UTC**, 1 h 40 min earlier.
- Deployed now: bazaar-maker runs `16ecba32` (PR #269, 23:58 UTC Sat), which contains the writer;
  `git diff 16ecba32 origin/main -- src` is empty. **No redeploy is needed.**
- End to end checked on a throwaway local Postgres 18 (prod runs 18.6): 44 real `GET /api/broker/book` snapshots of
  Saturday's run b52 went through the real `BenchBooks` writer (66 rows, 9 traders, no failure) and bazaar-live's
  own `show.venue_books` view (from its `db/venue.sql`) read them back as one run with 9 traders.
- Change in this branch: **tests only.** A real-shaped b52 broker-book fixture and two tests (parser, and the
  broker → recorder path). No `src` change, so nothing to redeploy for this.

## Method and data windows actually seen

- Code: `origin/main` at `235f296e` (`git grep bench_books`, `git log -S bench_books`).
- Prod Postgres: read-only `SELECT`s only (session `default_transaction_read_only = on`), Sun 07:16 UTC.
- Railway: `railway deployment list --service bazaar-maker --limit 200` and `railway logs -s bazaar-maker <id>`
  for every maker deployment from `d2a931f7` (19:12 UTC Sat) to the current `0ccfdca6`, plus the current
  deployment's logs up to 07:23 UTC Sun. Read-only; nothing linked or changed (used an already-linked scratch dir).
  Each Saturday deployment's log was complete for its lifetime (10–125 lines); earlier Saturday deployments not
  read (not needed: the writer did not exist then).
- Keyless game endpoints: `/api/schedule`, `/api/clock` (Sun 07:20 and 07:24 UTC).
- bazaar-live (read only): `origin/main` `f70d784` (PR #47, merged Sun 09:11 local), `db/venue.sql`.
- Real bench payload: 44 `GET /api/broker/book` snapshots of run b52 (Sat session 3, ~10 s apart), captured
  read-only by the Saturday mm-deep session (`books_s3/`, cited in `_night/MM_DEEP.md` §3).

## Findings

### 1. Writer: what records, when it was merged, whether the table exists in prod

- Schema: `src/bazaar_agent/sql/schema.sql:355` creates `bench_books` (key `world, run, tick, offer_id`), applied by
  `db.init_schema` (every long-running writer applies it on connect).
- Insert: `src/bazaar_agent/agents/bench_capture.py` (`BenchBooks.record` → bounded queue → daemon worker →
  `insert … on conflict do nothing`, 1.5 s statement timeout, failures logged once by exception class).
- Caller: the maker's broker only. `BrokerAgent.on_tick` (`src/bazaar_agent/agents/broker.py:348`) records
  `book.bench_offers` after its sends; `VenueKeeper._bench_books` (`src/bazaar_agent/agents/venue_keeper.py:333`)
  gives it a Postgres connection (`db.connect(app="bazaar-bench-books", connect_timeout_s=3)`) for the real game.
  `bench_probe.py` and no other service write it.
- Merge: `git log -S bench_books origin/main` → one commit, `39bb2932 feat: record the full Market Test bench book
  each tick` (2026-10-04 00:09 +02:00), first reached main through **PR #261** (`2c8b726d`, 00:17:30 +02:00).
- Prod (read-only):

  | query | result |
  |---|---|
  | `select table_name from information_schema.tables where table_name='bench_books'` | exists |
  | columns (`information_schema.columns`) | world, run, tick, offer_id, side, quote, venue, fee_bps, fee_per_card, offer, read_at: same as `schema.sql` |
  | `select count(*), min(read_at), max(read_at) from bench_books` | `0, null, null` |
  | `select n_tup_ins, n_live_tup, n_tup_del from pg_stat_user_tables where relname='bench_books'` | `0, 0, 0`: never an insert (since the stats were last reset) |
  | `pg_tables.tableowner` / grants | owner `postgres` (the role the maker's ledger/decisions writes already use); `bazaar_team_ro` has SELECT |

### 2. Deployed: which commit the maker runs, compared with the merge

| maker deployment | created (UTC) | commit | carries writer | game ticks run |
|---|---|---|---|---|
| `1d93e168` | Sat 20:17 | `4e856783` (PR #252) | no | yes; ran Saturday's last bench b103 (ticks 1401–1416, 4 pairs) |
| `db646f52` | Sat 20:49 | `dd19e189` (PR #255) | no | yes, until the venue closed |
| `5cec6578` | Sat 22:17 | `2c8b726d` (**PR #261**) | **yes, first** | none: "waiting, doors closed, paused" |
| 9 more, to `0c7517c3` | Sat 22:18–23:47 | PRs #257 … #270 | yes | none (10–11 log lines each) |
| **`0ccfdca6` (current)** | Sat 23:58 | **`16ecba32` (PR #269)** | **yes** | none until 07:20 UTC Sun |

The current maker logged only startup and `maker: waiting, doors closed, paused; next opening 2026-10-04T09:00`
until 07:00 UTC, then `waiting, doors open, paused`. At **07:20:10 UTC** (tick 1466): `venue: broker on for v19
(LIVE), bench exact`: the broker, and with it the `BenchBooks` recorder, exists only from that tick (it is built
lazily on the first live tick, `venue_keeper.py:345–371`). The prod table has `venue_broker_keys` row `v19` (key not
read), so the broker can start; it did.

Feed: the last `bench.started` row is session 6 at tick 1401, `received_at` Sat 20:37:57 UTC
(`select type, tick, received_at, payload->>'session' from feed_events where type like 'bench.%' order by id desc`).
No Market Test has run since the writer shipped.

### 3. Failing? Logs, config, and the payload shape

- No `bench books:` line (the recorder's only log, emitted on a failure) in any maker log since the writer shipped;
  no exception, no DB error. `DATABASE_URL` is set on bazaar-maker (`ledger: postgres ledger table on
  postgres.railway.internal:5432` at every start; `pg_stat_activity` shows `bazaar-maker`, `bazaar-writes-maker`,
  `bazaar-holdings-maker` connections).
- No feature flag: the recorder runs whenever the broker reads a non-empty `bench_offers`, in every bench policy.
  World is `"real"` (`holdings.scope_of` → `REAL`), the value the dashboard filters on.
- Shape: a real b52 book (fixture `tests/fixtures/broker_book_bench_b52.json`, one snapshot, its `offers` and
  `recent` were empty) has `bench_offers` items `{id: "b52-2", bench: true, maker: "bench", give: {cash, assets,
  types}, want: {cash, assets, types: ["bench:cromo"]}, expires_tick}`. A buyer bids `give.cash` with `want.cash: 0`;
  a seller asks `want.cash`. `bench_capture.side_and_quote` reads exactly that (`want.cash` truthy → sell, else
  `give.cash` → buy) and `run_of` splits `b52-2` → `b52`. Saturday's live logs show the same id form
  (`match bench: sell b103-11 (ask 95) × buy b103-1 (bid 102)`). RULES.md §Market Test: "It appears in your book as
  `bench_offers`."
- End to end on local Postgres 18 (`docs/research/2026-10-04/bench-books/e2e_local_pg.py`): 44 snapshots → 23
  non-empty ticks → **66 rows, 9 distinct traders, ticks 680–705, zero failure log lines**; the sides and quotes
  match the capture (b52-2 buy 42 → 56 as it relaxed, b52-10 sell 62 → 55).

### 4. Reader side (bazaar-live `/venue`)

`db/venue.sql` (bazaar-live `origin/main`, PR #47) builds `show.venue_books` from `public.bench_books` with
`where b.world = 'real'`, grouping by `run, offer_id` and reading `side, quote, tick, read_at, venue, fee_bps,
fee_per_card`; it never selects `offer`. Every column exists and is filled by the writer with those names and
values. Applied locally (its `db/show.sql` then `db/venue.sql`) on top of our schema, after the replay:
`select run, ticks_seen, jsonb_array_length(offers) from show.venue_books` → `b52 | 23 | 9`. The six `show.venue_*`
views exist in prod (`information_schema.views`). No mismatch. (Its `session` column joins `bench.started` feed rows
by tick and time; locally null because the local feed table was empty; prod has those rows.)

## Fix

No code fix is needed: nothing is broken. This branch (`fix/bench-books-recorder`) adds only:

- `tests/fixtures/broker_book_bench_b52.json`: one real Saturday broker book (synthetic organiser data only).
- `tests/test_bench_capture.py`: `test_a_real_bench_book_becomes_one_row_per_trader` (the real shape → the exact
  rows, raw offer kept whole) and `test_the_broker_records_a_real_bench_book_for_postgres` (the real book through
  `BrokerAgent.on_tick` → the Postgres rows). The existing tests used a hand-made buyer without `bench`, `types` or
  `want.cash: 0`.

## Deploy steps for Marius

- **None.** bazaar-maker already runs `16ecba32`, which has the recorder, and its broker came up for v19 at
  07:20 UTC. Do not redeploy for this.
- **Merging this branch redeploys bazaar-maker**: every non-`[skip ci]` merge to main has produced a new maker
  deployment (deployment list above). It is tests-only, so it gains nothing in prod. If merged during the game, do
  it in a gap between benches only: per `/api/schedule` (keyless) the Sunday benches are at game hours **14.65**
  (hard Market Test, 16 ticks), **15.0** (16 ticks) and **17.0** (16 ticks); a tick is 15 s (`/api/clock`
  `tick_seconds`). A maker restart inside a bench loses that session's matching. Safest: merge after the game.

## Risks

- **First connect inside the bench.** The recorder opens its Postgres connection lazily, on the first non-empty
  bench book. A connect failure there skips the next 5 batches (`RETRY_EVERY = 5`), up to 5 of 16 ticks; the JSONL
  on the maker volume (`<stats_dir>/bench_books.jsonl`) still gets every tick.
- **A slow DB drops rows, never ticks.** The queue holds 8 ticks; beyond that rows are dropped (logged once as
  `bench books: queue failed (RuntimeError)`). Fine at 15 s ticks.
- **Only what the broker reads.** One row per offer per tick our broker read the book; an offer that arrives and is
  matched within one tick never appears. A tick whose book read fails or whose window is closed records nothing.
- **Silent success.** A successful write logs nothing, so from the logs "never ran" looked the same as "failing".
  Follow-up after the game (a `src` change, so a redeploy): log once per run, e.g. `bench books: recording run b52
  (Postgres + JSONL)`.

## How to verify in prod after the first Sunday bench

Logs (`railway logs -s bazaar-maker`): lines `tick N broker: bench book bXX-1:s62 bXX-4:b35 …` during the bench,
and **no** `bench books: … failed` line.

SQL (read-only):

```sql
select run, count(*) as rows, count(distinct offer_id) as traders, count(distinct tick) as ticks,
       min(tick), max(tick), min(read_at), max(read_at)
  from bench_books where world = 'real' group by run order by min(read_at);

select run, day, first_tick, last_tick, ticks_seen, venue, session, jsonb_array_length(offers) as traders
  from show.venue_books order by first_tick;
```

Expected: one run per bench (10–12 traders, close to 16 ticks seen), `venue = 'v19'`, `session` set.

## Prod verification

Pending at the time of writing (07:30 UTC): the first Sunday bench (game hour 14.65, ≈ 08:16 UTC with 15 s ticks
running 1:1 since the unpause) had not started. The queries above are run read-only after it; the result is added
here in a follow-up commit.

## Could not verify

- Whether bazaar-live has deployed PR #47's `/venue` page (I checked the SQL and that the views exist in prod, not
  the dashboard service).
- Whether the bench policy is the one intended for today: the current maker logs `bench exact`, while the Saturday
  night notes expected `BAZAAR_BENCH_POLICY=probe`. Out of this topic; flagged for Marius.
