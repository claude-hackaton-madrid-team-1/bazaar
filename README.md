# Bazaar · Team 1

Our entry to the Causa Prima hackathon (Madrid, 2–4 Oct 2026): an autonomous agent that trades
Madrid cards in [The Bazaar](https://bazaar.causaprima.ai). It reads the live game the way a
trading system reads market data, remembers every trader and price, learns from it, asks Jev for a
verdict, and leaves execution to a separate runtime that only acts on structured decisions.

**Python only** (3.12 + `uv`). The organisers' SDK is vendored in `vendor/bazaar-kit/` and used
first; raw HTTP against `docs/api/openapi.json` is Plan B only.

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
uv run bazaar status                      # our cash, level, score, cards (needs BAZAAR_KEY)
uv run bazaar threads                     # our negotiation threads; `bazaar thread <id>` for one
uv run bazaar obs up                      # Phoenix traces UI (then BAZAAR_TRACING=1, see Observability)
uv run bazaar agent taker                 # autonomous buyer, every tick: DRY RUN (logs WOULD-moves) until --live
uv run bazaar agent maker                 # autonomous market maker (asks, bids, reprices): DRY RUN until --live

uv run bazaar db up && uv run bazaar db init && uv run bazaar db load   # Postgres + pgvector memory
uv run bazaar db tables                   # every table with its row count
```

Tests: `uv run pytest` (the DB tests are skipped when Postgres is unreachable).
Lint: `uv run ruff check . && uv run ruff format --check .` · Types: `uv run mypy src`.

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
  `bazaar-monitor` on Railway is off by team decision (see "Production on Railway").
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
`touch .local/PAUSE` stops every write from every agent at once.

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
| `duel` (AGENT) | `bazaar duel run` | one per duel id: role, limit, a `duel tick N` child per tick with the rival offer, our move, guardrail, refusals |
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

[`RUNTIME.md`](RUNTIME.md) configures it. Jev picks the model per move (`questions/runtime_model.json`,
a probability per candidate; undecided → `runtime_model_default`), unless you pin one:
`--llm-runtime` > `BAZAAR_LLM_RUNTIME` > RUNTIME.md `llm_runtime`. Aliases: `opus-5-5`,
`sonnet-5-5`, `haiku-4-5`, `fable-5-1`, `gpt-6-1-sol` (any `claude-*` / `gpt-*` id passes through).
Keys: `ANTHROPIC_API_KEY`, `OPENAI_API_KEY` in `.env`; without one, every LLM path falls back.

```sh
uv run bazaar llm                                    # config, keys set (never values), Jev's last model choices
uv run bazaar ask "buy LAV-09 under 90"              # strict intent → guardrail verdict → the command (never runs it)
uv run bazaar --llm-runtime opus-5-5 ask "sell my spare SAL-03 for at least 8"
uv run bazaar steer "be more aggressive with rares tonight"   # bounded deltas, clamped by GUARDRAILS.md,
                                                     # applied by `bazaar strategy` and `duel run` until a tick
uv run bazaar steer --show                           # what is steered now, and until which tick
```

With `llm_words` = true, `dealer buy --live` and `duel run --play` let the chosen model write each
message; the price stays the structured field set by code, and any other number, a timeout or an
error sends the template instead.

## Autonomous agents (taker and maker)

Two tick-driven agents trade on their own, inside `GUARDRAILS.md`, with the strategy from `STRATEGY.md`.
Both are **dry runs by default**: they read everything, decide, log `WOULD …` and write every decision
to Postgres, but send nothing. Live needs `--live`, or `BAZAAR_LIVE=1` in the process environment (never
read from `.env`); on Railway that variable is set by hand, never in `.railway/railway.py`.

```sh
uv run bazaar agent taker            # dry run; --threads N dealer conversations (default 3), --no-jev
uv run bazaar agent maker            # dry run
uv run bazaar agent taker --port 8080   # also serve the read-only status (GET /health, /state, WS /events)
```

Every tick, both read `/api/me` once (album first), our open offers, the catalog, the dealers, the venues
and the feed (the shared `feed_events` table when Postgres answers, else `.local/feed`, plus the live
window), then rank with the strategy engine. A tick's deadline is `ticks.action_budget_s`; every send
checks it right before it goes, and a move that would be late is logged `DROPPED` and not sent.

| Agent | Does every tick | Never |
|---|---|---|
| **taker** (`agents/taker.py`, `agents/desk.py`) | (a) scans every board we may trade on (El Rastro + team venues) for standing ASKS of missing page cards whose **ask + the venue fee the accepting side pays** is below the card's value to us (book × affinity + page-bonus share) by at least `min_buy_surplus`, scarce cards first; (b) keeps up to N dealer conversations (one per dealer) for the strategy's top dealer buys and packs, one move per tick each with `dealer.decide()`, never blocking on one thread. Jev `offer_is_worth_accepting` is advisory: a decided `no` vetoes a board accept, a decided `yes` may accept a dealer's ask early, inside our max | accepts above a guardrail, opens a second thread with a dealer, buys a card twice in a tick |
| **maker** (`agents/maker.py`) | posts ASKS for the strategy's sell candidates (duplicates, low-affinity sets, priced at the buyer's need, never below `your_value × sell_min_value_ratio`) and BIDS for missing page cards only teams hold (below their value to us), on the venue with the best expected fill (trades so far, discounted by the fee share); reprices a target that moved ≥ 5 %, cancels one that is no longer a target (and refunds a cancelled bid's spend in the ledger) | accepts anything, lists on our own venue, posts more than `offers_per_team_per_tick` (12, counted team-wide in the ledger) or above `max_open_offers_per_team` (30), lets open bids + a new bid take cash below `cash_floor` |

The maker owns our **board** offers: a hand-listed offer that is not a strategy target is cancelled, so
stop the maker before trading by hand. Offers inside a dealer thread belong to the taker's desk.

**One accept per tick for the whole team, across machines.** The guardrail ledger is the Postgres
`ledger` table (`bazaar db init` creates it; the JSONL file only when Postgres is unreachable at
start). `bazaar-duels`, the taker, `dealer buy` and the CLI all reserve accepts through
`ledger.reserve_accept`: an advisory lock plus a unique `(tick, slot)` index, so two processes can never
take the same slot. **Duels first**: the duel player decides right after the tick lands; the taker
waits until 2 s into the tick (15 % on fast ticks) and steps back when a `duel:<id>` accept is already
recorded for the tick. The maker never accepts. Spend per game hour and packs per hour come from the
same table, so `max_spend_per_game_hour` holds for the team, not per process.

**What they write.** Every proposed move is a `decisions` row (`agent`, `kind`, inputs, the strategy's
reason, Jev's verdict with its floats, the guardrail verdict, chosen or not, `dry_run`, status
`approved`/`rejected`/`expired`/`done`/`failed`); every live send is an `executions` row with the
server's answer or refusal code. Without Postgres they go to `.local/agents/*.jsonl`. With
`BAZAAR_TRACING=1`, each tick is a `taker tick N` / `maker tick N` trace with one `decision` event per move.

**Read-only status (for the web view).** With `--port` (or Railway's `PORT`), each agent serves
`GET /health` (`ok`, `agent`, `mode` dry|live, `tick`, `last_tick_at`, and the doors/paused state while
the game is not ticking), `GET /state` (mode, tick, the taker's dealer threads or the maker's open
offers, the last 50 decisions with move, reason, strategy, Jev, guardrail and sent/would-send), and
`WS /events`: every decision and execution as it happens in the web view's envelope (spec 003:
`{id, tick, t, type, scope, actor, payload}`, negative made-up ids, plus `agent`), types
`agent.decision`, `agent.execution`, `agent.tick`; a late client first gets the last 200 events. Nothing
there can trade or change a parameter, every string passes the telemetry scrubber, and CORS is open
(public read-only data). It runs on its own thread: publishing from the tick loop is an append and a
scheduled broadcast, so a slow client never delays a tick.

## Services and public URLs (start here for observability and the dashboard)

Railway project **`heartfelt-warmth`** (environment `production`, region `europe-west4`):
https://railway.com/project/05a9de65-622b-4754-a0f0-be4d7f54ec51?environmentId=912b1525-f223-4f0d-bfc6-8db725f7a6e0

| Service | Public URL | Private (inside Railway) | Role | State |
|---|---|---|---|---|
| `phoenix` | https://phoenix-production-6aa3.up.railway.app (login `admin@localhost`, password in its Railway variables) | `phoenix.railway.internal:6006` (OTLP/HTTP), `:4317` (gRPC) | traces UI for every negotiation, duel, monitor tick and CLI line | running |
| `Postgres` | `iriguchi.proxy.rlwy.net:28880`, db `railway`, user `postgres`, SSL (password: Postgres service → Variables) | `${{Postgres.DATABASE_URL}}` | the team's shared memory (feed, tape, dealer curves, traders, snapshots, alerts, decisions) | running |
| `bazaar-duels` | none (worker, no HTTP) | — | the team's ONE duel player (`duel run --play`) | running |
| `bazaar-monitor` | none (worker, no HTTP) | — | kept but OFF (no source, no deployment): the monitor runs in the CLI on a laptop (`uv run bazaar monitor --notify`) by team decision | off |
| `bazaar-taker` | https://bazaar-taker-production.up.railway.app (`/health`, `/state`) · wss://bazaar-taker-production.up.railway.app/events | `bazaar-taker.railway.internal:8080` | autonomous buyer (`bazaar agent taker`): board asks + dealer desk; read-only status | dry run (no `BAZAAR_LIVE`) |
| `bazaar-maker` | https://bazaar-maker-production.up.railway.app (`/health`, `/state`) · wss://bazaar-maker-production.up.railway.app/events | `bazaar-maker.railway.internal:8080` | autonomous market maker (`bazaar agent maker`): asks, bids, reprices; read-only status | dry run (no `BAZAAR_LIVE`) |
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
| `bazaar-monitor` | `bazaar monitor` (feed → JSONL + Postgres, traders, `/me`, alerts) | volume `bazaar-monitor-data` on `/app/.local` | OFF by team decision (no source, no deployment): the monitor runs in the CLI on a laptop |
| `bazaar-duels` | `bazaar duel run --play` (offers/accepts inside `GUARDRAILS.md`) | volume `bazaar-duels-data` on `/app/.local` | the team's ONE duel player; first claim on the team's accept each tick |
| `bazaar-taker` | `bazaar agent taker` + status on `PORT` 8080 (healthcheck `/health`) | volume `bazaar-taker-data` on `/app/.local` | dry run unless `BAZAAR_LIVE=1` is set by hand |
| `bazaar-maker` | `bazaar agent maker` + status on `PORT` 8080 (healthcheck `/health`) | volume `bazaar-maker-data` on `/app/.local` | dry run unless `BAZAAR_LIVE=1` is set by hand; never accepts |
| `phoenix` | `arizephoenix/phoenix:version-20.19.0` (same pin as `docker-compose.yml`), auth on | volume `phoenix-data` on `/mnt/data` | UI: https://phoenix-production-6aa3.up.railway.app |
| `Postgres` | `postgres-ssl:18` + pgvector | its own volume | managed in the dashboard, NOT by `.railway/railway.py` |

- **Builds.** `bazaar-duels` builds this repo's `main` with Railpack (Python 3.12 through
  `RAILPACK_PYTHON_VERSION`, `uv sync --locked --no-dev`, editable so `vendor/` and
  `GUARDRAILS.md` resolve from `/app`). Every push to `main` that touches `src/`, `vendor/bazaar-kit/`,
  `pyproject.toml`, `uv.lock`, `GUARDRAILS.md`, `STRATEGY.md`, `questions/` or `.railway/`
  redeploys it; README-only commits are skipped. Restart policy: always.
- **The monitor is off, not deleted.** Railway has no 0-replica setting (the API rejects
  `numReplicas` 0, and dropping the region moves the service to a default region), so "off" is no
  source and no deployment: `.railway/railway.py` declares `bazaar-monitor` with `enabled=False`.
  To turn it back on: set `enabled=True`, `railway config plan` (shows `source.repo` reconnecting),
  `apply`, then `railway redeploy --service bazaar-monitor --from-source --yes` if no build starts,
  and stop the laptop monitor: one monitor per team.
- **Variables.** `DATABASE_URL = ${{Postgres.DATABASE_URL}}` (private network),
  `PHOENIX_COLLECTOR_ENDPOINT = http://${{phoenix.RAILWAY_PRIVATE_DOMAIN}}:6006`,
  `PHOENIX_API_KEY = ${{phoenix.PHOENIX_API_KEY}}`, `BAZAAR_TRACING=1`, `BAZAAR_DATA_DIR=/app/.local`.
  Secrets (`BAZAAR_KEY`, `TYPESAFE_API_KEY`, `PHOENIX_SECRET`, `PHOENIX_DEFAULT_ADMIN_INITIAL_PASSWORD`,
  `PHOENIX_API_KEY`) are only in Railway; the file says `preserve()`. Set or rotate one without
  it touching a command line: `printf %s "$VALUE" | railway variable set NAME --stdin --service <svc>`.
- **Pause every write** (the guardrail kill switch): `railway ssh --service bazaar-duels -- touch /app/.local/PAUSE`
  (on the volume, so it survives redeploys); `rm` it to resume.

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
  `dealer buy` see one count (see "Autonomous agents"). A process that cannot reach Postgres at start
  falls back to its own `ledger.jsonl` and says so in its log; a ledger failure mid-run sends nothing
  that tick (fail closed).
- **Turn an agent live** (a team decision, not a deploy):
  `printf 1 | railway variable set BAZAAR_LIVE --stdin --service bazaar-taker` (it redeploys); delete
  the variable to go back to dry run. `/health` says `mode: live|dry`; check it after any
  `railway config apply` too, since the file does not declare `BAZAAR_LIVE`. The kill switch still applies:
  `railway ssh --service bazaar-taker -- touch /app/.local/PAUSE`.
- **Known limit: other state in `BAZAAR_DATA_DIR` is per container.** `steering.json`: a laptop
  `bazaar steer` does not reach Railway's duel player; steer it in its container
  (`railway ssh --service bazaar-duels -- /app/.venv/bin/bazaar steer "..."`). The runtime LLM keys
  (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`) are not set on Railway yet, so every LLM path there falls
  back to its template or default (RUNTIME.md `llm_words` is off anyway).

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
- **Backlog:** GitHub issues are the source of truth; the plan mirrors them.
- **Never** push from an agent, never commit `.env`, one team key only.

## Live status

<!-- BAZAAR:STATUS:START -->
<!-- Generated by scripts/readme_status.py on every commit. Do not edit by hand. -->

### Backlog (from `.ai/specs/02-plan.md`)

| Task id | Title | Phase | Status |
|---|---|---|---|
| [#21](https://github.com/claude-hackaton-madrid-team-1/bazaar/issues/21) | Feed capture + dealer curves | 0 → 1 | ✅ `bazaar monitor` (#32); real-time SSE + ours/theirs tagging 🔵 worker |
| [#2](https://github.com/claude-hackaton-madrid-team-1/bazaar/issues/2) | Team key + API client + fixtures | 0 | ✅ key works; SDK bridge; API fixtures (#26) |
| [#3](https://github.com/claude-hackaton-madrid-team-1/bazaar/issues/3) | Tick loop, governor, scheduler, kill switch | 0 → 1 | 🔵 tick loop + budget + `.local/PAUSE` done; cancel-open-offers kill switch ⬜ |
| [#8](https://github.com/claude-hackaton-madrid-team-1/bazaar/issues/8) | Abuela negotiator (concession curve) | 0 | ✅ 4 negotiated deals (7/9/9/22) |
| [#9](https://github.com/claude-hackaton-madrid-team-1/bazaar/issues/9) | Ladder maximizer + reach L2 | 0 → 2 | 🔵 level 2 reached (El Chato unlocked); first Chato deal walked (he held 33 vs our max 24) |
| [#4](https://github.com/claude-hackaton-madrid-team-1/bazaar/issues/4) | Duel logger (practice h2) | 0 | 🔵 `bazaar duel run --play` running, waiting for practice duels |
| N1 (new) | Memory schema + repository + Railway-ready DB | 1 | ✅ (#29, #32, #33) |
| N2 (new) | Intel: order book, tape, competitor profiles | 1 | ✅ (#29, #32) |
| N3 (new) | Learner + embeddings + RAG context | 1 | ⬜ not started (after strategy + LLM) |
| [#1](https://github.com/claude-hackaton-madrid-team-1/bazaar/issues/1) | Decision model: decider + Jev packs + policy | 1 | 🔵 autonomous taker + maker (`bazaar agent`), every move in `decisions`, dry run on Railway; live switch-on ⬜ |
| [#10](https://github.com/claude-hackaton-madrid-team-1/bazaar/issues/10) / [#24](https://github.com/claude-hackaton-madrid-team-1/bazaar/issues/24) | Executor firewall, offer inspector, flags | 1 → 2 | 🔵 guardrails + offer-term check done (#30, #31); executor ⬜ |
| N4 (new) | `service.py` + CLI + bazaar skill + commands | 1 | 🔵 CLI + skill done; `service.py` seam ⬜ |
| N5 (new) | Jev port to Python (judge, mask, log, report, parity) | 0 → 1 | ✅ (#29, #31); recorded-fixture parity test ⬜ |
| N6 (new) | Voice interface: ElevenLabs agent + Python tool server | 4 | ⬜ later |
| N7 (new) | Observability: OTel traces → Phoenix, `bazaar thread(s)` | 1 | ✅ (#34, #35) |
| N8 (new) | Runtime LLM: Jev-chosen model, `--llm-runtime`, ask, words, steer | 1 | 🔵 worker |
| N9 (new) | Guardrails rule book (GUARDRAILS.md) | 1 | ✅ (#30) |
| [#14](https://github.com/claude-hackaton-madrid-team-1/bazaar/issues/14) / [#23](https://github.com/claude-hackaton-madrid-team-1/bazaar/issues/23) | Strategy engine (scarcity, valuation, buy/sell, 3-pack quota) | 1 | 🔵 worker |
| [#11](https://github.com/claude-hackaton-madrid-team-1/bazaar/issues/11) / [#12](https://github.com/claude-hackaton-madrid-team-1/bazaar/issues/12) | Venue + limit-estimating broker | 1 → 2 | ⬜ not started (Market Test, Saturday) |
| [#13](https://github.com/claude-hackaton-madrid-team-1/bazaar/issues/13) | Organic market making | 2 | 🔵 maker posts/reprices/cancels asks and bids on the best venue (dry run); our own venue ⬜ |
| [#5](https://github.com/claude-hackaton-madrid-team-1/bazaar/issues/5) / [#7](https://github.com/claude-hackaton-madrid-team-1/bazaar/issues/7) | Duel policy, days module | 1 → 2 | 🔵 safe player + days worst case (#31); calibration ⬜ |
| [#15](https://github.com/claude-hackaton-madrid-team-1/bazaar/issues/15) | Score simulator + dashboard | 2 (nice-to-have) | ⬜ |
| [#16](https://github.com/claude-hackaton-madrid-team-1/bazaar/issues/16) / [#17](https://github.com/claude-hackaton-madrid-team-1/bazaar/issues/17) | Pitch + scoring tracker | 3 | ⬜ |

### CLI commands (from `src/bazaar_agent/cli.py`)

| Command | What it does |
|---|---|
| `uv run bazaar clock` | Current tick, pace, doors, per-tick limits and the action budget left in this tick. |
| `uv run bazaar dealers` | Dealers in play: traits, menu, list prices, hourly quotas. |
| `uv run bazaar tape` | Every settlement (trade print): who bought what from whom, at what price. |
| `uv run bazaar curves` | Dealer concession curves rebuilt from every team's public threads; ours are tagged. |
| `uv run bazaar teams` | The competition: each team's flow (dealer bids, buys, sells, listings, inferred ×1.6 set). Us apart. |
| `uv run bazaar book` | Live order book of a venue, with board pseudonyms resolved to team ids from the feed. Ours apart. |
| `uv run bazaar status` | Our cash, level, score, album pages with missing cards, and cards (GET /api/me). |
| `uv run bazaar threads` | Our negotiation threads (GET /api/me/threads): who, what, status and the last message. |
| `uv run bazaar thread` | One whole conversation (GET /api/threads/{id}): every message with sender, text and price. |
| `uv run bazaar dealer buy` | Buy one card or pack from a dealer: rising distinct bids, accept at our next bid, hard max. |
| `uv run bazaar duel run` | Every tick: log raw /api/duels to .local/duels; with --play, offer/accept inside our limit. |
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
| `uv run bazaar db tables` | Every table with its row count. |
| `uv run bazaar strategy` | Ranked playbook from STRATEGY.md: buys, sells and packs, each with its command and guardrail verdict. |
| `uv run bazaar sell list` | List one card for cash (give the asset, want cash), never below its your_value (GUARDRAILS.md). |
| `uv run bazaar sell bid` | Bid cash for any copy of a card (give cash, want the card): how we buy rares only teams hold. |
| `uv run bazaar sell offers` | Our open and queued offers, and open offers addressed to us (GET /api/me/offers). |
| `uv run bazaar sell cancel` | Withdraw one of our open offers. |
| `uv run bazaar llm` | Runtime LLM config (RUNTIME.md), pinned model, which keys are set (never values), and Jev's last choices. |
| `uv run bazaar ask` | Talk to the agent: sentence → strict intent → guardrail verdict → exact command. Dry run: never trades. |
| `uv run bazaar steer` | Steer the style: instruction → bounded parameter deltas, clamped to GUARDRAILS.md, expiring at a tick. |

### Latest team memory (from `.ai/memory.md`, newest first)

- [2026-10-02] build-error — a ledger note on stdout broke `bazaar strategy --json`
- [2026-10-02] gotcha — after 23:00 the doors close and every tick loop just waits
- [2026-10-02] finding — first autonomous dry runs (tick 155): the taker would buy MAL-02 for 5, the maker would list 3 asks
- [2026-10-02] build-error — one DNS failure killed the laptop monitor (Friday close, commuting)
- [2026-10-02] gotcha — Railway has no 0 replicas; `railway config apply` can fail with exit 0
- [2026-10-02] gotcha — `railway variable set` has no shared-variable flag; use `--stdin` for secrets
- [2026-10-02] gotcha — Phoenix forces an admin password reset even with an initial password set
- [2026-10-02] gotcha — Railpack's uv install is `--no-editable`, which breaks REPO_ROOT

<!-- BAZAAR:STATUS:END -->

## Activity

<!-- BAZAAR:ACTIVITY:START -->
<!-- Generated by CI on every push to main (.github/workflows/readme.yml). Do not edit by hand. -->

### Recently merged

| PR | Title | Merged | Commit |
|---|---|---|---|
| [#48](../../pull/48) | feat: autonomous taker and maker agents (dry run), shared Postgres ledger, read-only status | Fri 23:57 | `aca6fe1` |
| [#47](../../pull/47) | fix: unattended tick loops survive network errors instead of exiting | Fri 23:45 | `f093e64` |
| [#45](../../pull/45) | chore: turn the Railway monitor off; the monitor runs in the CLI on a laptop | Fri 23:00 | `f46872e` |
| [#44](../../pull/44) | docs: map every Railway service and public URL | Fri 22:44 | `d50bd8d` |
| [#42](../../pull/42) | feat: always-on runtime on Railway (monitor, duels, Phoenix with auth) as code | Fri 22:42 | `2e5d7a5` |
| [#41](../../pull/41) | fix: read the live duel payload so the duel player actually plays | Fri 22:40 | `cdf0d11` |
| [#40](../../pull/40) | feat: real-time monitor over the live stream, with our team told apart | Fri 22:34 | `a7c09df` |
| [#39](../../pull/39) | feat: runtime LLM layer (Jev picks the model, ask, words, steer) | Fri 22:31 | `7813244` |
| [#38](../../pull/38) | fix: greet the dealer we are actually talking to | Fri 22:16 | `5894941` |
| [#37](../../pull/37) | feat: strategy engine, sell/bid offers, pack quota and Jev pack gate | Fri 22:15 | `83007fb` |
| [#36](../../pull/36) | ci: keep the root README current after every merge to main | Fri 22:08 | `a23f074` |
| [#35](../../pull/35) | fix: a failing tracing hook can never break a live negotiation | Fri 22:04 | `3bf4527` |

### Open pull requests

| PR | Title | Branch |
|---|---|---|
| [#46](../../pull/46) | docs(adr): trace agent behavior in Phoenix — turns, typed spans, sessions | `docs/adr-agent-tracing` |
| [#43](../../pull/43) | feat: web dashboard on the real live feed, terminal UI removed | `feat/web-live` |

<!-- BAZAAR:ACTIVITY:END -->
