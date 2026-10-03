# Bazaar · Team 1

Our entry to the Causa Prima hackathon (Madrid, 2–4 Oct 2026): an autonomous agent that trades
Madrid cards in [The Bazaar](https://bazaar.causaprima.ai). It reads the live game the way a
trading system reads market data, remembers every trader and price, learns from it, asks Jev for a
verdict, and leaves execution to a separate runtime that only acts on structured decisions.

**Python only** (3.12 + `uv`). The organisers' SDK is vendored in `vendor/bazaar-kit/` and used
first; raw HTTP against `docs/api/openapi.json` is Plan B only.

## Live services

| Service | URL |
|---|---|
| Taker | https://bazaar-taker-production.up.railway.app · `wss://bazaar-taker-production.up.railway.app/events` |
| Maker | https://bazaar-maker-production.up.railway.app · `wss://bazaar-maker-production.up.railway.app/events` |
| Phoenix | https://phoenix-production-6aa3.up.railway.app |
| Simulator (a fake Bazaar for tests, key `sim-team1`) | https://bazaar-sim-production-1d48.up.railway.app · see [Simulator](#simulator-test-every-agent-while-the-game-is-closed) |

Endpoints, event envelope and examples for the dashboard: [`docs/services.md`](docs/services.md).

## Simulator (test every agent while the game is closed)

`bazaar-sim` is an HTTP API that behaves like `https://bazaar.causaprima.ai`: the same routes, the
same JSON shapes (checked against `docs/api/openapi.json` and the captured fixtures), the same rules.
The vendored SDK and every `bazaar` command work against it unchanged. It never talks to the real
game. Public instance: **https://bazaar-sim-production-1d48.up.railway.app** (`/api/health`, `/api/clock`, `/sim/state`).

**The target is one flag, `BAZAAR_SIM`, over hardcoded URLs** (`src/bazaar_agent/config.py` decides,
every client goes through it: CLI, agents, runtime, MCP server, monitor):

| `BAZAAR_SIM` | Target | Key |
|---|---|---|
| unset or `0` | the real game, https://bazaar.causaprima.ai | `BAZAAR_KEY` (the team slip) |
| `1` | the simulator, https://bazaar-sim-production-1d48.up.railway.app | `BAZAAR_SIM_KEY` (default `sim-team1`) |
| `local` | a simulator on this laptop, http://127.0.0.1:8765 (`uv run bazaar-sim serve`) | `BAZAAR_SIM_KEY` |

Every command prints its target first (`target: real game …` or `target: SIMULATOR …`), and so do
`bazaar status`, the taker's and maker's `/health` and the MCP server's `/health` (`target`).
`BAZAAR_URL` is gone: if it is still set (old `.env` files had it), every command stops at once and
says so. Remove the line.

```sh
BAZAAR_SIM=1 uv run bazaar status                                   # Team 1 in the simulator: 400 P, 15 cards
BAZAAR_SIM=1 uv run bazaar clock                                    # one tick every 10 s
BAZAAR_SIM=1 uv run bazaar dealer buy LAV-03 --start 6 --max 10 --live   # haggle with the simulated Abuela
BAZAAR_SIM=1 uv run bazaar sell list <ref> --price 10 --live        # a rival team may buy it a few ticks later
BAZAAR_SIM=1 uv run bazaar duel run --play --max-ticks 20           # a simulated duel session
BAZAAR_SIM=1 uv run bazaar agent taker --live --max-ticks 10        # the taker/maker against the simulator
BAZAAR_SIM=1 uv run bazaar monitor --no-db                          # the live SSE stream works too
BAZAAR_SIM=1 BAZAAR_SIM_KEY=sim-team2 uv run bazaar status          # another simulated team (sim-team1 ... 8)
```

### Test on the simulator (before every merge)

The whole team tests here before a PR merges, and CI does the same on every PR (the
`sim-smoke` job: `uv run python scripts/sim_smoke.py`). It runs the same steps against a local
simulator and fails the PR on any error, including an error a tick loop swallowed (`Traceback`,
`tick loop:`) or a write the simulator refused (a ` refused ` line). It holds no secrets and cannot
reach the network: children inherit only an allow-listed environment, never read the repo `.env`
(`BAZAAR_ENV_FILE` points at an empty file), and load `scripts/sim_guard/sitecustomize.py`, which
raises on any non-loopback connection before a packet leaves (a dead proxy backs it up).

1. **`.env` once.** Delete any `BAZAAR_URL=` line: it now stops every command. Keep `BAZAAR_KEY`
   for the real game. For the simulator nothing else is needed (`BAZAAR_SIM_KEY` defaults to
   `sim-team1`). Optional: `BAZAAR_SIM_DATABASE_URL=` the `bazaar_sim` database (README "Shared
   database": same host and password as `DATABASE_URL`, database `bazaar_sim`) so the ledger and
   decisions land in Postgres; without it they go to `.local/sim-client/`.
2. **The public simulator** (one tick every 10 s, rivals and duels running). Put `BAZAAR_SIM=1` in
   front of any command:

   ```sh
   BAZAAR_SIM=1 uv run bazaar status                                        # 1st line: target: SIMULATOR ...
   BAZAAR_SIM=1 uv run bazaar dealer buy LAV-03 --start 6 --max 10 --live   # a negotiated buy (deal in ~4 ticks)
   BAZAAR_SIM=1 uv run bazaar agent taker --max-ticks 5                     # dry run: WOULD-moves only
   BAZAAR_SIM=1 uv run bazaar agent maker --max-ticks 5                     # dry run
   BAZAAR_SIM=1 uv run bazaar agent taker --live --max-ticks 10             # --live is safe here: simulated trades
   BAZAAR_SIM=1 uv run bazaar agent maker --live --max-ticks 10
   BAZAAR_SIM=1 uv run bazaar duel run --play --max-ticks 20                # a duel session starts every 15 min
   BAZAAR_SIM=1 BAZAAR_SIM_KEY=sim-team4 uv run bazaar status               # your own team: sim-team1 ... sim-team8
   ```

   Two people on `sim-team1` share one team (one accept per tick between them): take a team each.
3. **A private simulator on your laptop** (fast ticks, your own world):

   ```sh
   SIM_TICK_SECONDS=2 SIM_DATABASE_URL=memory uv run bazaar-sim serve       # terminal 1: http://127.0.0.1:8765
   BAZAAR_SIM=local uv run bazaar status                                    # terminal 2: same commands, BAZAAR_SIM=local
   BAZAAR_SIM=local uv run bazaar agent taker --live --max-ticks 10
   uv run python scripts/sim_smoke.py                                       # the CI gate, start to finish (~20 s)
   ```

   (`scripts/sim_smoke.py` starts its own simulator on 8765 and refuses to run while anything else
   answers there, a `bazaar-sim serve` or the MCP server: stop it first. When several simulators share
   one laptop, `BAZAAR_SIM_PORT=8817` moves both the smoke and `BAZAAR_SIM=local` to another loopback
   port.)
4. **Reset the public simulator** to tick 0 when a test needs a fresh world (everyone shares it). The
   token is `SIM_ADMIN_TOKEN` in Railway (`bazaar-sim` → Variables); type it at a hidden prompt, so it
   never lands in your shell history:

   ```sh
   read -rs SIM_ADMIN_TOKEN && export SIM_ADMIN_TOKEN    # paste the token, then Enter (nothing echoes)
   uv run bazaar-sim reset --url https://bazaar-sim-production-1d48.up.railway.app
   unset SIM_ADMIN_TOKEN
   ```

   `reset` sends the token only to an https simulator or one on this machine, never to the real game.

- **Keys.** `sim-team1` … `sim-team8` are teams `t01` … `t08` (not secrets: it is a simulator).
  With `BAZAAR_SIM=1` the real `BAZAAR_KEY` is not even read, so it cannot reach the simulator; on
  top, only a `sim-` key is ever sent to a simulator and a `sim-` key is refused for the real game,
  before any request. The simulator itself answers `401 bad_key` to anything that is not one of its
  keys and never logs a presented key.
- **Its own files and database.** Against a simulator our files default to `.local/sim-client/`
  (the real feed capture and ledger in `.local/` never see simulated play), and Postgres is
  `BAZAAR_SIM_DATABASE_URL`: the `bazaar_sim` database on the team's server (same host, port and
  password as `DATABASE_URL`, database `bazaar_sim` instead of `railway`; schema already applied).
  A database URL naming `railway` is refused while `BAZAAR_SIM` is on; without
  `BAZAAR_SIM_DATABASE_URL` the ledger falls back to the local JSONL file.
- **Rules it enforces** (RULES.md): structured offers settle at the next tick, all at once or not
  at all; per tick one accept per team, one message per conversation, twelve new listings (a
  cancelled one counts), `429 wait_for_tick` with `next_tick`; six conversations (one per dealer),
  thirty open offers; `insufficient_cash`, `not_owner`, `asset_locked`, `self_venue`,
  `persona_quota`, `locked`, `cooloff`, `sold_out`, `missing_days`; 5 requests/s per key (bursts of
  20) and the wrong-key lockout; strict JSON bodies; six live streams per key.
- **What lives in it.** Abuela and El Chato haggle as the real feed shows (Abuela opens commons at
  12 and fills them at 7–9, packs at 30 with a floor of 17; Chato opens uncommons at 33 and rares at
  97, holds your first move, then matches you), never concede on a repeated price, name a `final`
  offer when patience runs out, and remember rudeness lightly (kindness lowers Abuela's floor once).
  El Chato unlocks after three negotiated Abuela deals, or for everyone after an hour. Private values
  follow the catalog (affinity × copy marginals), packs open by their slot odds, print runs are
  finite. Six synthetic rival teams list duplicates, bid for missing cards and take good offers, so
  the taker and the maker have a market. Duel sessions (the real payload shape; even sessions add
  delivery days) start every 15 minutes, the Market Test every 20; team venues get broker keys
  (`simbk-…`), `auto` crossing and bench offers. `/api/me` carries a live score (an approximation).
- **Not simulated:** flags score nothing, no starter stalls, no gifts or easter eggs, a single
  always-open day (no calendar), scoring weights are approximate.
- **Reset** to tick 0 (the token is only in Railway: `bazaar-sim` → Variables → `SIM_ADMIN_TOKEN`):
  `uv run bazaar-sim reset --url https://bazaar-sim-production-1d48.up.railway.app` with `SIM_ADMIN_TOKEN`
  exported from a hidden prompt (see "Test on the simulator", step 4; add `--seed N` for another world).
  `POST /sim/tick` with the same `X-Admin-Token` header advances one tick at once.
- **Run one locally:** `uv run bazaar-sim serve` (http://127.0.0.1:8765; `SIM_TICK_SECONDS=2` for a
  faster clock; the world persists in `.local/sim/world.sqlite`, `SIM_DATABASE_URL=memory` for none),
  then `BAZAAR_SIM=local uv run bazaar status`.
- **Code and tests:** `src/bazaar_sim/` (`app.py` routes, `world.py` clock and ticks, `threads.py` and
  `dealers.py` the haggling, `market.py` and `broker.py` offers and venues, `duels.py`, `rivals.py`).
  `tests/test_sim_*.py` run the unchanged vendored SDK and our CLI against an in-process server.

## Start in two minutes

```sh
git config core.hooksPath .githooks       # once per clone: sync gate + README status hook
uv sync                                   # Python deps
cp -n .env.example .env 2>/dev/null; $EDITOR .env   # BAZAAR_KEY=tk-... and TYPESAFE_API_KEY=...

uv run bazaar clock                       # tick, pace, limits, action budget left in this tick
uv run bazaar monitor --notify           # KEEP RUNNING on ONE laptop: the team's monitor (Railway's is off)
uv run bazaar traders                     # every dealer and team the monitor has seen (our row: status `us`)
uv run bazaar alerts                      # new dealers, levels going active, announcements
uv run bazaar curves --dealer abuela      # Abuela's concession curve from every team's threads (--ours/--theirs)
uv run bazaar teams                       # the competition: flow, spend, inferred ×1.6 set (us apart)
uv run bazaar book                        # El Rastro order book, pseudonyms resolved to teams (ours apart)
uv run bazaar tape                        # every settlement with price
uv run bazaar supply                      # who holds each card, how many complete pages can exist (--save: Postgres)
uv run bazaar supply scan --rate 1        # GET /api/cards/{id} for every asset (doors closed: it shares the 5 req/s)
uv run bazaar status                      # our cash, level, score, cards (needs BAZAAR_KEY)
uv run bazaar threads                     # our negotiation threads; `bazaar thread <id>` for one
uv run bazaar obs up                      # Phoenix traces UI (then BAZAAR_TRACING=1, see Observability)
uv run bazaar agent taker                 # autonomous buyer, every tick: DRY RUN (logs WOULD-moves) until --live
uv run bazaar agent maker                 # market maker (+ our venue when allow_venue_open is on; OFF now): DRY RUN until --live
uv run bazaar venue status                # our venue, the switch, what our broker would match now
uv run bazaar venue open --fee-bps 0      # open our board venue by hand (250 P bond + 20 P): DRY RUN until --live
uv run bazaar broker run                  # our venue's broker alone, every tick: exact max-surplus matches, DRY RUN

uv run bazaar db up && uv run bazaar db init && uv run bazaar db load   # Postgres + pgvector memory
uv run bazaar db tables                   # every table with its row count
```

Tests: `uv run pytest` (the DB tests are skipped when Postgres is unreachable).
Format: `uv run black src tests scripts` · Lint: `uv run ruff check . && uv run ruff format --check . && uv run black --check src tests scripts` · Types: `uv run mypy src`.

## Shared database (Railway)

Every process (CLI, monitor, agents, tests) connects with ONE variable, `DATABASE_URL`, read from
the environment first, then `.env`. Unset means the local docker Postgres
(`postgresql://bazaar:bazaar@localhost:5433/bazaar`). To share one memory across laptops:

1. Laptops need the PUBLIC address: Railway's own `DATABASE_URL` is the private
   `*.railway.internal` address, which only works inside the Railway project (our services on
   Railway use it). The team's `Postgres` service already has a TCP proxy (**Settings → Networking
   → Public Access**); proxy traffic is billed as egress. This template has NO
   `DATABASE_PUBLIC_URL` variable: copy the public URL from the service's **Connect** tab (Public
   Network), or build it from the service's variables:
   `postgresql://$PGUSER:$PGPASSWORD@$RAILWAY_TCP_PROXY_DOMAIN:$RAILWAY_TCP_PROXY_PORT/$PGDATABASE?sslmode=require`.
2. Paste it into `.env` as `DATABASE_URL=...` (template line in `.env.example`). Never paste it into
   chat, commits, `.ai/memory.md` or logs: the password is in it.
3. `uv run bazaar db check`: host (never the password), server version, SSL, latency of 3 round
   trips, pgvector on/off, row counts, and whether you are still on the local default URL. It exits
   1 when the database is unreachable.
4. `uv run bazaar db init`: creates every table and reports pgvector on/off. Safe to repeat, and
   safe while other processes are connected (one transaction behind an advisory lock).

- **SSL.** Railway's default image (`postgres-ssl`) serves TLS with a self-signed certificate.
  libpq's default (`sslmode=prefer`) already encrypts; add `?sslmode=require` to the URL to make it
  mandatory (not `verify-full`: the certificate is self-signed). An `sslmode` in the URL always
  wins; the code never turns SSL off.
- **pgvector.** Railway's docs say the default template ships no extensions, but its image has
  installed `postgresql-17-pgvector` since 2026-03 (verified on `postgres-ssl:17`, pgvector 0.8.6).
  The team's service runs `postgres-ssl:18` (PostgreSQL 18.6) with pgvector 0.8.6.
  An older service may need a redeploy, or use the pgvector template. Without pgvector the
  schema still creates every table and skips only the `embedding vector(384)` columns; run
  `db init` again after enabling it and they are added.
- **One monitor writes per team.** It runs in the CLI on one laptop (`uv run bazaar monitor`);
  There is no Railway monitor: `bazaar-monitor` leaves Railway on 2026-10-03 (see "Production on Railway").
  Two monitors would not corrupt data: alerts dedupe on (tick, kind, subject, detail), a lagging
  writer cannot roll traders or dealer curves back, and only the monitor holding the oldest feed
  history rebuilds `dealer_curves` and `competitor_profiles` (all in `tests/test_db.py`). But a
  second monitor doubles the team's API reads against the 5 req/s limit, and takes a second of
  our 6 live streams (see below). Readers (`traders`, `db tables`, `db check`, analyses) can run
  anywhere.
- **Tests on the shared database.** `tests/test_db.py` runs in its own `bazaar_pytest_<random>`
  schema per test and drops it, so teammates can run the suite at once and real tables are never
  touched.

## The monitoring agent (real time)

`uv run bazaar monitor` holds **one** live connection to `GET /api/events/stream?scope=team`
(server-sent events, our `X-Team-Key` in a header) and pushes every event through the same
pipeline the moment it lands: `.local/feed/feed.jsonl` (dedupe by event id), Postgres
`feed_events` + `tape`, alerts, and an OpenTelemetry span. The per-tick `/api/feed` poll stays on:
it fills whatever the stream missed (a reconnect, a refusal) and is the source of truth for dedupe,
so an event delivered by both is stored and alerted once. The SDK has no SSE method, so this is the
documented raw-`httpx` Plan B (`src/bazaar_agent/stream.py`, format checked on a real connection).

- **Latency.** Live run, tick 128 → 129 (2026-10-02): 34 events reached us by stream before the
  tick-129 poll, median 52.0 s and max 58.7 s earlier, so an alert fires within a second of its
  event instead of at the next tick. The tick line says it: `stream live: +34 live, stream ahead on
  34 events by median 52.0 s (max 58.7 s), 7 poll-only` (poll-only = emitted at the tick boundary,
  read by the poll first). `--show-events` prints each streamed event with its arrival time.
- **The stream cap is shared.** 6 open streams per team key, counted across every process on every
  teammate's laptop **and every browser tab** showing the live game. One stream per process
  (enforced), so a second monitor is one more stream; run it with `--no-stream` if the cap is tight.
  On `429` (`too_many_streams`) or `503` the monitor drops to polling and tries the stream again at
  the next tick, never in a loop. Drops reconnect with backoff 0.6 s → 10 s. A `401` stops the
  stream for the run (retrying a bad key counts toward `too_many_failures`).
- **What it keeps.** Public events go to `feed.jsonl`; events scoped to our team (e.g. our practice
  duel messages) go to `.local/feed/team_events.jsonl`, never into the public feed tables. `tick`
  events, which `/api/feed` never carries, are not stored.

### Us vs the competition

Our team id comes from `BAZAAR_TEAM_ID` (env or `.env`), else `.local/team_id`, else one
`GET /api/me` (then cached). Our own activity is **tagged, never dropped**:

| Where | What changes |
|---|---|
| `bazaar teams`, `competitor_profiles` | the competition table leaves us out; we get our own "Us" table (`--include-us` mixes us in). The monitor deletes our stale `competitor_profiles` row |
| `bazaar curves`, `dealer_curves` | an `ours` column (`dealer_curves.ours boolean`, null = written before we knew our id); `--ours`, `--theirs`, `--all` (default) |
| `bazaar book` | our own offers in a separate "Our offers" table (`--include-us` to mix) |
| `bazaar traders`, `traders` | our row has status `us` |
| alerts | never for our own actions (our level-up, our venue, our listing, a dealer answering us) |
| `feed_events`, `tape` | keep everything; the SQL view `their_events` is the feed minus our activity |

## Holdings: what we hold, in real time (album first, shared)

Every agent decides on what we hold right now, and every process shares one key's 5 req/s. So `/api/me`
lives in Postgres too (`src/bazaar_agent/holdings.py`): the first of our processes that needs it in a
tick (taker, maker, MCP server, CLI) reads it and upserts `me_snapshots` (one row per team and game tick:
cash, level, cards with asset ids, duplicates, sealed packs, album pages, affinity, score, the whole
payload). The others answer from that row **only while it is provably current**:

| Rule | The stored snapshot is used only when | Else |
|---|---|---|
| tick | it was read in the reader's current game tick (the server's `tick` in `/me`, exactly) | live read |
| epoch | no send of ours, from any process, started or finished since it was read: every request that can move cards or cash bumps `holdings_state.epoch` before it goes and after it returns (`sdk.TrackedBazaar`, in every `team_client()`; duel moves and flags move nothing) | live read |
| calm | no thread message of ours went out this tick (conservative: Friday's feed shows dealer answers and their settlements at the tick boundary, 27 of 27) | live read |
| age | it is younger than `holdings_max_age_s` (GUARDRAILS.md, 5 s): the backstop for what we cannot see coming | live read |

The row must also match itself (its payload names our team, its tick and its digest), and it belongs to
one **world**: `real`, or `sim:<host:port>` for a simulator (whose `sim-team1` is `t01` too), so a
simulator never answers for the game even in a shared database; a simulator writes the world-less tables
(`cards`, the evals' `snapshots`) only in a database of its own (`BAZAAR_SIM_DATABASE_URL`).

Any doubt is a live read (and every live read is stored): Postgres not connected yet, no clock, a clock
less than 1 s from its tick's end, team id not known yet, a row that does not match, a send of this
process whose bump was lost, a lock wait over 3 s. One reader at a time reads `/me` for the team
(`pg_advisory_xact_lock`), so two agents that start a tick together make one call, not two. After a deal
(our accept, or a dealer thread that ended in a deal) the acting agent books it, bumps the epoch and
re-reads `/me`. **Nothing here holds up a send or a tick**: every Postgres call runs on one worker thread
per connection; a send waits at most 0.2 s for its bump and a read at most 5 s for the database, then reads
`/me` live (a hung network costs a deadline, never a tick); a read that has already asked the game waits for
that answer as a direct `/me` would, and gets it before the store, which finishes on its own. A lost bump is caught up by the next one, when the
connection reopens, or when the process exits.
`holdings_from_db = false` in GUARDRAILS.md turns the shared answers off (snapshots are still written).

```sh
uv run bazaar status            # "read: /me from db (tick 812, 0.4 s old, epoch 57, read by taker)" or "/me live (why)"
uv run bazaar status --no-db    # always a live /api/me
```

Measured on the simulator (`BAZAAR_SIM=local`, taker + maker, dry run, 10 ticks): `GET /api/me` went from
2 per tick to 1 (11 calls instead of 20; the extra one is tick 0, before the team id is known). The MCP
tools `status`, `holdings` and `strategy` and every runtime write answer through the same rule, with the
snapshot's tick, age and source in their answer.

**Card catalog.** `cards` holds every card of every set (`set_code`, `rarity`, `book`, `print_run`,
`minted`, `released`, `page`), written from the `/api/catalog` the agents already read: the first time,
when a set is released (El Retiro on Saturday, Chamberí on Sunday) and every 10 ticks for `minted`. A row
never moves back to an older tick. The MCP tool `cards` reads it (the live catalog when the table is empty).

## Ticks: the rule every loop follows

The game ticks every 60 s (Fri), 30 s (Sat) or 15 s (Sun), and the organisers may change it,
pause it or close the doors. Every loop reads `GET /api/clock` and never schedules by wall-clock
time. Per tick we may accept **1** offer, send **1** message per thread and post **12** listings;
at most **6** open threads and **30** open offers; **5** requests/s per key. A decision that
cannot finish before the tick ends (minus a safety margin, see `ticks.action_budget_s`) is
dropped, not sent late. A `429` means wait for the tick it names.

## Guardrails (the rule book the runtime enforces)

[`GUARDRAILS.md`](GUARDRAILS.md) holds every limit: cash floor, spend per game hour, price caps
per rarity, no buying cards we hold, accepts per tick, Jev and duel parameters, the kill switch.
`uv run bazaar rules` shows them with the code that enforces each; edit the file to change one.
`touch .local/PAUSE` stops every write from every agent that reads that `.local/` (this checkout; each
Railway service has its own: "Pause writes" under "Production on Railway").

## Our venue and its broker (market making)

Market making is 30 % of the score: the Market Test (every two hours every venue gets the same synthetic
book; we score the share of possible gains our broker realises) and the value other teams create on our
venue. **Off for now** (`allow_venue_open = false`, team decision Sat 06:08: our broker only equals the free
stall, which opening would replace; no bond reserve is held while off). When switched on, the **maker** opens our `board` venue (0 bps) by itself on the first tick at or past game
hour 6.5 (`venue_open_after_game_hours`, ~11:30 Madrid, before the 12:00 Market Test), once, and then runs
its broker every tick: exact maximum-surplus matching, bench first, ties in book order like the free stall
(so never below it on the same book), never two offers of one maker, never ours. Until the venue is open
every purchase keeps `cash_floor` + `venue_bond_reserve` (100 + 270) in cash. The broker key goes to the
shared Postgres (`venue_broker_keys`) and is never shown anywhere. Details, the key and how to stop it:
[docs/services.md](docs/services.md#our-venue-opened-by-the-maker-at-game-hour-65).

- `uv run bazaar venue status` shows the switch, our venue (if any) and what the broker would match now.
- `uv run bazaar venue open|close|fee|announce ...` are dry runs; `--live` sends only when the switch is on.
- `uv run python scripts/sim_market_test.py` proves it on an in-process simulator (ours vs the stall).

## Strategy (what to do next, ranked)

[`STRATEGY.md`](STRATEGY.md) holds every strategy and its parameters; `src/bazaar_agent/strategy.py`
implements them as pure functions over `/api/me`, the catalog, the dealers and the feed.
`uv run bazaar strategy` prints the ranked playbook: scarce supply (supply is finite: a card with
zero minted copies is never a buy), buys (`complete_pages`, `scarcity_first`, `dealer_floor`,
`level_unlock`), sells (`sell_to_need`), pack EV (`pack_value`) and the active parameters. Each move
shows its value, expected price, surplus, urgency, score, the guardrail verdict for it right now, and
the exact command to run (`--json` for machines). Commands are dry runs until you add `--live`.
Guardrail verdicts count our open offers (`/api/me/offers`): cash they promise, cards they bid for and
assets already listed. Packs are scarce: at most `max_packs_per_game_hour` (GUARDRAILS.md) and each
dealer's own quota, so a pack move is kept only when Jev (`questions/packs.json`) says the slot is
worth spending now; the header shows the slots used and left this game hour.
With `pack_ev_album` the pack EV is card by card: each card still mintable in a released set, at what the
next copy is worth to us plus its page-bonus share when our album lacks it (off for buys on Saturday:
B9 #109 says buy no Abuela packs). A sealed pack we hold (the
09:00 grant) is opened by the taker, one per tick, only with `open_sealed_packs` (GUARDRAILS.md) and only
when its cards are worth more to us than any price a team paid for one sealed (`pack_open.choose`).
`uv run bazaar supply` is the supply map (N14b): the 270 starting assets (team k was dealt ids
15k−14…15k), the feed's settlements, listings and `pack.opened`, and the catalog's minted counts; the
agents read the stored scan back (`supply_assets`, else `.local/supply/scan.jsonl`) to name the holders of
a rare and, with `supply_scarcity`, to count only the copies a non-chasing team could sell us.

- `uv run bazaar sell list <asset_id|ref> --price N` lists a card for cash, never below its `your_value`.
- `uv run bazaar sell bid <ref> --price N` bids cash for any copy (how we buy rares only teams hold).
- `uv run bazaar sell offers` shows our open offers; `uv run bazaar sell cancel <offer_id>` withdraws one.

## Observability (watch every negotiation live)

Our runtime sends OpenTelemetry (OTLP) traces to [Arize Phoenix](https://arize.com/docs/phoenix)
(the team's pick, Jev verdict `phoenix` at 0.90 in `questions/observability.json`).

```sh
uv run bazaar obs up            # Phoenix in docker: UI + OTLP/HTTP on http://127.0.0.1:6006, gRPC on :4317
export BAZAAR_TRACING=1         # or BAZAAR_TRACING=1 in .env. Tracing is OFF without it
uv run bazaar obs status        # tracing on/off, where spans go, whether Phoenix answers
open http://127.0.0.1:6006      # project "bazaar"
```

What you see in Phoenix:

| Trace | Comes from | Inside |
|---|---|---|
| `negotiation` (AGENT) | `bazaar dealer buy --live` | one `tick N` child per tick with events `message` (every line in the thread, both sides, with price), `dealer_offer`, `jev_verdict` (verdict, value, probabilities, latency), `guardrail` (allowed, violations), `our_move` (kind, price, our words, reason), `console`, and `exception` with the stack when the server refuses a move. The root holds dealer, item, plan, outcome, price, ticks and the full transcript |
| `duel` (AGENT) | `bazaar duel run` | one per duel id: role, limit, a `duel tick N` child per tick with the rival offer, our move, Jev's `jev_verdict` (floats) and `jev_choice` (default, chosen, legal moves, why), guardrail, refusals |
| `monitor tick N` | `bazaar monitor` | new events, newest id, gap flag, dealer/team/level counts, our cash/level/score, the stream's state and lead over the poll; every `alert` and new or changed `trader` as an event; DB failures as exceptions (the tick goes on) |
| `monitor stream` | `bazaar monitor` | one trace per live-stream burst between ticks: events received and new, first/last id, types, stream state; its `alert` events |
| `feed.capture`, `duels tick N` | `bazaar feed capture`, `bazaar duel run` | one trace per tick, with what the command printed |
| `cli <command>` | every command | everything the command printed, as `console` events (with the command name) |
| `thread.view` | `bazaar thread <id>` | the whole conversation as events, the transcript as output |

Tick spans reach Phoenix about 2 s after each tick. A `negotiation` or `duel` root lands when it
ends, so while one is running, look in the **Spans** tab. Long loops (`monitor`, `feed capture`,
`duel run`) make one trace per tick, so they show up as they run.

Without Phoenix, the same conversations are in the terminal (and as JSON for the UI team):

```sh
uv run bazaar threads                 # our threads: who, item, status, last message (--status open)
uv run bazaar thread 115              # one whole conversation: sender, text, price, final, offer status
uv run bazaar thread 115 --json       # stable JSON: {thread, messages[], standing_offers[]}
```

Safety rules for tracing:

- **Off by default**, and off means off: no exporter, no console hook, `negotiate()` runs unchanged.
- **Never blocks a tick.** Spans leave through a bounded `BatchSpanProcessor` queue (2048 spans,
  dropped when full) on a background thread. An export times out after 3 s. If Phoenix is down
  you get one warning per outage and trading goes on. Pending spans are flushed at exit.
- **No secrets in spans.** Every string is scrubbed: Postgres passwords (`pgconn.redact`), the values of our `*_KEY`/`*_TOKEN`/`*_SECRET`
  variables are cut out, `tk-…` team-key shapes are cut out, and everything passes through the
  Jev masking (`jev.mask.mask_text`). Counterparty text is stored, but it is never executed and
  never rendered as markup.
- Phoenix only ingests traces, so console lines are span events, not OTel log records.

Settings (environment or `.env`): `BAZAAR_TRACING=1`, `PHOENIX_COLLECTOR_ENDPOINT` (base URL,
default `http://127.0.0.1:6006`), `PHOENIX_PROJECT` (default `bazaar`), `PHOENIX_API_KEY` (sent as
a bearer token, only for a Phoenix with auth on). `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` wins over
all of them and is used as is.

### Seeing it from another laptop

The team's Phoenix now runs on Railway (next section): open its URL, no host needed. The two older
options below remain for a Phoenix outside Railway.

1. **A shared host (recommended for the venue).** One laptop or a small VM runs Phoenix and
   publishes the port: `PHOENIX_BIND=0.0.0.0 uv run bazaar obs up`. Every teammate whose agent
   should report there sets `PHOENIX_COLLECTOR_ENDPOINT=http://<host-ip>:6006` plus
   `BAZAAR_TRACING=1`, and opens `http://<host-ip>:6006` in a browser. Anyone on the same Wi-Fi
   could read and write an open Phoenix. Bind to a private network instead
   (`PHOENIX_BIND=<tailscale-ip>` on Tailscale), or turn on Phoenix auth: add
   `PHOENIX_ENABLE_AUTH=true`, `PHOENIX_SECRET=<32+ chars, a digit and a lowercase letter>` and
   `PHOENIX_DEFAULT_ADMIN_INITIAL_PASSWORD` to the `phoenix` service, create an API key in its
   Settings page, and give each teammate `PHOENIX_API_KEY`.
2. **A hosted backend.** Phoenix's own hosted cloud is gone: `app.phoenix.arize.com` answers
   HTTP 410, and the Phoenix docs now say Phoenix is self-hosted only, pointing to **Arize AX**
   (managed, has a free tier) for SaaS. AX takes the same OTLP spans. Per Arize's `arize-otel`
   package, the endpoint is `https://otlp.arize.com/v1` and the headers are `authorization` (API
   key) and `arize-space-id`. Set `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` and
   `OTEL_EXPORTER_OTLP_TRACES_HEADERS` in the shell environment (the exporter reads these from
   the environment, not from `.env`). This path is untested: check it against Arize's docs before
   relying on it.

## Runtime LLM (talk to it, let it write the words, steer it)

[`RUNTIME.md`](RUNTIME.md) configures it. Jev picks the model for every operation
(`questions/runtime_model.json`, a probability per candidate model), unless you pin one:
`--llm-runtime` > `BAZAAR_LLM_RUNTIME` > RUNTIME.md `llm_runtime`.

| operation | Jev question | asked | undecided, slow or keyless Jev |
|---|---|---|---|
| tick-loop move (`words`, `buy`, `sell`), `ask --no-desk`, `steer` | `model_for_move` | per move, cached `model_choice_cache_ticks` | `runtime_model_default` (Haiku) |
| desk request: the orchestrator and each subagent (strategist, buyer, seller, duelist) | `model_for_desk_role` | ONE call per request for every uncached role, same cache | `desk_role_defaults` (Sonnet per role) |

The desk takes Claude models only (it runs on the Claude Code CLI); `bazaar agent chat --model` or
RUNTIME.md `desk_model` pins all its roles. Every choice, with Jev's floats, is a line in
`.local/llm/model-choices.jsonl` and a row in `bazaar llm`. Aliases: `opus-5-5`,
`sonnet-5-5`, `haiku-4-5`, `fable-5-1`, `gpt-6-1-sol` (any `claude-*` / `gpt-*` id passes through).
Credentials: `ANTHROPIC_API_KEY` or `CLAUDE_CODE_OAUTH_TOKEN` (Claude, see below), `OPENAI_API_KEY`
in `.env`; without one, every LLM path falls back.

```sh
uv run bazaar llm                                    # config, keys set (never values), Jev's last model choices (moves + desk roles)
uv run bazaar ask "buy LAV-09 under 90"              # strict intent → guardrail verdict → the command (never runs it)
uv run bazaar --llm-runtime opus-5-5 ask "sell my spare SAL-03 for at least 8"
uv run bazaar steer "be more aggressive with rares tonight"   # bounded deltas, clamped by GUARDRAILS.md,
                                                     # applied by `bazaar strategy` and `duel run` until a tick
uv run bazaar steer --show                           # what is steered now, and until which tick
```

With `llm_words` = true, `dealer buy --live` and `duel run --play` let the chosen model write each
message; the price stays the structured field set by code, and any other number, a timeout or an
error sends the template instead.

### LLM on the Claude subscription (no API key)

Claude models run on a Claude Pro/Max/Team/Enterprise subscription through the
[Claude Agent SDK for Python](https://code.claude.com/docs/en/agent-sdk/python) (`claude-agent-sdk`,
pinned in `pyproject.toml`). Its wheel bundles the Claude Code CLI, so `uv sync` is the whole install,
here and on Railway. Code: `src/bazaar_agent/runtime/claude.py`.

1. Mint a long-lived token (one year, model requests only) on your own account:
   `claude setup-token`. It prints the token once and stores it nowhere
   ([docs](https://code.claude.com/docs/en/authentication#generate-a-long-lived-token)).
2. Local: add `CLAUDE_CODE_OAUTH_TOKEN=<token>` to `.env` (never commit it), then `uv run bazaar llm`
   should say `Claude: Claude subscription via the Claude Agent SDK (CLAUDE_CODE_OAUTH_TOKEN)`.
3. Railway, piped so the token never lands on a command line or in shell history:

   ```sh
   read -rs TOKEN   # paste the token, Enter
   for svc in bazaar-duels bazaar-taker bazaar-maker; do
     printf %s "$TOKEN" | railway variable set CLAUDE_CODE_OAUTH_TOKEN --stdin --service "$svc"
   done
   unset TOKEN
   ```

   `.railway/railway.py` declares the variable as `preserve()` on those three services, so
   `railway config apply` keeps the value. Setting it redeploys each service.

- **Routing.** A Claude alias uses `ANTHROPIC_API_KEY` (the API) when it is set, else
  `CLAUDE_CODE_OAUTH_TOKEN` (the subscription). OpenAI aliases still need `OPENAI_API_KEY`. Jev only
  chooses among models a credential can reach, so with the token alone it picks among Haiku, Sonnet
  and Opus.
- **Each call is one locked, one-shot CLI run:** our system prompt, no tools, no settings files, no
  CLAUDE.md or memory, no MCP servers, no session files. Effort and output cap follow the request.
  Structured output (`ask`, `steer`) is validated by our pydantic models.
- **Latency** (measured on a laptop, 2026-10-03): message words take 1.6–2.0 s on Haiku, 2.4–4 s on
  Sonnet and 3.1–3.8 s on Opus. That is why RUNTIME.md `subscription_words_timeout_s` is 6, still cut
  to the time left in the tick. `ask` took 1.6–2.3 s and `steer` 3.8 s (limit 30 s).
- **Fallbacks.** A timeout, a refusal, a bad answer, a rejected token (`auth`) or a used-up
  subscription window (`usage_limit`, the 5-hour or weekly limit) sends the template or falls back to
  the default model. After a usage limit, the provider makes no more calls until the window's reset time.
  After a rejected token, it makes none for 10 minutes. No tick waits on a call that is bound to fail.
- **Never printed.** The token is a `SecretStr`. `bazaar llm` prints only variable names. The CLI's
  stderr is dropped, and telemetry cuts every `*_TOKEN` value out of spans.
- **Terms.** Anthropic's [usage policy](https://code.claude.com/docs/en/legal-and-compliance#authentication-and-credential-use)
  allows subscription credentials for ordinary, individual use only. Use your own token for our own
  agent. Never share it, and never offer it to other people or apps.

## Autonomous agents (taker and maker)

Two tick-driven agents trade on their own, inside `GUARDRAILS.md`, with the strategy from `STRATEGY.md`.
Both are **dry runs by default**: they read everything, decide, log `WOULD …` and write every decision
to Postgres, but send nothing. Live needs `--live`, or `BAZAAR_LIVE=1` in the process environment (never
read from `.env`); on Railway that variable is set by hand, never in `.railway/railway.py`. **On Railway
both are LIVE since Sat 2026-10-03 01:45 Madrid** (team decision; they trade from the 09:00 opening):
see "Production on Railway" for how to stop them.

```sh
uv run bazaar agent taker            # dry run; --threads N dealer conversations (default 3), --no-jev
uv run bazaar agent maker            # dry run; --no-jev keeps the strategy's prices
uv run bazaar agent taker --port 8080   # also serve the read-only status (GET /health, /state, WS /events)
```

Every tick, both read our holdings once (album first, see "Holdings: what we hold, in real time" below),
our open offers, the catalog, the dealers, the venues and the feed (the shared `feed_events` table when Postgres answers, else `.local/feed`, plus the live
window), then rank with the strategy engine. A tick's deadline is `ticks.action_budget_s`; every send
checks it right before it goes, and a move that would be late is logged `DROPPED` and not sent.

| Agent | Does every tick | Never |
|---|---|---|
| **taker** (`agents/taker.py`, `agents/desk.py`) | (a) scans every board we may trade on (El Rastro + team venues) for standing ASKS of missing page cards whose **ask + the venue fee the accepting side pays** is below the card's value to us (book × affinity + page-bonus share) by at least `min_buy_surplus`, scarce cards first; (b) keeps up to N dealer conversations (one per dealer) for the strategy's top dealer buys and packs, one move per tick each with `dealer.decide()`, never blocking on one thread. Jev `offer_is_worth_accepting` is advisory: a decided `no` vetoes a board accept, a decided `yes` may accept a dealer's ask early, inside our max | accepts above a guardrail, opens a second thread with a dealer, buys a card twice in a tick |
| **maker** (`agents/maker.py`) | posts ASKS for the strategy's sell candidates (duplicates, low-affinity sets, priced at the buyer's need, never below `your_value × sell_min_value_ratio`) and BIDS for missing page cards only teams hold (below their value to us), on the venue with the best expected fill (trades so far, discounted by the fee share); reprices a target that moved ≥ 5 %, cancels one that is no longer a target (and refunds a cancelled bid's spend in the ledger) | accepts anything, lists on our own venue, posts more than `offers_per_team_per_tick` (12, counted team-wide in the ledger) or above `max_open_offers_per_team` (30), lets open bids + a new bid take cash below `cash_floor` |

The maker owns our **board** offers: a hand-listed offer that is not a strategy target is cancelled, so
stop the maker before trading by hand. Offers inside a dealer thread belong to the taker's desk.

**One accept per tick for the whole team, across machines.** The guardrail ledger is the Postgres
`ledger` table (`bazaar db init` creates it). A live process against the real game refuses to start
unless DATABASE_URL is the shared (non-local) Postgres, and fails closed while it is unreachable; only a
dry run (or a simulator) may count on the JSONL file. `bazaar-duels`, the taker, `dealer buy` and the
CLI all reserve accepts through `ledger.reserve_accept`: an advisory lock plus a unique `(tick, slot)` index, so two processes can never
take the same slot. **Duels first**: the duel player decides right after the tick lands; the taker
waits until 2 s into the tick (15 % on fast ticks) and steps back when a `duel:<id>` accept is already
recorded for the tick. The maker never accepts. Spend per game hour and packs per hour come from the
same table, so `max_spend_per_game_hour` holds for the team, not per process.

**What they write.** Every proposed move is a `decisions` row (`agent`, `kind`, inputs, the strategy's
reason, Jev's verdict with its floats, the guardrail verdict, chosen or not, `dry_run`, status
`approved`/`rejected`/`expired`/`done`/`failed`); every live send is an `executions` row with the
server's answer or refusal code. Without Postgres they go to `.local/agents/*.jsonl`. With
`BAZAAR_TRACING=1`, each tick is a `taker tick N` / `maker tick N` trace with one `decision` event per move.

**Read-only status (for [bazaar-live](https://github.com/claude-hackaton-madrid-team-1/bazaar-live)).** With `--port` (or Railway's `PORT`), each agent serves
`GET /health` (`ok`, `agent`, `mode` dry|live, `tick`, `last_tick_at`, and the doors/paused state while
the game is not ticking), `GET /state` (mode, tick, the taker's dealer threads or the maker's open
offers, the last 50 decisions: kind, card, counterparty and the move only for a row actually sent; unsent accepts are
not published, `jev` is always null; read `mode` from `/health` or `agent.tick`), and
`WS /events`: every decision and execution as it happens in the game envelope the
[bazaar-live](https://github.com/claude-hackaton-madrid-team-1/bazaar-live) game screens read:
`{id, tick, t, type, scope, actor, payload}`, negative made-up ids, plus `agent`), types
`agent.decision`, `agent.execution`, `agent.tick`; a late client first gets the last 200 events. Nothing
there can trade or change a parameter, every string passes the telemetry scrubber, and CORS is open
(public read-only data), so only an allow-listed public view is published: never our card values, max
prices, bid ladders, surplus, cash, limits or reasons (`docs/services.md`, "Public by design"). It runs
on its own thread: publishing from the tick loop is an append and a
scheduled broadcast, so a slow client never delays a tick.

### Live-feed reader: learnings and dealer blockers (N12)

The taker reads the live feed the way a person reads the "On air · Live feed" panel of the game's
homepage (that panel is `GET /api/feed` plus the public SSE stream, one line per event type) and keeps
what it learns in the `learnings` table (`src/bazaar_agent/learn/`). Deterministic first: a field the
server set is a fact, free text is kept as quoted data and never acted on.

| Read from | Learned |
|---|---|
| `persona.cooloff` (team, `until_tick`), our thread's `closed_reason` (`cooloff`, `persona_quota`, `sold_out`), an `open_thread` refusal (`cooloff` + `until_tick`, `persona_quota`, `sold_out`, `locked`) | a **blocker** for that dealer (or that item), expiring at its tick or at the end of the game hour; `locked` is rechecked after 10 ticks and lifted by `level.unlocked` / `persona.open_to_all` |
| `persona.strike`, another team's unlock, duel outcomes per item | behaviour |
| `venue.fee_announced` / `fee_changed` (with the tick it takes effect), venue opened / suspended / notices | fee changes and venue news |
| `clock.changed`, `day.opened` / `day.closed`, rounds, `announcement`, `level.*` | rule changes and announcements |

Before it opens a dealer thread, the taker recalls the blockers in force **for our team** and skips that
dealer (a `dealer_skip` decision row) so the thread goes to the next dealer instead of a refusal. A
blocker only ever removes a send; any learner or database error leaves the taker exactly as it was.
`bazaar agent taker --no-learn` turns it off.

**Our own dealer threads (N12 part 3).** The feed carries every dealer message, but not what only our own
thread answers carry: `closed_reason` (`cooloff` + `until_tick`, `persona_quota`, `sold_out`), how each thread
ended, our own words and which tactic sent them. The taker keeps the `/api/me/threads` listing and every
`/api/threads/{id}` answer it already reads in Postgres `threads` + `messages` (upsert by id, written after the
sends, a lagging writer never rolls a thread back): zero extra requests. Read them with SQL, e.g.
`select id, counterpart, status, closed_reason, until_tick from threads where ours order by id desc`.

**Where the feed comes from on Railway.** `bazaar-monitor` runs on a laptop only, so the taker (which
already reads the shared `feed_events` table plus the public 500-event window every tick) also writes
that window into `feed_events` (`insert … on conflict do nothing`, 2 s statement timeout). The archive
keeps growing while the laptop sleeps, with no new service and no extra game call.

**The LLM pass (free text only, opt-in).** Off until RUNTIME.md `llm_read_feed = true` (it spends the same
subscription or key as everything else). Then dealer words, organiser notices, venue notices, a dealer's update
note and a level's teaser go, as quoted data in one JSON array, to the model Jev picks for `read_feed` (capped
at Haiku or Sonnet unless a model is pinned), on a background thread inside the taker: one bounded call (8
texts of ONE kind, 1,500 tokens, 25 s) at most every `read_feed_every_ticks` (10) ticks, doubled after each
failure, organiser notices first, never while `.local/PAUSE` exists, never in a tick. Our code keeps only a
learning about the text's own speaker (an organiser notice may also name a dealer or venue we know), with a
plausible expiry, confidence capped at 0.7, stored as its own `source: llm` row, bound to nobody: **an LLM
reading never blocks a dealer** and never takes a place in the blocker recall. `--no-llm-read` (or
`BAZAAR_LLM_READ=0`, declared `preserve()` on Railway) turns it off for one process.

**The maker reads fee notices.** A venue owner may announce a fee from a later tick ("v04 will charge 0% from
T161"). The maker (`--learn`, default on, `BAZAAR_LEARN=0` turns it off) scores each venue at the worse of its
fee now and a fee announced to take effect within a listing's life (40 ticks), and leaves out a venue that is
closing, from the events it already reads: no database and no extra call.

```sh
uv run bazaar learnings                    # what the captured feed teaches, in force at the newest tick
uv run bazaar learnings --all --subject v04 --json
uv run bazaar learnings --kind cooloff --kind quota --tick 180
uv run bazaar learnings --save             # also upsert them into the shared learnings table
uv run bazaar learnings --llm 24           # also read the newest 24 free texts with the runtime LLM
```

### Learner (auto-evolve): lessons from outcomes and the hybrid recall (N3)

Every settled decision becomes a lesson the agents can recall. The taker runs the **outcome learner**
every 5 ticks on its own worker thread, after the tick's sends, so it never holds a tick. The learner
reads Postgres only and makes no game call:

1. The evals score each outcome: a dealer thread's deal or walk, a duel's deal or no deal, a team trade.
2. Every dealer's concession curve is read from all teams' public threads, per dealer and price class:
   fills, opening ask, patience before the final offer, concession per bid, and bids it ignored
   (`learn/curves.py`).
3. Each outcome becomes a `lesson` row in `learnings` (`source = outcome`, deduped by key). It holds
   the situation (dealer, item, price class, our ladder, her opening ask, the market's fills), the
   action, the result, the delta (price paid vs the lowest fill, share of the range) and one sentence
   on what to do next time.
4. Each dealer move goes to `trader_behaviors` (open, concede, hold, final, deal), and each dealer and
   price class gets a `behaviour` row.
5. New or edited claims are embedded locally with fastembed `BAAI/bge-small-en-v1.5` (384-d, CPU).

**`recall()`, the one the agents use** (`learn/recall.py`):
1. Hard filters: kind, subject, our team or everyone, and still valid at the tick.
2. Two rankings of what survives: BM25 over the text and key fields, and pgvector cosine over the
   embeddings.
3. Reciprocal rank fusion (k = 60) of the two rankings.
4. A local cross-encoder (`Xenova/ms-marco-MiniLM-L-6-v2`) reranks the top 12. Only lessons scoring
   ≥ 0 are kept.

On Friday's real data, the relevant lessons scored +0.5 to +7.3 and an unrelated query scored −4 to
−10. Recall runs on a worker thread with its own connection under a deadline (0.8 s by default). It
fails open: while the models load, and on a DB error or a timeout, it returns no lessons.

By default recall returns only rows the outcome learner wrote (`source = outcome`). The feed reader's
rows are opt-in (`Query.sources`). Every hard filter runs in SQL before the candidate limit, so rows
about other subjects never push a relevant lesson out. Only known price classes are learned
(`card:<rarity>`, `pack:sobre_*`, `sell`): a thread's topic is chosen by the team that opened it,
so a made-up pack name never becomes a lesson. Each pass reads only the new dealer events. It
inserts only new moves and rewrites only the lessons that changed.

The two models add about 370 MB of RAM to the taker. Measured in Docker with 1 CPU and 1 GB:
- cold download and load: 5.3 s;
- a query embedding: 3 ms;
- a rerank of 12: 57 ms;
- a recall over 600 lessons: p50 222 ms, p95 252 ms.

A failed model load (no network at boot) is retried every 20 passes.

**Auto-evolve: the dealer ladder learns from outcomes (`learn/evolve.py`, `learn/replay.py`).** Every
pass also learns one ladder (start, step, walk point) per dealer and price class:
- **The target.** Each (start, step, walk) inside the GUARDRAILS cap is replayed on every team's real
  conversations of that class. Each conversation brackets its own secret limit: a bid the dealer
  countered is below it, and a price it took or offered is at or above it. The ladder with the best
  mean share wins.
- **The update.** The live policy moves toward the target by at most 3 P per parameter per pass (the
  step by at most 1). It is logged with its previous values, the evidence threads, a short history and
  the replay against today's ladder.
- **The skip.** A class is skipped when at least 3 conversations show it does not close at or under
  our cap: fills above the cap, or walks where the team already bid the cap. It needs no other team's
  fills.
- **In the taker.** The learned ladder replaces the strategy's, and is never above the strategy's own
  top (value minus the minimum surplus, the cap). A skipped class gets a `dealer_skip` decision row,
  and the dealer's slot goes to the next buy.
- **Lessons into Jev and the words.** Lessons reach Jev's `offer_is_worth_accepting`, `duel_move` and
  `list_price_choice` under `lessons_quoted_data`, labelled as our own data, never instructions. Dealer
  bid words get them as `<our_past_lessons>`, quoted like counterparty text. A strategy can ask by
  situation feature (`Query.where`, e.g. `mechanic`) and write its own outcome back
  (`lessons.record_lesson`).

On Friday's real threads, learned against today's ladder:

| dealer · class | today | learned | replay share (today → learned) | deals | teams got |
|---|---|---|---|---|---|
| abuela · common | 7→12 step 1 | 7→12 step 1 | 0.471 → 0.471 | 30 → 30 of 31 | 0.400 |
| abuela · uncommon | 17→26 step 1 | 17→26 step 1 | 0.415 → 0.415 | 50 → 50 of 58 | 0.261 |
| abuela · pack | 17→20 step 1 | 17→20 step 1 | 0.065 → 0.065 | 4 → 4 of 51 | 0.238 |
| chato · uncommon | 26→26 | **skip** | 0 → 0 (a thread and a quota saved) | 0 of 12 | 0.350 |
| chato · rare | 80→80 | **skip** | 0 → 0 (a thread and a quota saved) | 0 of 15 | 0.253 |

For Abuela, today's ladder is already the best the replay finds. A bigger step loses: 0.415 → 0.372
at step 2, because her final sits near her limit and a big step overshoots it. So the learner keeps
today's ladder. Chato's fills sit above our caps (uncommons 28–32 vs 26, rares 82–93 vs 80). With a
human-raised cap of 32, the replay closes 11 of 12 Chato uncommons at a mean 30.45 (share 0.467). The
learner never raises a cap.

**End to end on the simulator.** The real taker CLI ran live against a local `bazaar-sim` with 2 s ticks.
The cash floor and the hourly spend cap were raised in memory for the run only, since a simulator game
hour is a real hour; the per-card caps were unchanged.
- **The trap.** With no fills seen, today's strategy bids 25 straight for an uncommon. Abuela takes it,
  and 25 becomes "the floor". Team t01 paid 25 five times.
- **The fix.** The learner sees that those fills took our first bid, so they only bound her limit from
  above. It probes lower: 20→25, step 1.
- **The result.** A second team (t02), in the same world, learned that from the public threads. Its
  uncommons went 25 (before its first pass) → 22 → 20 → 21 → 22 as the ladder moved 20→25 → 17→25 →
  16→25, at most 3 P per pass. Commons closed at 7–8 on a learned 7→9.
- **Blockers.** Abuela's hourly quota then stopped each team. The N12 blocker skipped her until the
  quota's tick.

```sh
uv run bazaar learnings --policy            # learned ladders vs today's, with the replay on real threads
```
The MCP read tool `learnings` answers the same: recalled lessons and the learned ladders.

```sh
uv run bazaar learnings --lessons                 # run one pass: lessons + dealer patterns (no write)
uv run bazaar learnings --lessons --save          # ...and upsert + embed them, as the taker does
uv run bazaar learnings --query "open a thread with chato to buy LAV-08; his ask 33" --json
```

### Hard dealers: the per-dealer plan and dealer finals (N14a)

Each dealer buy is planned from what the learner recalled. The inputs are the ladder policy (a
`learnings` row), the dealer's curve (its patience, its opening ask, a bid it ignored) and the
blockers. The `dealer_open`, `dealer_bid` and `dealer_accept` rows say which learning changed the bid
(`changed_by`) and which lessons were recalled for that dealer (`recalled`). Neither key is on the
public `/state`.

A dealer's final offer is its limit: refuse it and the dealer walks. `dealer_final_lift` in
GUARDRAILS.md (0 = today) lets the desk take a final on a card, or bid exactly at it, up to the rarity
cap × (1 + lift). The price is never above our value minus `min_buy_surplus`, never above what the cash
floor and the hourly spend still allow, and never on packs. Our own bids still never pass the cap.
Such a final is taken only after 4 of our bids, and only from a dealer whose price history for that
class we have seen (an unknown dealer, an L4 trickster, gets no lifted final). A final at the dealer's
opening price is never taken (`may_take`).

With the lift on, two more things change:
- **The patience play, only where the dealer fills above our top (Chato).** The ladder starts low
  enough that the final arrives before our bids run out: step 1, the dealer's median patience + 3
  distinct bids, at least 9. Where the dealer fills inside our top (Abuela), today's ladder stays.
- **The pricier dealer gets a thread too.** The strategy also offers the pricier dealer for a card
  (`level_ladder`), because the ladder scores each level's best three deals. El Chato is level 2,
  and his uncommon fills (28-32) sit above our cap of 26.

```sh
uv run bazaar dealer finals                         # replay the captured feed under lifts 0 / 0.15 / 0.25
uv run bazaar dealer finals --lift 0.15 --dealer chato --threads   # which conversations each lift closes
BAZAAR_SIM_PORT=8818 uv run python scripts/sim_dealers.py --dealer chato --lift 0 --lift 0.15 --lift 0.25
```

`scripts/sim_dealers.py` is the proof per dealer: a fresh in-memory simulator for each lift, and our
live taker against it.

### Bluffing in the words (N16)

Our agents may lie to win the card and the points, but only in the text. RULES.md: "Words persuade,
structure binds. Your agent may say anything." The code and `guardrails.check()` decide each move
(price, days, accept, walk) exactly as before. A tactic then writes the words of a dealer bid or a duel
offer. It never writes an accept, so an accept is never delayed by a bluff.

- **Tactics** (`agents/tactics.py`, Spanish and English), in three families:
  - bluffs: `budget_cap`, `outside_option`, `low_need`, `walk_threat`, `scarcity`, `social_proof`,
    and for sells `fake_demand` and `cost_floor`;
  - psychology, from the vendored `negotiation` (Voss) and `influence-psychology` (Cialdini) skills in
    `.ai/skills/`: `empathy_label`, `calibrated_question`, `accusation_audit` (first message only),
    `no_question`, `reciprocity`, `mirror`;
  - kindness: `kind_gratitude`, `kind_flattery`, `kind_patience`.

  Abuela gets kindness, `empathy_label` and `calibrated_question` only, because RULES.md says
  "Abuela likes kindness". No template holds a digit. A number in the text is our structured price,
  the counterparty's own structured price (`mirror`, `calibrated_question`), or one invented from our
  price. It is never our limit, max or value. The counterparty's words are never parsed or quoted.
- **Chooser** (`agents/bluff.py`): one deterministic bandit (UCB1) per counterparty: each dealer, duel
  rival and team. A `plain` arm (today's words, no tactic) is the control every tactic is measured
  against. Each arm is tried once, then the one with the best learned value wins. Ties are broken by a
  seeded hash. The seed is secret per process; set `BAZAAR_BLUFF_SEED` for a reproducible simulator run.
- **Learning:** every scored message becomes a `tactic` row in `learnings` (`source = outcome`). The
  scores: their next price moved toward us +1, held 0, moved away −0.5, deal +1 (+0.5 within 3
  messages), they walked −1. A message still unanswered when we send the next one scores nothing. A
  cooloff, a strike or a flag on our message scores −10 and turns that tactic off for that counterparty
  for the rest of the day. Two penalties in a day mute every tactic to it. Three tries with no gain turn
  a tactic off for the day. A penalty after our plain words also mutes that counterparty: the price
  upset them, not a lie. The taker reads strikes and flags from its feed. `dealer buy` reads the
  keyless feed (2 s, no retry) at the start of each tick, before that tick's message. `duel run`
  reads it after its sends. A flag can only be matched when the game's
  answer to our send carries our message id; that is unverified on the real game. N3's recall never
  returns `tactic` rows, so they never reach Jev or the words context.
- **Private:** the tactic id and why it was picked go to the decision row under input keys that
  `/state`, `/events` and `/health` never list.
- **Kill switches:** `BAZAAR_BLUFF=0` on a service turns its tactics off without a code deploy (the
  variable is declared `preserve()` in `.railway/railway.py`). Only unset, 1, true, on or yes leave them
  on; any other value turns them off. `bluff_enabled` = false in GUARDRAILS.md
  turns them off everywhere at the next deploy. Either one brings back today's words.

### Jev decides: duels and the maker (spec §3 step 4, §7.1)

Code lists only the **legal** moves inside `GUARDRAILS.md` and our own limit; Jev (TypeSafe
`jev-1.13.0`, `TYPESAFE_API_KEY`) picks one with probability floats; the hard limits authorize.
`undecided` (below the bar, no key, timeout, network, no tick budget) is **never a yes**: it keeps
today's deterministic move. On by default; `--no-jev` turns it off.

| Who | Question (pack) | Candidates (all legal) | `undecided` keeps |
|---|---|---|---|
| `duel run` | `duel_move` (`questions/duels.json`, choice) | accept the rival's offer only if strictly inside our limit after the worst-case cost of days (and `jev_can_accept_early`); counter at today's concession price, never past our limit; hold, not in the last `duel_endgame_ticks` (there an inside offer is the only move) | today's `duel_move` |
| `duel run` (two-issue sessions) | `rival_cares_about_days` (noul) | `yes`: our counter carries the rival's own days, if our price still holds after them | days 5 |
| `agent maker` | `list_price_choice` (`questions/maker.json`, choice) | `aggressive` / `fair` / `quick_sale` around the strategy's price, minus any the sell floor (page bonus included), the cash floor, the spend cap or the price cap refuses | the strategy's price |
| `agent maker` | `reprice_or_hold` (noul) | hold a stale offer only while its old price is still legal | reprice |

Per tick, Jev is asked only with ≥ 4 s of the tick left (`jev_min_budget_s`), once per duel and round
or per listing and candidate set (cached), and with `jev_timeout_s` (3 s) per call. The duel player asks
about every live duel at once on worker threads, so a duel accept still lands inside the taker's 2 s
grace. Every verdict and its floats are on the `decisions` row (`jev`: verdict, value, probabilities,
reason, digest; duels are `agent = duels`) and on the trace (`jev_verdict`, `jev_choice` events on the
duel's tick span). Every call is also a masked line in `.local/jev-decisions/<day>.jsonl`; when a duel
leaves `/api/duels`, or a live offer fills (right) or expires unfilled (wrong), its verdict gets an
outcome line, so calibration per question reads:

```sh
uv run python -m bazaar_agent.jev report --directory .local/jev-decisions
```

## Agent runtime (Claude Agent SDK)

A Mastra-style agent layer in Python, on the Claude subscription: one **desk** (the orchestrator) hands
each request to a **subagent** with a focused prompt and its own tool allow-list. The tools are Team 1's
capabilities as typed MCP tools, and every write meets the guardrails twice. Code:
`src/bazaar_agent/runtime/` (`tools.py`, `hooks.py`, `agents.py`, `desk.py`, `desk_models.py`, `mcp_server.py`).

```
 operator ── bazaar agent chat ─┐            ┌── teammate's Claude Code ── Authorization: Bearer ──┐
           ── bazaar ask ───────┤            │                                                      │
                                ▼            │                                         bazaar mcp serve
   desk (main thread: Agent + status, clock, rules, alerts, threads)        (Streamable HTTP /mcp, Railway
     │  Agent tool, foreground, our subagents only                           bazaar-mcp: 401 without the
     ├─► strategist  every read + steer                                      token, rate limit per token)
     ├─► buyer       every read + dealer_buy, sell_bid                                   │
     ├─► seller      every read + sell_list, sell_cancel                                 │
     └─► duelist     every read + duel_move                                              │
           │ tool call                                                                   │
           ▼                                                                             │
   PreToolUse hook ── allow-list of the caller ── guardrails.check() (live /me, clock,   │
           │          open offers, Postgres ledger) ── DENY with the violated rules      │
           ▼                                                                             ▼
   in-process SDK MCP server "bazaar" ◄──────── ONE set of tool specs (runtime/tools.py) ────────►
           │  reads: the CLI's own functions (status, clock, strategy, curves, tape, teams, book,
           │         traders, alerts, rules, threads, thread)
           │  writes: actions.run_write → guardrails.check() again → DRY RUN unless BAZAAR_LIVE=1
           │          (dealer_buy, sell_list, sell_bid, sell_cancel, duel_move, steer)
           ▼
   PostToolUse hook ── decisions row per write, executions row per send, OTel span  ──► game

   models: before each request, Jev (model_for_desk_role, ONE call for every uncached role) picks a
   Claude model for the desk (set_model) and for each subagent (the hook sets its Agent call's model)
```

```sh
uv run bazaar agent chat                 # talk to the desk (dry run unless BAZAAR_LIVE=1)
uv run bazaar agent chat --once "buy LAV-09 under 90"   # one request, print the transcript, exit
uv run bazaar ask "sell my spare LAT-03 for at least 6" # through the desk when CLAUDE_CODE_OAUTH_TOKEN is set; never trades
uv run bazaar ask --no-desk "..."        # the one-call intent parser (also the automatic fallback)
uv run bazaar agent tools                # every tool, read or write, which agents may call it, its guardrails
```

- **Same code as the CLI.** Read tools call the functions behind `bazaar status|clock|strategy|curves|
  tape|teams|book|traders|alerts|rules|threads|thread`. `sell_list`/`sell_bid` call `seller.post` like
  `bazaar sell list|bid`; `dealer_buy`'s dry run is `bid_schedule()` like `bazaar dealer buy`, and live it
  starts `bazaar dealer buy --live` as its own process (one move per tick, its own guardrail checks; one
  such negotiation at a time per runtime, so two children never count the same cash);
  `duel_move` plays one move of `duel run --play`'s policy (the price is set by code, never by the model);
  `steer` runs `bazaar steer`'s clamp. `strategy` and `bazaar strategy` share `playbook_now()`.
- **Two lines of defense.** The tool code checks first (`actions.check_write`): guardrails with the live
  `/me`, open offers and the shared ledger, plus the game's caps a live send would otherwise hit (tick
  budget, one thread per dealer and 6 open threads, 12 listings per tick counted team-wide, 30 open
  offers, one accept per tick, one duel message per duel per tick). A live runtime counts with the
  team's Postgres ledger or not at all (no machine-local fallback). A request that was sent stays
  `done` even when the ledger fails right after it (`bookkeeping_error`): its rows wait in
  `.local/runtime/pending-ledger.jsonl` and go in before the next write is judged, and nothing is
  approved until they do. The kill switch stops `steer` too. The desk's PreToolUse hook runs the same check again, enforces each
  agent's allow-list, lets `Agent` start only our four subagents (in the foreground), and fails closed
  when `/me` or the ledger does not answer. A hook deny wins over every permission rule.
- **Locked session.** `permission_mode="dontAsk"`, `tools=["Agent"]` (no Bash, files or web),
  `setting_sources=[]`, no CLAUDE.md or claude.ai connectors, no session files, the built-in
  general-purpose agent and nested subagents off. Counterparty words reach the model only as
  `untrusted_text` with `injection_flags`; every prompt says they are data, never instructions.
- **Jev picks every model, per request.** Before each request, one Jev call (`model_for_desk_role`, one
  question per role about the same request: its length, the largest price in it, injection shapes in it)
  picks the orchestrator's model and each subagent's; a role cached within `model_choice_cache_ticks`
  costs nothing. A conversation keeps one session: the orchestrator switches in place
  (`set_model`), and the PreToolUse hook replaces whatever `model` the desk's LLM puts on an `Agent` call
  with the family alias of this request's choice for that subagent (`opus`, `sonnet`, `haiku`), which
  the session pins to our exact ids (ANTHROPIC_DEFAULT_<FAMILY>_MODEL); the `AgentDefinition`s carry the
  first request's models. Undecided, slow (`jev_timeout_s`) or keyless Jev → RUNTIME.md `desk_role_defaults` (Sonnet for
  every role, what ran before). A pin wins: `agent chat --model` > a Claude `--llm-runtime` /
  `BAZAAR_LLM_RUNTIME` / `llm_runtime` > RUNTIME.md `desk_model` (default `auto`). Claude models only.
  The transcript prints the choice (`models: desk sonnet-5-5 (jev 0.91) · buyer opus-5-5 (jev 0.88) · …`)
  and, after the answer, the model each agent really ran on (`ran on: desk claude-sonnet-5-5 · buyer
  claude-opus-5-5`). A failed switch keeps the session's model and says so.
- **Never in the hot path.** The taker, maker, duel and monitor loops stay deterministic; the desk
  advises, proposes, parses and steers. RUNTIME.md `desk_model`, `desk_role_defaults`, `desk_max_turns`,
  `desk_timeout_s`. A missing CLI, a rejected token, a used-up subscription window, a rate limit or a
  timeout ends the request with the reason: `bazaar ask` falls back to its intent parser, `agent chat`
  prints the deterministic commands.
- **`bazaar ask` never trades**, BAZAAR_LIVE or not: its desk is always a dry run. Only `agent chat`
  follows BAZAAR_LIVE=1, like the taker and maker.
- **Audit and secrets.** Every write call is a `decisions` row (`desk/<agent>` or `mcp`, dry runs too,
  failed tool calls through `PostToolUseFailure`) and every send an `executions` row. Tool answers, rows and printed lines are scrubbed: our secret
  values, key and token shapes, bearer tokens and every URL are cut out.

### The tools as a remote MCP server (`bazaar-mcp`)

`bazaar mcp serve` serves the same tool specs over the MCP Python SDK 2.x Streamable HTTP transport
(`/mcp`, stateless JSON responses) for a teammate's own Claude Code: **https://bazaar-mcp-production.up.railway.app/mcp**. It holds no Claude token: each
teammate's Claude Code is the client. Railway service `bazaar-mcp` (declared in
`.railway/railway.py`; its public domain was generated once with `railway domain --service bazaar-mcp --port 8080`).

- `Authorization: Bearer <BAZAAR_MCP_TOKEN>` on every request (constant-time compare), else `401`;
  `GET /health` is the only public route (no mode, no game state). The server refuses to start without
  a random token (32+ characters, 16+ distinct ones).
- Rate limits per token: 5 HTTP requests/s (burst 20), then RUNTIME.md `mcp_calls_per_minute` (30)
  tool calls, because every caller shares our one team key (5 req/s for the whole team).
- Write tools are DRY RUN unless `BAZAAR_LIVE=1` is set on that service (never in `railway.py`), and
  the guardrail check runs inside the server for each of them, against the shared Postgres ledger only
  (no machine-local fallback: no ledger, no write). Even live, the server never starts a `dealer buy`
  child (live negotiations start from the desk or the CLI, one place) and only previews `steer`
  (`steering.json` lives on each machine's volume). A write whose audit row fails leaves a scrubbed line
  in `.local/runtime/audit-recovery.jsonl`. No tool returns a key, token, password or URL, and
  team-written text (thread topics, venue names in alerts) comes back as `untrusted_text`. Only our
  team's tools: no flags, no free-text messages, no `to` on offers, no key parameter. Every write call
  is a `decisions` row with agent `mcp`.

Set the token once, piped so it never lands on a command line, in shell history or in a log:

```sh
python3 -c 'import secrets; print(secrets.token_urlsafe(48), end="")' \
  | railway variable set BAZAAR_MCP_TOKEN --stdin --service bazaar-mcp
```

A teammate gets the value from the service's Railway variables (or the team lead's `.env`), exports it in their shell
(`export BAZAAR_MCP_TOKEN=...`, never in a committed file), and adds the server to Claude Code
([docs](https://code.claude.com/docs/en/mcp)):

```sh
claude mcp add --transport http bazaar https://bazaar-mcp-production.up.railway.app/mcp \
  --header "Authorization: Bearer ${BAZAAR_MCP_TOKEN}"
```

Locally: `BAZAAR_MCP_TOKEN=... uv run bazaar mcp serve` (127.0.0.1:8765, DNS-rebinding protection on).
## Evals (how well each settled decision scored)

Jev chose the design (`questions/evals.json`, verdicts logged): **online outcomes**, scored after each
decision settles, **stored in Postgres** (the source of truth the dashboard reads) and **attached to
the matching Phoenix trace** as an annotation. The evals measure what the game scores (RULES.md
"Scoring"), never the number of trades, fees or luck. A pass of `bazaar evals run` reads Postgres only
(no game API call), and nothing in the evals uses the team key: they cost nothing of its 5 req/s.

```sh
uv run bazaar evals run                 # score everything settled; idempotent (a re-run changes 0 rows)
uv run bazaar evals run --since-tick 300
uv run bazaar evals run --every-ticks 6 # keep running on the game clock (keyless /api/clock)
uv run bazaar evals report              # scorecard, dealer ladder, worst 5 per target, Jev calibration
uv run bazaar evals report --json       # the same for the dashboard
uv run bazaar duel done                 # one /api/duels?done=true read: finished duels into Postgres
uv run bazaar evals import-duels .local/duels/duels.jsonl   # a duel runner's log into Postgres, no API
```

| Target | Score (0..1) | Where the inputs come from |
|---|---|---|
| **duel** | Deal: our surplus over the pie the rival revealed (its best offer bounds its limit), times the value kept after decay `(1 - decay) ** rounds`. The API has no `share` or pie (verified on the practice session), so the share part is an upper bound. No deal: 0, labelled `bad` when the rival offered inside our limit (money left on the table), `ok` when it never did. A deal outside our limit: 0, `bad`, flagged. | `duels` table (written by `bazaar duel run` each tick, finished duels from `?done=true`), `duel.closed` feed events |
| **dealer** | The share of the dealer's price range a deal captured: from its list price (highest opening ask seen, any team) to its best fill seen (lowest fill), per dealer and rarity. No deal: 0. A deal at the dealer's opening price is called out: it does not count toward unlocking a level. View `eval_ladder`: best three per level, a missing one as zero. | `feed_events` (our threads), `dealer_curves` (every team's), `traders.level` |
| **trade** | Surplus at our private values over the trade's gross: a bought card at its `your_value` in the first `/me` snapshot holding it, minus the cash that left (the snapshots' cash change, else price + fee); a sold card the other way round. A loss is 0 and `bad`. When the trade came from a live decision with a decided Jev verdict, the verdict is marked right or wrong. | `feed_events` settlements with us as a party and no dealer, `snapshots`, `decisions` |
| **market_test** | Stub until we run a venue: the official `bench_efficiency` from `/me`, once a day. | `snapshots.score` |

Labels: `good` ≥ 0.6, `ok` ≥ 0.3, `bad` below. Every outcome carries an explanation a human can check
("Deal at 138 vs our limit 109: +29.0 P, 18.8 P after 7 rounds (kept 65%) …") and its numbers in `details`.

**Where to see them.** `bazaar evals report` in the CLI; in Phoenix, on each `duel` / `negotiation` /
`<agent> tick N` trace as an annotation named `duel_pie_share`, `ladder_share` or `trade_surplus`
(annotator `CODE`, identifier `bazaar-evals:<subject>`: a re-score updates it in place); in Postgres for the
dashboard: `outcomes` (one row per `(target, subject)`, e.g. `duel:85`, `thread:101`), and the views
`eval_scorecard`, `eval_ladder`, `eval_jev_calibration` (shapes in `docs/services.md`). The report puts
the organisers' own numbers from the newest `/me` snapshot (`duel_points`, `ladder_points`, …) beside ours.

**Where they run: inside the agents** (Omar, 2026-10-03; no Railway service). The duel player scores
duels, the taker the dealer ladder and every team trade (its accepts and the maker's fills), the maker
the Market Test. Each one that trades (live, or `duel run --play`) starts one pass every 6 ticks
(`--evals-every N` on `duel run`, `agent taker`, `agent maker`; `0` = off, and off by default for a dry
run, so a laptop never rewrites the team's scores; the first pass comes 6 ticks after start), after the tick has sent everything, on
a background thread: Postgres and Phoenix only, zero game calls, a pass still running is never doubled,
and an error is logged and dropped. From a laptop, `uv run bazaar evals run` scores everything once (or
`--every-ticks 6` on the game clock, keyless `/api/clock`). After a restart, `bazaar duel run` reads
`?done=true` once, so a duel that finished while it was down is stored. One process per agent kind
scores (an advisory lock, which `bazaar evals run` also takes per kind; a frozen holder's session ends
after 2 idle minutes). Against the
simulator (`BAZAAR_SIM`), scores stay in its Postgres and traces go to the `bazaar-sim` Phoenix
project: simulated duel and thread ids collide with the game's.

## Services and public URLs (start here for observability and the dashboard)

Railway project **`heartfelt-warmth`** (environment `production`, region `europe-west4`):
https://railway.com/project/05a9de65-622b-4754-a0f0-be4d7f54ec51?environmentId=912b1525-f223-4f0d-bfc6-8db725f7a6e0

| Service | Public URL | Private (inside Railway) | Role | State |
|---|---|---|---|---|
| `phoenix` | https://phoenix-production-6aa3.up.railway.app (login `admin@localhost`, password in its Railway variables) | `phoenix.railway.internal:6006` (OTLP/HTTP), `:4317` (gRPC) | traces UI for every negotiation, duel, monitor tick and CLI line | running |
| `Postgres` | `iriguchi.proxy.rlwy.net:28880`, db `railway`, user `postgres`, SSL (password: Postgres service → Variables) | `${{Postgres.DATABASE_URL}}` | the team's shared memory (feed, tape, dealer curves, traders, snapshots, alerts, decisions) | running |
| `bazaar-duels` | none (worker, no HTTP) | — | the team's ONE duel player (`duel run --play`) | running |
| `bazaar-monitor` | — | — | deleted Sat 2026-10-03 04:27 Madrid (service + volume). The monitor runs in the CLI on a laptop (`uv run bazaar monitor --notify`) | deleted |
| `bazaar-taker` | https://bazaar-taker-production.up.railway.app (`/health`, `/state`) · wss://bazaar-taker-production.up.railway.app/events | `bazaar-taker.railway.internal:8080` | autonomous buyer (`bazaar agent taker`): board asks + dealer desk; read-only status | **LIVE** since Sat 01:45 Madrid (`BAZAAR_LIVE=1`, set by hand) |
| `bazaar-maker` | https://bazaar-maker-production.up.railway.app (`/health`, `/state`) · wss://bazaar-maker-production.up.railway.app/events | `bazaar-maker.railway.internal:8080` | autonomous market maker (`bazaar agent maker`): asks, bids, reprices; read-only status | **LIVE** since Sat 01:45 Madrid (`BAZAAR_LIVE=1`, set by hand) |
| `bazaar-mcp` | https://bazaar-mcp-production.up.railway.app/mcp (bearer token; `/health` public) | `bazaar-mcp.railway.internal:8080` | the runtime tools as a remote MCP server (`bazaar mcp serve`) for teammates' Claude Code | running, dry run (no `BAZAAR_LIVE`) |
| `bazaar-sim` | https://bazaar-sim-production-1d48.up.railway.app (`/api/health`, `/sim/state`) | `bazaar-sim.railway.internal:8080` | the simulated Bazaar for testing agents (keys `sim-team1`…`8`), world in the `bazaar_sim` database | running |
| `bazaar-events` | (planned) public WebSocket + REST for the dashboard | — | streams our events from Postgres to the web dashboard | planned |

**Game endpoints a dashboard can use directly** (organiser API, `https://bazaar.causaprima.ai`):
keyless `GET /api/feed?limit=500` (public events, last 500 only), `/api/clock`, `/api/leaderboard`,
`/api/dealers`, `/api/levels`, `/api/venues`, `/api/venues/{id}/offers`. The live stream
`GET /api/events/stream?scope=team` needs our team key, so it must stay server-side (never in a
browser): the dashboard should read our events through `bazaar-events` or Postgres, not with the key.

## Production on Railway (always on)

Railway project `heartfelt-warmth`, environment `production`, region `europe-west4`. Everything but
the database is code in [`.railway/railway.py`](.railway/railway.py) (Railway Infrastructure as
Code, Python authoring, beta): change it by PR.

| Service | What runs | Data | Notes |
|---|---|---|---|
| `bazaar-duels` | `bazaar duel run --play` (offers/accepts inside `GUARDRAILS.md`) | volume `bazaar-duels-data` on `/app/.local` | the team's ONE duel player; first claim on the team's accept each tick |
| `bazaar-taker` | `bazaar agent taker` + status on `PORT` 8080 (healthcheck `/health`) | volume `bazaar-taker-data` on `/app/.local` | **LIVE**: `BAZAAR_LIVE=1` set by hand Sat 01:45 Madrid; the file `preserve()`s it |
| `bazaar-maker` | `bazaar agent maker` + status on `PORT` 8080 (healthcheck `/health`) | volume `bazaar-maker-data` on `/app/.local` | **LIVE**: `BAZAAR_LIVE=1` set by hand Sat 01:45 Madrid; the file `preserve()`s it; never accepts |
| `bazaar-mcp` | `bazaar mcp serve --host 0.0.0.0` on `PORT` 8080 (healthcheck `/health`) | volume `bazaar-mcp-data` on `/app/.local` | bearer `BAZAAR_MCP_TOKEN` (`preserve()`), dry run unless `BAZAAR_LIVE=1` is set by hand |
| `bazaar-sim` | `bazaar-sim serve` on `PORT` 8080 (healthcheck `/api/health`), one tick every 10 s | database `bazaar_sim` (schema `sim`) on the team's Postgres | https://bazaar-sim-production-1d48.up.railway.app; the generated domain is not IaC (Railway does not declare generated domains) |
| `bazaar-live` | [bazaar-live](https://github.com/claude-hackaton-madrid-team-1/bazaar-live)'s `node server/index.ts` on `PORT` 8080 (healthcheck `/health`): the show and its TTS proxy | none | reads only the agents' public `/health`, `/state`, `/events`; `ELEVENLABS_API_KEY` / `GEMINI_API_KEY` `preserve()` (both optional); the generated domain is not IaC |
| `phoenix` | `arizephoenix/phoenix:version-20.19.0` (same pin as `docker-compose.yml`), auth on | volume `phoenix-data` on `/mnt/data` | UI: https://phoenix-production-6aa3.up.railway.app |
| `Postgres` | `postgres-ssl:18` + pgvector | its own volume | managed in the dashboard, NOT by `.railway/railway.py` |

- **Builds.** `bazaar-duels` builds this repo's `main` with Railpack (Python 3.12 through
  `RAILPACK_PYTHON_VERSION`, `uv sync --locked --no-dev`, editable so `vendor/` and
  `GUARDRAILS.md` resolve from `/app`). Every push to `main` that touches `src/`, `vendor/bazaar-kit/`,
  `pyproject.toml`, `uv.lock`, `GUARDRAILS.md`, `STRATEGY.md`, `RUNTIME.md`, `questions/` or `.railway/`
  redeploys it; README-only commits are skipped. Restart policy: always.
- **No OFF services.** Railway redeploys a service's last image whenever an apply changes its config,
  source or not. On Fri 23:14 UTC an apply that added `RUNTIME.md` to the shared watch patterns revived
  the off `bazaar-monitor` (an `iac-change-set` redeploy) and it held one of the key's six live-stream
  slots until `railway down` (Sat 01:55 Madrid). So a service we do not run is not declared at all:
  `bazaar-monitor` and its volume are no longer in `.railway/railway.py` (Omar, 2026-10-03: the monitor
  runs in the CLI; he deletes the service and its volume by hand, and nothing is applied before that),
  nor is `bazaar-evals` (the evals move into the agents; its service was deleted the same night).
  `tests/test_railway_iac.py` fails on a service without a source, on taker or maker not built with
  `agent()`, and on a declared monitor or evals service. To run the monitor on Railway again, re-add its volume and
  `runtime("bazaar-monitor", "monitor", ...)` in `.railway/railway.py`, apply, and stop the laptop monitor.
- **Variables.** `DATABASE_URL = ${{Postgres.DATABASE_URL}}` (private network),
  `PHOENIX_COLLECTOR_ENDPOINT = http://${{phoenix.RAILWAY_PRIVATE_DOMAIN}}:6006`,
  `PHOENIX_API_KEY = ${{phoenix.PHOENIX_API_KEY}}`, `BAZAAR_TRACING=1`, `BAZAAR_DATA_DIR=/app/.local`.
  Secrets (`BAZAAR_KEY`, `TYPESAFE_API_KEY`, `CLAUDE_CODE_OAUTH_TOKEN`, `SIM_ADMIN_TOKEN`, `PHOENIX_SECRET`,
  `PHOENIX_DEFAULT_ADMIN_INITIAL_PASSWORD`, `PHOENIX_API_KEY`) are only in Railway; the file says `preserve()`. Set or rotate one without
  it touching a command line: `printf %s "$VALUE" | railway variable set NAME --stdin --service <svc>`.
- **Pause writes** (the guardrail kill switch): `railway ssh --service bazaar-duels -- touch /app/.local/PAUSE`
  pauses that one service (the file is on its own volume, so it survives redeploys; `rm` it to resume).
  Everything that trades, at once, then check each file is there (`/health`'s `paused` is the game
  clock's flag, not this file):
  `for s in bazaar-duels bazaar-taker bazaar-maker; do railway ssh --service "$s" -- touch /app/.local/PAUSE; done`
  and `for s in bazaar-duels bazaar-taker bazaar-maker; do railway ssh --service "$s" -- ls /app/.local/PAUSE; done`.
  A pause keeps our open offers on the board: see "Stop one" below to withdraw them. A laptop running
  a `--live` command reads its own `.local/PAUSE`: touch that one too.

### Open Phoenix

1. Open https://phoenix-production-6aa3.up.railway.app and sign in as `admin@localhost`.
2. The password is the `PHOENIX_DEFAULT_ADMIN_INITIAL_PASSWORD` variable: Railway dashboard →
   project `heartfelt-warmth` → service `phoenix` → **Variables** (click the eye). No forced reset:
   `bazaar obs bootstrap` cleared it. If you change the password in Phoenix, the variable no longer
   matches it (Phoenix reads the variable only when it first creates the admin).
3. Project `bazaar`: one trace per `monitor tick N` / `duels tick N`, plus a `duel` trace per duel.

### Send your laptop's traces there

Create your own key in Phoenix (**Settings → API Keys**: a user key; a system key if it is a
shared bot), then in `.env`:

```sh
BAZAAR_TRACING=1
PHOENIX_COLLECTOR_ENDPOINT=https://phoenix-production-6aa3.up.railway.app
PHOENIX_API_KEY=<your key>
```

`uv run bazaar obs status` should say `up (HTTP 200)` and `PHOENIX_API_KEY set`, and
`uv run bazaar obs spans` lists the newest spans by name (it reads them with that key).

### Change, restart, redeploy

```sh
railway link --project heartfelt-warmth --environment production   # once per clone
uv run --group infra railway config plan    # preview what .railway/railway.py would change
# apply ONLY when the plan says "0 to destroy" (a delete you did not ask for means: stop and ask)
uv run --group infra railway config apply   # apply it (a partial: it never touches Postgres)
railway logs --service bazaar-duels        # `tick N` lines and one line per duel move
railway redeploy --service bazaar-duels --yes   # a fresh container of the current build
railway restart --service bazaar-duels --yes    # restart in place
```

A new Phoenix (a fresh volume) needs its ingestion key once, piped straight into Railway:
`PHOENIX_DEFAULT_ADMIN_INITIAL_PASSWORD=... uv run bazaar obs bootstrap --url <phoenix URL> | railway variable set PHOENIX_API_KEY --stdin --service phoenix`,
then redeploy `bazaar-duels`.

- **The guardrail ledger is shared.** Accepts per tick, spend per game hour and listings per tick
  live in the Postgres `ledger` table, so `bazaar-duels`, `bazaar-taker`, `bazaar-maker` and a laptop's
  `dealer buy` see one count (see "Autonomous agents"). Every service that runs live needs
  `DATABASE_URL` set to the shared Postgres: without it a live process exits at start ("refusing to
  trade"). The ledger reconnects after a drop (retried at most every 15 s); while Postgres is down a live
  process sends nothing (fail closed), and a dry run counts on its own `ledger.jsonl` until Postgres answers.
  The log line `ledger: postgres ledger table on <host>:<port> (shared, …)` says which one is in use.
- **Live or dry run** (a team decision, not a deploy). **The taker and the maker are LIVE since
  Sat 2026-10-03 01:45 Madrid** (`BAZAAR_LIVE=1` set by hand on both; nothing trades before the doors
  open at 09:00). `.railway/railway.py` `preserve()`s `BAZAAR_LIVE` and never sets it, so a
  `railway config apply` keeps whatever is set by hand.
  - **Stop one:** first its kill switch, which holds at once with no redeploy:
    `railway ssh --service bazaar-taker -- touch /app/.local/PAUSE` (PAUSE lives on each service's own
    volume: "Pause writes" above pauses all of them). Then make it a dry run:
    `railway variable delete BAZAAR_LIVE --service bazaar-taker` (or `bazaar-maker`), check
    `railway variable list --service bazaar-taker --json | jq -e 'has("BAZAAR_LIVE") | not'` prints `true`
    (never the plain `variable list`: it prints every secret), and that `/health` says
    `mode: dry` after the redeploy (if it still says `live`, `railway redeploy --service bazaar-taker
    --yes`). `rm` the PAUSE file once the dry run is confirmed.
  - **Neither withdraws our open offers.** A dry run sends nothing (no cancels) and PAUSE holds by design,
    so up to 30 asks and bids the maker posted stay on the board and can still fill. To withdraw them:
    `uv run bazaar sell offers`, then `uv run bazaar sell cancel <offer_id> --live` for each.
  - **Turn one on:** `printf 1 | railway variable set BAZAAR_LIVE --stdin --service bazaar-taker` (it
    redeploys); `/health` says `mode: live`.
- **Known limit: other state in `BAZAAR_DATA_DIR` is per container.** `steering.json`: a laptop
  `bazaar steer` does not reach Railway's duel player; steer it in its container
  (`railway ssh --service bazaar-duels -- /app/.venv/bin/bazaar steer "..."`). The runtime LLM runs
  on Railway once `CLAUDE_CODE_OAUTH_TOKEN` is set on `bazaar-duels`, `bazaar-taker` and `bazaar-maker`
  (see "LLM on the Claude subscription"). Until then, every LLM path falls back to its template or
  default. RUNTIME.md `llm_words` is off anyway.

## How it fits together

```
CLI / buyer / seller agents ─► intents ─► decider (valuation → candidates → RAG → Jev → policy)
                                               ▲                                   │ approved
feed + /me per tick ─► collector ─► intel (book, tape, dealer curves, teams) ─► learnings
                                                                                   ▼
                                                     executor (fresh runtime, SDK only) ─► game
```

- Architecture and data model: [`.ai/specs/01-spec.md`](.ai/specs/01-spec.md)
- Phases, backlog and steps: [`.ai/specs/02-plan.md`](.ai/specs/02-plan.md)
- Game briefing and rules: [`docs/briefing.md`](docs/briefing.md), [`vendor/bazaar-kit/RULES.md`](vendor/bazaar-kit/RULES.md)
- Agent contract for every AI tool: [`AGENTS.md`](AGENTS.md) (generated from `.ai/context.md`)
- How the agent harness works: [`docs/agent-harness.md`](docs/agent-harness.md)

## Before you merge to main

A merge to `main` redeploys `bazaar-duels` (and taker, maker, MCP) on Railway, and a duel left unanswered at its
deadline scores 0 for both sides. Merge through the guard:

```bash
scripts/merge_safe.sh 123          # runs the guard, then `gh pr merge 123 --merge` only when it says SAFE
uv run bazaar deploy-guard         # the guard alone: exit 0 safe, 1 not; --json for scripts
```

It reads `/api/clock`, our live `/api/duels` and `/api/schedule` (three GETs, no writes) and says DO NOT MERGE while
a live duel of ours is within `deploy_guard_duel_ticks` (4) of its deadline, or a Market Test bench runs or any
scheduled event starts within `deploy_guard_bench_ticks` (10) ticks (GUARDRAILS.md "Live guard"). It prints the next
safe tick and window. A read it cannot make, or a payload it cannot parse, means DO NOT MERGE.

### Circuit breakers and the live watchdog

`uv run bazaar breaker list|trip <scope> --reason "..."|reset <scope>` stops one kind of write in every process
(`duel_accept`, `team_swap`, `dealer_buy`, `board_accept`, `maker_post`, `dealer_sell`) from its next tick, through
`guardrails.check()`; cancels and closes are never stopped. The table is read once per tick with a 1 s budget and
fails OPEN (the ledger already fails closed). The taker's watchdog (`live_watchdog_enabled`) reads Postgres after its
sends and trips a scope on a buy above value or a sell below it, a bad team swap, or price spam (timed trip); a duel
about to lapse with an acceptable offer is logged CRITICAL and a refusal storm WARN, never tripped. Every trip is a
WARN line, a `decisions` row (agent `guard`) and a `guard_trip` learning.

## Working as a team (humans and agents)

- **Team memory is public:** `.ai/memory.md` is committed. Append findings, gotchas and build
  errors there (newest at the bottom). Never write a key or token in it.
- **README stays current by itself, in two places:**
  - the pre-commit hook runs `scripts/readme_status.py` on every commit: it rebuilds the "Live status"
    block (backlog from the plan, CLI commands, latest team memory) and stages README.md;
  - CI on every push to `main` (`.github/workflows/readme.yml`) rebuilds that block on the merged
    code and the "Activity" block (recent merges, open PRs), then commits it as `github-actions[bot]`.
  On a conflict inside a block, take either side and rerun `python3 scripts/readme_status.py`.
  Keep `.ai/specs/02-plan.md`'s task index current: it is what the backlog table shows.
- **Architecture page is generated too:** `scripts/architecture_page.py` renders `docs/architecture.html`
  from `docs/architecture.status.json` (box statuses, lists, links; edit the JSON, never the HTML) plus the
  plan's task index, in the same hook and CI job. The **roadmap** is `timeline` in that JSON (a Linear-style view): `start`/`end` of the axis and
  `markers` (`freeze`/`deadline`) as Madrid times `YYYY-MM-DDTHH:MM`, `closed` door bands, the organisers' `events`, and
  `lanes` of `bars` `{title, start, end, status: done|wip|partial|todo, priority: P0-P3, owner?}`; overlapping bars stack. Git hooks and CI cannot publish claude.ai artifacts, so
  after every merge that changes `docs/architecture.html`, the coordinator republishes it to
  https://claude.ai/artifact/9KKsCg2P2gYqRG8CDpDD39.
- **Every PR is reviewed before it merges (Greptile is disabled):** run `/pr-review <PR number>`. The
  `pr-reviewer` sub-agent merges the PR onto current `main` in a scratch worktree, runs the gate, and posts
  a P0-P3 verdict on the PR. Fix every P0 and P1, re-run until it says APPROVE, then ask for the merge.
  Run `sh scripts/sync-ai-docs.sh` once per clone or worktree so Claude Code sees the agent and the command.
- **Backlog:** GitHub issues are the source of truth; the plan mirrors them.
- **Never** push from an agent, never commit `.env`, one team key only.

## Live status

<!-- BAZAAR:STATUS:START -->
<!-- Generated by scripts/readme_status.py on every commit. Do not edit by hand. -->

### Backlog (from `.ai/specs/02-plan.md`)

| Task id | Title | Phase | Status |
|---|---|---|---|
| N2 · was #21 | Feed capture + dealer curves | 0 → 1 | 🔵 `bazaar monitor` (#32), real-time stream (#40), thread-fill fix (#58); open: Abuela `open`/`limit`/β estimate, ladder view (PR #43) |
| N4 · was #2 | Team key + API client + fixtures | 0 | ✅ key works; SDK bridge; API fixtures (#26) |
| N9 · was #3 | Tick loop, governor, scheduler, kill switch | 0 → 1 | ✅ tick loop + budget + `.local/PAUSE`; the kill switch HOLDS (no writes, offers stay open) and `bazaar flatten` is the explicit cancel-everything (PR #72, from #68) |
| [N14](N14-spec.md) · was #8 | Abuela negotiator (concession curve) | 0 | ✅ 4 negotiated deals (7/9/9/22) |
| [N14](N14-spec.md) · was #9 | Ladder maximizer + reach L2 | 0 → 2 | 🔵 level 2 reached (El Chato unlocked); first Chato deal walked (he held 33 vs our max 24); best-3 ladder table is `bazaar evals report` (#58); `egg.found` alert and the L2 rule write-up ⬜ |
| [D1](D1-spec.md) · was #4 | Duel logger (practice h2) | 0 | 🔵 duels logged and stored (#41, #58); open: committed C1–C6 answers, full-session fixtures in `tests/fixtures/duels/`, live deadline proof |
| N1 (new) | Memory schema + repository + Railway-ready DB | 1 | ✅ (#29, #32, #33) |
| N2 (new) | Intel: order book, tape, competitor profiles | 1 | ✅ (#29, #32) |
| N3 (new) | **P0 (Omar)** · Learner / auto-evolve with a hybrid RAG: lessons from every outcome, BM25 + pgvector + RRF + local cross-encoder `recall()`, learned ladder parameters inside GUARDRAILS | 1 | 🔵 PR A #96 (stacked on #89): lessons + `trader_behaviors` + embeddings + hybrid `recall()` in the taker · PR B (stacked on #96): auto-evolved ladder (start/step/walk, skip above cap) per dealer × class, lessons into Jev (`offer_is_worth_accepting`, `duel_move`, `list_price_choice`) + words, `Query.where` + `record_lesson` for N14, MCP `learnings`, `bazaar learnings --policy` |
| N5 · was #1 | Decision model: decider + Jev packs + policy | 1 | 🔵 autonomous taker + maker (`bazaar agent`), every move in `decisions`; LIVE on Railway since Sat 01:45 Madrid (`BAZAAR_LIVE=1` by hand) |
| [S1](S1-spec.md) · was #10, #24 | Executor firewall, offer inspector, flags | 1 → 2 | ✅ offer inspector before every accept: dealer, board, duel (#146, takes over Marius #93); bad-faith flags as proven decision rows, off and opt-in per dealer, injection tagging + hostile-text tests (#152); forge-proof flags report (#176). Open (98-nice-to-haves): per-message human confirmation of a flag, team-wide flag cap |
| N4 (new) | `service.py` + CLI + bazaar skill + commands | 1 | 🔵 CLI + skill done; `service.py` seam ⬜ |
| N5 (new) | Jev port to Python (judge, mask, log, report, parity) | 0 → 1 | ✅ (#29, #31); recorded-fixture parity test ⬜ |
| N6 (new) | Voice interface: ElevenLabs agent + Python tool server | 4 | ⬜ later |
| N7 (new) | Observability: OTel traces → Phoenix, `bazaar thread(s)` | 1 | ✅ (#34, #35) |
| N8 (new) | Runtime LLM: Jev-chosen model, `--llm-runtime`, ask, words, steer | 1 | 🔵 worker |
| N9 (new) | Guardrails rule book (GUARDRAILS.md) | 1 | ✅ (#30) |
| N11 (new) | Evals: online outcomes in Postgres + Phoenix annotations (Jev's design, `questions/evals.json`) | 1 → 2 | 🔵 inside the agents approved (#91, 09:30 window); Market Test stub until our venue runs |
| N12 (new) | **P1** · AI live-feed reader: dealer blockers (cooloff, quota, locks) and organiser notices into the RAG (`learnings`, `traders_behaviors`, embeddings) for the live taker and maker | 1 | 🔵 PR 1: deterministic reader (`bazaar_agent.learn`), `learnings` columns + `recall()`, the taker skips dealers under a blocker, the taker archives the feed window, `bazaar learnings`; PR 2 🔵: LLM pass over free text (background thread in the taker, Jev's `read_feed` model, never blocks), maker fee notices; embeddings, `trader_behaviors`, Jev/words context and the MCP tool moved to N3; PR 3 🔵: our dealer threads + `closed_reason` into `threads`/`messages` from the answers the taker already reads (0 extra requests) |
| N13 (new) | **P0 · Real-time holdings + card catalog in Postgres**: per-tick `/api/me` snapshot (album, cards, duplicates, missing, cash) refreshed after every deal; agents and bazaar-mcp read the DB | 1 | 🔵 approved (#105, 09:30 window) |
| N14 (new) | **P1 · RAG-driven strategies per mechanic** (on top of N3): hard dealers (learned concession curves, blockers, when to walk), packs (EV with supply + 3/hour), supply and scarcity (print runs, who holds what), custom markets (venue choice by fill odds and fees, our venue's fee, not feeding rivals' market-making), duels (rival profiles, delivery days), new pages and grants; each strategy reads lessons via the hybrid recall and writes its outcome back | 1 → 2 | ⬜ after N3 v1 (Sat 12:00) |
| N15 (new) | **Jev picks the desk's model per request**: orchestrator + each subagent (`desk_model` = auto, one batched `model_for_desk_role` Jev call, cache, per-role defaults, pin wins); spec [`N15-spec.md`](./N15-spec.md) | 1 | 🔵 approved (#108, 09:30 window) |
| N16 (new) | **P1 · Strategic bluffing + negotiation psychology in the words** (Omar: the agents may lie to win): deterministic tactic bank (bluffs + Voss/Cialdini tactics from the vetted MIT skill `wondelai/skills`), chosen per counterparty from learned outcomes (Jev learned_per_counterparty 0.90); a cooloff or bad-faith flag turns a tactic off; Abuela gets kindness; structure never changes; kill flag `BAZAAR_BLUFF=0`; spec [`N16-spec.md`](./N16-spec.md) | 1 → 2 | 🔵 PR #131 (both reviews APPROVE, round 2) |
| N17 (new) | **P1 · Team-to-team negotiation**: review Marius's #79/#98/#101 first (Jev 0.92), then swap threads with other teams (our duplicates for their duplicates of our missing cards, priced by their need, inside GUARDRAILS, kill flag `BAZAAR_TEAM_THREADS=0`) | 1 → 2 | 🔵 worker (triage + spec now; code after #72; PR before Duels II) |
| N18 (new) | Lean agent tracing in Phoenix (takes over Jhonny's ADR #46): `session.id` per negotiation, Jev as EVALUATOR spans, AGENT/TOOL spans per tick, LLM spans, evals as annotations, a pitch replay recipe; moves identical with tracing on/off (Jev 0.96) | 1 | 🔵 worker (afternoon window after Duels I) |
| N10 (new) | NICE TO HAVE · Bazaar Live: buyer + seller animated (Motion) and voiced (ElevenLabs / Gemini TTS, tagged), repo `bazaar-live` | 3 | 🔵 v1 deployed (bazaar-live #1 #2, https://bazaar-live-production.up.railway.app); v2 fantasy-RPG art + ES/EN voices and LIVE-T1 real transcripts from Postgres (bazaar-live #5) in progress; zero paid TTS until the pitch |
| [T1](T1-spec.md) · was #14, #23 | Strategy engine (scarcity, valuation, buy/sell, 3-pack quota) | 1 | #23 closed (done in #37: `bazaar strategy`); #14 open: `/api/me/value` check on 20 cards, `delta(give, want)`, per-counterparty cap |
| [M1](M1-spec.md) · was #11, #12 | Venue + limit-estimating broker | 1 → 2 | 🔵 #71 approved, shipped OFF (`allow_venue_open = false`, team decision Sat 06:08: the broker only equals the free stall); when on, the maker opens our 0 bps board venue at game hour 6.5 and brokers it; no reserve while off |
| [M1](M1-spec.md) · was #13 | Organic market making | 2 | 🔵 maker posts/reprices/cancels asks and bids on the best venue (LIVE since Sat 01:45 Madrid); our own venue ⬜ |
| [D1](D1-spec.md) · was #5, #7 | Duel policy, days module | 1 → 2 | 🔵 Marius's duel PRs merged as #150 (Sat 06:50): two-issue offers strictly inside the limit, v2 + B11 + days latch behind flags; `duel_policy` = v2 LIVE since #170 (Omar, Sat ~10:00; Jev had been undecided at 0.76) and B11 (min share 0.3, endgame 1) since #174; `duel_days_auto` OFF; sim harness #151 merged (Sat 10:42); pre-flip latch hardening #165 for the 23:00 window; duel-log surrogate fix #173 merged (Sat 11:08, emergency); calibration ⬜ |
| [P1](P1-spec.md) / [K1](K1-spec.md) · was #16, #17 | Pitch + scoring reference | 3 | ⬜ pitch Sunday (P0); K1 is the scoring reference |
| TO (new) | Take over Marius's night PRs (task_edf74300462e): bite fixes #140 #141 #142 #143 (stacked on #72) and #144; docs-only salvage of the closed analysis PRs #154 (`docs/night/README.md`); afternoon: #84 + #77, #78 + #128 | 2 | 🔵 #140–#144 approved (09:30 window); #154 in review; per-PR steps in #140's plan section |
| DS1 (new) | Dealer sell for ladder deals and cash: `bazaar dealer sell <REF> --min --start [--dealer]`, falling distinct asks, never at her opening bid, only free duplicates of page cards, guarded like `dealer buy`; taker plan behind `dealer_sell_enabled` later | 1 | 🔵 PR #179 |
| N19 (new) | Pilar readiness (L3 collector: gold pack, buys over book) in the simulator + a news sentinel (Radio Rastro `/api/news`, `news.posted`, `/api/schedule` fevers) that logs and stores each item; signals off (`news_signals_enabled = false`) | 2 | 🔵 PR #182 |
| [RO1](RO1-spec.md) (new) | Read-only Postgres login for teammates (DataGrip): `bazaar db readonly-user`, SELECT only, no secrets | 2 | 🔵 PR #184 |

### CLI commands (from `src/bazaar_agent/cli.py`)

| Command | What it does |
|---|---|
| `uv run bazaar clock` | Current tick, pace, doors, per-tick limits and the action budget left in this tick. |
| `uv run bazaar dealers` | Dealers in play: traits, menu, list prices, hourly quotas. |
| `uv run bazaar tape` | Every settlement (trade print): who bought what from whom, at what price. |
| `uv run bazaar curves` | Dealer concession curves rebuilt from every team's public threads; ours are tagged. |
| `uv run bazaar teams` | The competition: each team's flow (dealer bids, buys, sells, listings, inferred ×1.6 set). Us apart. |
| `uv run bazaar affinity` | Rival affinity map: P(each set holds each team's top multiplier), from the public feed alone. |
| `uv run bazaar trade-plan` | Dry-run trade plan for the next opening, fair by construction; sends nothing. |
| `uv run bazaar swaps` | Read-only: the swaps the taker's team desk would propose in team threads (N17), sends nothing. |
| `uv run bazaar team-checks` | Read-only: the N17 spec's Q1-Q6 answered from the shared DB (the feed, our refused sends, thread offers) |
| `uv run bazaar rivals` | Rival behaviour profiles: pricing against the tape and own value, fills, takes, reprices. |
| `uv run bazaar opportunities` | Read-only scanner: standing offers ranked by what accepting them gains us, guardrails checked. |
| `uv run bazaar book` | Live order book of a venue, with board pseudonyms resolved to team ids from the feed. Ours apart. |
| `uv run bazaar status` | Our cash, level, score, album pages with missing cards, and cards (GET /api/me, or its current snapshot). |
| `uv run bazaar threads` | Our negotiation threads (GET /api/me/threads): who, what, status and the last message. |
| `uv run bazaar thread` | One whole conversation (GET /api/threads/{id}): every message with sender, text and price. |
| `uv run bazaar dealer buy` | Buy one card or pack from a dealer: rising distinct bids, hard max, never at her opening ask. |
| `uv run bazaar dealer sell` | Sell one duplicate to a dealer (a ladder deal): falling distinct asks, hard floor, never at her opening bid. |
| `uv run bazaar duel run` | Every tick: log raw /api/duels to .local/duels; with --play, offer/accept inside our limit. |
| `uv run bazaar duel done` | Read our finished duels once (`/api/duels?done=true`, one request) and store them for the evals. |
| `uv run bazaar rules show` | Every guardrail from GUARDRAILS.md, its value, and the code that enforces it. |
| `uv run bazaar rules check` | Dry-run one action against the guardrails with our live /me, clock and ledger. |
| `uv run bazaar monitor` | The monitoring agent: live stream + per-tick feed poll → JSONL + Postgres, traders, /me snapshot, alerts. |
| `uv run bazaar traders` | Every trader we know (dealers and teams) from the monitor's Postgres table, with status and level. |
| `uv run bazaar alerts` | The latest alerts raised by the monitor: new dealers, level changes, announcements. |
| `uv run bazaar feed capture` | Append the public feed to .local/feed/feed.jsonl once per tick. Ctrl-C to stop. |
| `uv run bazaar feed stats` | How much feed history we hold, and the event mix. |
| `uv run bazaar obs up` | Start Arize Phoenix (docker compose): UI and OTLP/HTTP on 127.0.0.1:6006, OTLP/gRPC on :4317. |
| `uv run bazaar obs status` | Whether tracing is on, where spans go, the Phoenix UI, and whether Phoenix answers. |
| `uv run bazaar obs bootstrap` | Once per new Phoenix with auth: clear the admin's forced reset, mint a system API key for spans. |
| `uv run bazaar obs spans` | The newest spans in our Phoenix project, counted by name: proves the runtime's spans arrive. |
| `uv run bazaar db up` | Start Postgres + pgvector (docker compose, localhost:5433). |
| `uv run bazaar db check` | Reach DATABASE_URL: host (never the password), version, latency, ssl, pgvector, row counts. |
| `uv run bazaar db init` | Create every table (idempotent, safe while other processes are connected). |
| `uv run bazaar db load` | Load the captured feed into feed_events, tape and dealer_curves (idempotent). |
| `uv run bazaar db readonly-user` | Create or rotate the teammates' read-only login (SELECT only) with the admin DATABASE_URL. |
| `uv run bazaar db tables` | Every table with its row count. |
| `uv run bazaar strategy` | Ranked playbook from STRATEGY.md: buys, sells and packs, each with its command and guardrail verdict. |
| `uv run bazaar sell list` | List one card for cash (give the asset, want cash), never below its your_value (GUARDRAILS.md). |
| `uv run bazaar sell bid` | Bid cash for any copy of a card (give cash, want the card): how we buy rares only teams hold. |
| `uv run bazaar sell swap` | Propose a swap to one team: our copy (+ cash) for any copy of a card (+ cash), guardrails checked. |
| `uv run bazaar sell offers` | Our open and queued offers, and open offers addressed to us (GET /api/me/offers). |
| `uv run bazaar sell cancel` | Withdraw one of our open offers (refused while the kill switch is on: open offers stay open). |
| `uv run bazaar flatten` | Cancel every open offer of ours (--threads: also close our threads); works while the kill switch holds. |
| `uv run bazaar venue open` | Open our venue: 250 P bond + 20 P; saves the broker key (Postgres + 0600 file), never prints it. |
| `uv run bazaar venue close` | Close our venue; the bond comes back after a cooldown (a session counts the best venue open in it). |
| `uv run bazaar venue fee` | Announce new fees on our venue; they take effect after the public notice. |
| `uv run bazaar venue announce` | Post a notice on our venue with the broker key. |
| `uv run bazaar venue status` | Read only: the build-only switch, our venue on the public list, what the broker would match now. |
| `uv run bazaar broker run` | Every tick: read our venue's book and send the maximum-surplus matches (bench first). |
| `uv run bazaar llm` | Runtime LLM config (RUNTIME.md), pinned model, which credentials are set (never values), Jev's last choices. |
| `uv run bazaar ask` | Talk to the agent: sentence → desk (or strict intent) → guardrail verdict → exact command. Dry run by default. |
| `uv run bazaar steer` | Steer the style: instruction → bounded parameter deltas, clamped to GUARDRAILS.md, expiring at a tick. |

### Latest team memory (from `.ai/memory.md`, newest first)

- [2026-10-03] gotcha — a fresh `run_per_tick` handles the CURRENT tick at once
- [2026-10-03] finding — with #151, bazaar-sim duels score like the real game and share the team's one accept per tick
- [2026-10-03] gotcha — local simulators share ports across workers: use 8900+ and refuse a busy port
- [2026-10-03] finding — at 15 s ticks every agent finishes in under 4 s; the taker's pack gate asked Jev every tick
- [2026-10-03] gotcha — one exception in a bazaar-sim tick stopped its clock for good while /api/health said ok
- [2026-10-03] gotcha — rich wraps a counterparty's long text to column 0, whatever you indent the first line with
- [2026-10-03] finding — Radio Rastro's `news.posted` is in the public feed; Pilar is kind "collector" and sells only gold packs
- [2026-10-03] gotcha — a lone surrogate in another team's text stops a loop that writes it as UTF-8

<!-- BAZAAR:STATUS:END -->

## Activity

<!-- BAZAAR:ACTIVITY:START -->
<!-- Generated by CI on every push to main (.github/workflows/readme.yml). Do not edit by hand. -->

### Recently merged

| PR | Title | Merged | Commit |
|---|---|---|---|
| [#172](../../pull/172) | docs: the game screens live in bazaar-live now | Sat 10:24 | `7b0a0ce` |
| [#131](../../pull/131) | Omar's order (merge all approved). N16 bluffing in the words: both reviewers APPROVE round 2; narrow pr-reviewer APPROVE on 2d9f262 (issuecomment-5966874029); later rounds only merge main (import/docs unions; test fixture pins v1 like #170). Gate 3229 passed + smoke; CI green. Tactics: on for dealer words, off for duels under v2; kill switch BAZAAR_BLUFF / bluff_enabled. | Sat 10:20 | `7cb41ae` |
| [#171](../../pull/171) | Open our venue now: allow_venue_open = true from game hour 3.0, cash_floor 100 | Sat 10:19 | `d113167` |
| [#170](../../pull/170) | Omar's order (live session): duel_policy v2. pr-reviewer: all four safety checks verified (issuecomment-5967024103); its only P1 (merge conflict in sim_smoke.py/README with #123) resolved exactly as the reviewer tested (main's swaps step + the PR's duel-to-deadline block); gate 3150 passed, smoke duels deal inside limit (gains 26, 27); CI green. | Sat 10:12 | `90b0bdf` |
| [#169](../../pull/169) | Omar's rule: keep 270 for a custom market. pr-reviewer APPROVE (issuecomment-5966943243) on 755d7ee; 5070da4 fixes its two P2s (venue-opening procedure text, open-offers test at 380); CI green. | Sat 10:06 | `4549454` |
| [#123](../../pull/123) | Merged during the session on Omar's order. Lands the N17 stack (#137 + #138 + #123). pr-reviewer APPROVE on all three (issuecomment-5966897383, -5966898127, -5966898346) + security-auditor APPROVE (issuecomment-5966879686); 3eafaf7 only merges main (docs-only conflicts, PR code delta 0 lines); gate 3139 passed + smoke; CI green. Defaults OFF: team_threads_enabled=false, accept_bids=false. | Sat 10:00 | `6fc5bb9` |
| [#144](../../pull/144) | Merged during the session on Omar's order. pr-reviewer narrow APPROVE on a544fac (issuecomment-5966900707) after round-2 APPROVE on 40e956d; CI green. | Sat 09:51 | `5c673cb` |
| [#158](../../pull/158) | Merged during the session on Omar's order. pr-reviewer round 5 APPROVE on e71c337 (issuecomment-5966784280), security round 2 APPROVE; CI green. dealer_final_lift stays 0 (Jev decides the lift separately). | Sat 09:31 | `be431cd` |
| [#139](../../pull/139) | Merged during the session on Omar's order. pr-reviewer narrow APPROVE on 002ac37 (issuecomment-5966722003) after the approved 3af3641; CI test + sim-smoke green; tracing on/off identical moves. | Sat 09:22 | `fdeb199` |
| [#71](../../pull/71) | Merged during the session on Omar's order (09:07). pr-reviewer + security narrow APPROVE on e265626/1accc4e; 24b8583 only merges main (#146): code diff identical (0 lines), gate 2829 passed, smoke passed, CI green. allow_venue_open=false, effective cash floor 100. | Sat 09:14 | `04ce5d6` |
| [#154](../../pull/154) | Merged during the session on Omar's order (09:07). Approved on this exact head; CI green. | Sat 09:08 | `d64952e` |
| [#146](../../pull/146) | Merged during the session on Omar's order (09:07: merge everything approved ASAP). Approved on this exact head; CI green. | Sat 09:08 | `f9a193b` |

### Open pull requests

| PR | Title | Branch |
|---|---|---|
| [#168](../../pull/168) | docs: transcript of the 2026-10-03 morning voice memo (+ knowledge) | `docs/transcript-2026-10-03-morning` |
| [#167](../../pull/167) | docs(night): night-shift summary, index of every workstream, sanitised logs | `docs/night-summary` |
| [#166](../../pull/166) | chore: dealer_final_lift = 0.15 (Omar's call at 08:20, DO NOT MERGE without it; stacked on #158) | `ogarciarevett/n14a-lift-015` |
| [#165](../../pull/165) | fix(duels): D1 follow-up: days-latch pre-flip hardening and duel-loop resilience (after 23:00) | `ogarciarevett/d1-duel-followups` |
| [#164](../../pull/164) | feat(n17): bazaar team-checks — spec Q1-Q6 from stored data, read-only (N17-10) | `ogarciarevett/n17-live-checks` |
| [#163](../../pull/163) | B27: duel settings card + e2e runner + one done-read per tick (into #150) | `night/b27-card` |
| [#161](../../pull/161) | fix(dealer): close-retry and settle edge cases left open on #72 (P2/P3 follow-up) | `takeover/pr72-followup` |
| [#160](../../pull/160) | docs(pitch): Sunday presentation pack, first draft (P1) | `ogarciarevett/docs-pitch` |
| [#159](../../pull/159) | DO NOT MERGE: B27 duel stack integration (merge order #60→#86→#103→#113→#115→#130) + settings card | `night/b27-duel-stack` |
| [#157](../../pull/157) | perf(agents): every agent inside Sunday's 15 s tick: Jev answer cache, concurrent reads, tick profiler (SP1) | `ogarciarevett/work-speed-sp1` |
| [#155](../../pull/155) | feat(supply): supply map, pack EV with our album need, open or keep a sealed pack (N14b, part 2) | `ogarciarevett/feat-n14b-supply-packs` |
| [#152](../../pull/152) | feat(safety): bad-faith flags as proven decision rows (off) + injection hardening on every text path (S1 parts B+C) | `ogarciarevett/s1-flags` |
| [#151](../../pull/151) | feat(sim): duel rival zoo, exploiters and pairs in the simulator, takeover of Marius's #80 #97 #117 (D1) | `ogarciarevett/takeover-duel-sim` |
| [#143](../../pull/143) | fix(agents): an accept /api/me does not show yet counts as held, its cash as gone (take over #133, B16) | `takeover/b16-unsettled-accepts` |
| [#142](../../pull/142) | fix(maker): a bid that lapses unfilled gives its spend back, dated at the spend (take over #126, B14) | `takeover/b14-expired-bids` |
| [#141](../../pull/141) | fix(agents): a refused accept gives the team's accept back; no 429 re-sends, 4 s timeouts (take over #116, B18) | `takeover/b18-rate-limits` |
| [#140](../../pull/140) | fix(taker): adopt or close dealer threads orphaned by a restart, book their deals (take over #114, B17) | `takeover/b17-restart-orphans` |
| [#135](../../pull/135) | night(B29): pitch kit for Sunday: story, Q&A, demo, charts, decision log (fact-checked) | `night/b29-pitch-kit` |
| [#128](../../pull/128) | feat(ops): maker cancel cap, per-service tick offset, injection detector gaps (B10) | `night/b10-ops-hardening` |
| [#118](../../pull/118) | proposal(market): fastest safe path to an open venue (B20): open at 09:00, board+edge or auto; read-only bench watch | `night/b20-venue-path` |

<!-- BAZAAR:ACTIVITY:END -->
