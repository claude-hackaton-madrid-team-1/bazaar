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
`tick loop:`). It holds no secrets: it drops the secret variables, never reads the repo `.env`
(`BAZAAR_ENV_FILE` points at an empty file), and sends every non-local request to a dead proxy.

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
   answers there, a `bazaar-sim serve` or the MCP server: stop it first.)
4. **Reset the public simulator** to tick 0 when a test needs a fresh world (everyone shares it):
   `SIM_ADMIN_TOKEN=<bazaar-sim → Variables in Railway> uv run bazaar-sim reset --url https://bazaar-sim-production-1d48.up.railway.app`.

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
  `SIM_ADMIN_TOKEN=<token> uv run bazaar-sim reset --url https://bazaar-sim-production-1d48.up.railway.app`
  (add `--seed N` for another world).
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
uv run bazaar status                      # our cash, level, score, cards (needs BAZAAR_KEY)
uv run bazaar threads                     # our negotiation threads; `bazaar thread <id>` for one
uv run bazaar obs up                      # Phoenix traces UI (then BAZAAR_TRACING=1, see Observability)
uv run bazaar agent taker                 # autonomous buyer, every tick: DRY RUN (logs WOULD-moves) until --live
uv run bazaar agent maker                 # autonomous market maker (asks, bids, reprices): DRY RUN until --live

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

[`RUNTIME.md`](RUNTIME.md) configures it. Jev picks the model per move (`questions/runtime_model.json`,
a probability per candidate; undecided → `runtime_model_default`), unless you pin one:
`--llm-runtime` > `BAZAAR_LLM_RUNTIME` > RUNTIME.md `llm_runtime`. Aliases: `opus-5-5`,
`sonnet-5-5`, `haiku-4-5`, `fable-5-1`, `gpt-6-1-sol` (any `claude-*` / `gpt-*` id passes through).
Credentials: `ANTHROPIC_API_KEY` or `CLAUDE_CODE_OAUTH_TOKEN` (Claude, see below), `OPENAI_API_KEY`
in `.env`; without one, every LLM path falls back.

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
read from `.env`); on Railway that variable is set by hand, never in `.railway/railway.py`.

```sh
uv run bazaar agent taker            # dry run; --threads N dealer conversations (default 3), --no-jev
uv run bazaar agent maker            # dry run; --no-jev keeps the strategy's prices
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
offers, the last 50 decisions with kind, card, counterparty, the move sent, Jev verdict, guardrail label and
sent/would-send), and
`WS /events`: every decision and execution as it happens in the web view's envelope (spec 003:
`{id, tick, t, type, scope, actor, payload}`, negative made-up ids, plus `agent`), types
`agent.decision`, `agent.execution`, `agent.tick`; a late client first gets the last 200 events. Nothing
there can trade or change a parameter, every string passes the telemetry scrubber, and CORS is open
(public read-only data), so only an allow-listed public view is published: never our card values, max
prices, bid ladders, surplus, cash, limits or reasons (`docs/services.md`, "Public by design"). It runs
on its own thread: publishing from the tick loop is an append and a
scheduled broadcast, so a slow client never delays a tick.

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
`src/bazaar_agent/runtime/` (`tools.py`, `hooks.py`, `agents.py`, `desk.py`, `mcp_server.py`).

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
- **Never in the hot path.** The taker, maker, duel and monitor loops stay deterministic; the desk
  advises, proposes, parses and steers. RUNTIME.md `desk_model` (Sonnet 5.5), `desk_max_turns`,
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

**Always on.** Railway service `bazaar-evals` runs `bazaar evals run --every-ticks 6`. Like every
loop here it follows the game clock (tick discipline), read from the keyless public `/api/clock`, so
it never touches the team key: doors closed, no pass. Every 6 ticks it scores again when an input moved
in Postgres (a game tick, a duel from `duel done` or `import-duels`, a `/me` snapshot, a decision) or an
outcome still waits for its Phoenix span (each is looked up on three passes: a trace lands when its duel
or negotiation ends). A Postgres outage is retried at the next due tick. After a restart, `bazaar duel
run` reads `?done=true` once, so a duel that finished while it was down is stored.

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
| `bazaar-mcp` | https://bazaar-mcp-production.up.railway.app/mcp (bearer token; `/health` public) | `bazaar-mcp.railway.internal:8080` | the runtime tools as a remote MCP server (`bazaar mcp serve`) for teammates' Claude Code | running, dry run (no `BAZAAR_LIVE`) |
| `bazaar-evals` | none (worker, no HTTP) | — | scores settled duels, dealer deals and trades (`evals run --every-ticks 6`) into Postgres `outcomes` and Phoenix annotations | running |
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
| `bazaar-monitor` | `bazaar monitor` (feed → JSONL + Postgres, traders, `/me`, alerts) | volume `bazaar-monitor-data` on `/app/.local` | OFF by team decision (no source, no deployment): the monitor runs in the CLI on a laptop |
| `bazaar-duels` | `bazaar duel run --play` (offers/accepts inside `GUARDRAILS.md`) | volume `bazaar-duels-data` on `/app/.local` | the team's ONE duel player; first claim on the team's accept each tick |
| `bazaar-taker` | `bazaar agent taker` + status on `PORT` 8080 (healthcheck `/health`) | volume `bazaar-taker-data` on `/app/.local` | dry run unless `BAZAAR_LIVE=1` is set by hand |
| `bazaar-maker` | `bazaar agent maker` + status on `PORT` 8080 (healthcheck `/health`) | volume `bazaar-maker-data` on `/app/.local` | dry run unless `BAZAAR_LIVE=1` is set by hand; never accepts |
| `bazaar-mcp` | `bazaar mcp serve --host 0.0.0.0` on `PORT` 8080 (healthcheck `/health`) | volume `bazaar-mcp-data` on `/app/.local` | bearer `BAZAAR_MCP_TOKEN` (`preserve()`), dry run unless `BAZAAR_LIVE=1` is set by hand |
| `bazaar-evals` | `bazaar evals run --every-ticks 6` (README "Evals") | none: Postgres in, Postgres and Phoenix annotations out | no `BAZAAR_KEY`: only the keyless `/api/clock` paces it |
| `bazaar-sim` | `bazaar-sim serve` on `PORT` 8080 (healthcheck `/api/health`), one tick every 10 s | database `bazaar_sim` (schema `sim`) on the team's Postgres | https://bazaar-sim-production-1d48.up.railway.app; the generated domain is not IaC (Railway does not declare generated domains) |
| `phoenix` | `arizephoenix/phoenix:version-20.19.0` (same pin as `docker-compose.yml`), auth on | volume `phoenix-data` on `/mnt/data` | UI: https://phoenix-production-6aa3.up.railway.app |
| `Postgres` | `postgres-ssl:18` + pgvector | its own volume | managed in the dashboard, NOT by `.railway/railway.py` |

- **Builds.** `bazaar-duels` builds this repo's `main` with Railpack (Python 3.12 through
  `RAILPACK_PYTHON_VERSION`, `uv sync --locked --no-dev`, editable so `vendor/` and
  `GUARDRAILS.md` resolve from `/app`). Every push to `main` that touches `src/`, `vendor/bazaar-kit/`,
  `pyproject.toml`, `uv.lock`, `GUARDRAILS.md`, `STRATEGY.md`, `RUNTIME.md`, `questions/` or `.railway/`
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
  Secrets (`BAZAAR_KEY`, `TYPESAFE_API_KEY`, `CLAUDE_CODE_OAUTH_TOKEN`, `SIM_ADMIN_TOKEN`, `PHOENIX_SECRET`,
  `PHOENIX_DEFAULT_ADMIN_INITIAL_PASSWORD`, `PHOENIX_API_KEY`) are only in Railway; the file says `preserve()`. Set or rotate one without
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
  plan's task index, in the same hook and CI job. The **roadmap** is `roadmap` in that JSON: one entry per time slot with
  `when`, `title`, optional `events` (the organisers' schedule) and `items` of `{priority: P0-P3, status: done|wip|partial|todo,
  text, owner?}` (the page shows `wip` as "doing"). Git hooks and CI cannot publish claude.ai artifacts, so
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
| [#21](https://github.com/claude-hackaton-madrid-team-1/bazaar/issues/21) | Feed capture + dealer curves | 0 → 1 | 🔵 `bazaar monitor` (#32), real-time stream (#40), thread-fill fix (#58); open: Abuela `open`/`limit`/β estimate, ladder view (PR #43) |
| [#2](https://github.com/claude-hackaton-madrid-team-1/bazaar/issues/2) | Team key + API client + fixtures | 0 | ✅ key works; SDK bridge; API fixtures (#26) |
| [#3](https://github.com/claude-hackaton-madrid-team-1/bazaar/issues/3) | Tick loop, governor, scheduler, kill switch | 0 → 1 | 🔵 tick loop + budget + `.local/PAUSE` done; cancel-open-offers kill switch ⬜ |
| [#8](https://github.com/claude-hackaton-madrid-team-1/bazaar/issues/8) | Abuela negotiator (concession curve) | 0 | ✅ 4 negotiated deals (7/9/9/22) |
| [#9](https://github.com/claude-hackaton-madrid-team-1/bazaar/issues/9) | Ladder maximizer + reach L2 | 0 → 2 | 🔵 level 2 reached (El Chato unlocked); first Chato deal walked (he held 33 vs our max 24); best-3 ladder table is `bazaar evals report` (#58); `egg.found` alert and the L2 rule write-up ⬜ |
| [#4](https://github.com/claude-hackaton-madrid-team-1/bazaar/issues/4) | Duel logger (practice h2) | 0 | 🔵 duels logged and stored (#41, #58); open: committed C1–C6 answers, full-session fixtures in `tests/fixtures/duels/`, live deadline proof |
| N1 (new) | Memory schema + repository + Railway-ready DB | 1 | ✅ (#29, #32, #33) |
| N2 (new) | Intel: order book, tape, competitor profiles | 1 | ✅ (#29, #32) |
| N3 (new) | Learner + embeddings + RAG context | 1 | ⬜ not started (after strategy + LLM) |
| [#1](https://github.com/claude-hackaton-madrid-team-1/bazaar/issues/1) | Decision model: decider + Jev packs + policy | 1 | 🔵 autonomous taker + maker (`bazaar agent`), every move in `decisions`, dry run on Railway; live switch-on ⬜ |
| [#10](https://github.com/claude-hackaton-madrid-team-1/bazaar/issues/10) / [#24](https://github.com/claude-hackaton-madrid-team-1/bazaar/issues/24) | Executor firewall, offer inspector, flags | 1 → 2 | 🔵 guardrails + offer-term check done (#30, #31); `untrusted_text` wrapping (#59); open: executor, flags, hostile-text tests, public `/state` follow-up, duel limit (PR #60) |
| N4 (new) | `service.py` + CLI + bazaar skill + commands | 1 | 🔵 CLI + skill done; `service.py` seam ⬜ |
| N5 (new) | Jev port to Python (judge, mask, log, report, parity) | 0 → 1 | ✅ (#29, #31); recorded-fixture parity test ⬜ |
| N6 (new) | Voice interface: ElevenLabs agent + Python tool server | 4 | ⬜ later |
| N7 (new) | Observability: OTel traces → Phoenix, `bazaar thread(s)` | 1 | ✅ (#34, #35) |
| N8 (new) | Runtime LLM: Jev-chosen model, `--llm-runtime`, ask, words, steer | 1 | 🔵 worker |
| N9 (new) | Guardrails rule book (GUARDRAILS.md) | 1 | ✅ (#30) |
| N11 (new) | Evals: online outcomes in Postgres + Phoenix annotations (Jev's design, `questions/evals.json`) | 1 → 2 | 🔵 duels, dealer ladder, team trades scored; `bazaar-evals` service; Market Test stub until we run a venue |
| N12 (new) | **P1** · AI live-feed reader: dealer blockers (cooloff, quota, locks) and organiser notices into the RAG (`learnings`, `traders_behaviors`, embeddings) for the live taker and maker | 1 | 🔵 worker (first version before Duels I) |
| N10 (new) | NICE TO HAVE · Bazaar Live: buyer + seller animated (Motion) and voiced (ElevenLabs / Gemini TTS, tagged), repo `bazaar-live` | 3 | ⬜ planned (98-nice-to-haves.md) |
| [#14](https://github.com/claude-hackaton-madrid-team-1/bazaar/issues/14) / [#23](https://github.com/claude-hackaton-madrid-team-1/bazaar/issues/23) | Strategy engine (scarcity, valuation, buy/sell, 3-pack quota) | 1 | #23 closed (done in #37: `bazaar strategy`); #14 open: `/api/me/value` check on 20 cards, `delta(give, want)`, per-counterparty cap |
| [#11](https://github.com/claude-hackaton-madrid-team-1/bazaar/issues/11) / [#12](https://github.com/claude-hackaton-madrid-team-1/bazaar/issues/12) | Venue + limit-estimating broker | 1 → 2 | ⬜ not started (Market Test, Saturday) |
| [#13](https://github.com/claude-hackaton-madrid-team-1/bazaar/issues/13) | Organic market making | 2 | 🔵 maker posts/reprices/cancels asks and bids on the best venue (dry run); our own venue ⬜ |
| [#5](https://github.com/claude-hackaton-madrid-team-1/bazaar/issues/5) / [#7](https://github.com/claude-hackaton-madrid-team-1/bazaar/issues/7) | Duel policy, days module | 1 → 2 | 🔵 safe player + days worst case (#31); calibration ⬜ |
| [#15](https://github.com/claude-hackaton-madrid-team-1/bazaar/issues/15) | Score simulator + dashboard | 2 (nice-to-have) | 🔵 outcome evals (#58) partly cover it; top-3 normalisation ⬜, dashboard in PR #43 |
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
| `uv run bazaar db tables` | Every table with its row count. |
| `uv run bazaar strategy` | Ranked playbook from STRATEGY.md: buys, sells and packs, each with its command and guardrail verdict. |
| `uv run bazaar sell list` | List one card for cash (give the asset, want cash), never below its your_value (GUARDRAILS.md). |
| `uv run bazaar sell bid` | Bid cash for any copy of a card (give cash, want the card): how we buy rares only teams hold. |
| `uv run bazaar sell offers` | Our open and queued offers, and open offers addressed to us (GET /api/me/offers). |
| `uv run bazaar sell cancel` | Withdraw one of our open offers. |
| `uv run bazaar llm` | Runtime LLM config (RUNTIME.md), pinned model, which credentials are set (never values), Jev's last choices. |
| `uv run bazaar ask` | Talk to the agent: sentence → desk (or strict intent) → guardrail verdict → exact command. Dry run by default. |
| `uv run bazaar steer` | Steer the style: instruction → bounded parameter deltas, clamped to GUARDRAILS.md, expiring at a tick. |

### Latest team memory (from `.ai/memory.md`, newest first)

- [2026-10-03] finding — the simulator smoke is the merge gate (`scripts/sim_smoke.py`, CI `sim-smoke`)
- [2026-10-03] gotcha — Greptile hit its 50-credit trial limit; `/pr-review` is the gate now
- [2026-10-03] finding — the target is now the flag BAZAAR_SIM, never a URL
- [2026-10-03] gotcha — an undeclared hand-set variable is deleted by `railway config apply`
- [2026-10-03] build-error — a 64 KB pytest parametrize id killed the CI test step
- [2026-10-03] gotcha — the simulator's database is `bazaar_sim`, beside `railway` on the same server
- [2026-10-03] gotcha — Railway IaC cannot declare a generated `*.up.railway.app` domain
- [2026-10-03] finding — a dealer's "Deal!" settles in the SAME tick as the message

<!-- BAZAAR:STATUS:END -->

## Activity

<!-- BAZAAR:ACTIVITY:START -->
<!-- Generated by CI on every push to main (.github/workflows/readme.yml). Do not edit by hand. -->

### Recently merged

| PR | Title | Merged | Commit |
|---|---|---|---|
| [#82](../../pull/82) | feat: timed roadmap on the architecture page | Sat 02:37 | `16552b7` |
| [#83](../../pull/83) | chore: make the agent harness Claude-only and remove unused files | Sat 02:34 | `e91a8de` |
| [#76](../../pull/76) | chore: pr-reviewer sub-agent + /pr-review merge gate (replaces Greptile) | Sat 02:16 | `6d729ce` |
| [#74](../../pull/74) | docs: status page after #55 and #69 | Sat 02:10 | `7c10b7b` |
| [#69](../../pull/69) | fix(status): publish an allow-listed public view of decisions (no values, limits, reasons) | Sat 02:08 | `d5e769e` |
| [#55](../../pull/55) | feat: a simulated Bazaar API (bazaar-sim) to test every agent while the game is closed | Sat 02:06 | `9c8cbda` |
| [#70](../../pull/70) | docs: taker and maker live; new decisions on the status page | Sat 01:48 | `3e5a4a6` |
| [#64](../../pull/64) | docs: architecture status after #57 and #59, bazaar-mcp live URL | Sat 01:45 | `c73ee77` |
| [#67](../../pull/67) | docs: sync the plan's task index with the triaged GitHub issues | Sat 01:42 | `68aa1b7` |
| [#66](../../pull/66) | docs: first eval target is a nice-to-have; evals merged | Sat 01:30 | `3f737f0` |
| [#58](../../pull/58) | feat: online-outcome evals in Postgres + Phoenix annotations (bazaar-evals) | Sat 01:29 | `c2f122b` |
| [#65](../../pull/65) | docs: architecture status after #57/#59, token no longer blocked | Sat 01:24 | `be99f75` |

### Open pull requests

| PR | Title | Branch |
|---|---|---|
| [#89](../../pull/89) | feat: live-feed reader learns dealer blockers; the taker skips them (N12, part 1) | `ogarciarevett/feat-feed-reader-rag` |
| [#88](../../pull/88) | feat: Linear-style roadmap timeline (Fri 2 → Sun 4, freeze Sun 06:00, deadline Sun 14:00) | `feat/roadmap-timeline` |
| [#87](../../pull/87) | feat(plan): page economics and the cash plan (W7, read-only) | `night/w7-page-economics` |
| [#86](../../pull/86) | Night W2b: duel policy v2 behind duel_policy = v1 (silence is free, one accept per tick) | `night/w2b-duel-v2` |
| [#85](../../pull/85) | feat: declare bazaar-live (the show + TTS proxy) in .railway/railway.py | `ogarciarevett/railway-bazaar-live` |
| [#84](../../pull/84) | feat(market): bench broker edge for the Market Test (W1b, stacked on #71) | `night/w1b-broker-edge` |
| [#81](../../pull/81) | feat(ladder): ladder maximiser: floor table, bid plans, backtest, 09:00 schedule (W3, stacked on #61) | `night/w3-ladder` |
| [#80](../../pull/80) | Night W2a: duel rival zoo + replay harness on the real practice payloads | `night/w2a-duel-zoo` |
| [#79](../../pull/79) | feat(trade-desk): rival affinity map, per-counterparty cap, 09:00 dry-run trade plan (W4) | `night/w4-trade-desk` |
| [#78](../../pull/78) | night(W5+W6): score simulator, red-team injection tests, request budget, morning summary | `night/w5w6-score-redteam-morning` |
| [#77](../../pull/77) | feat(sim): realistic Market Test bench (arrivals, firm/impatient traders, relaxing quotes, stall replica, oracle) | `night/w1a-bench-sim` |
| [#75](../../pull/75) | ci: the simulator smoke is the merge gate, and Test on the simulator in the README | `ogarciarevett/sim-merge-gate` |
| [#73](../../pull/73) | fix: no OFF services on Railway (monitor + evals removed); BAZAAR_LIVE kept; docs say taker/maker are LIVE | `ogarciarevett/fix-railway-live-monitor` |
| [#72](../../pull/72) | fix(agents): cash and spend accounting within a tick, open thread bids, dated refunds | `fix/cash-spend-accounting` |
| [#71](../../pull/71) | feat(market): venue and broker, build only (exact matcher, dry run, allow_venue_open) | `feat/venue-broker-build-only` |
| [#68](../../pull/68) | fix: the kill switch holds (no cancels, closes or walks), read live; bazaar flatten cancels on purpose | `fix/kill-switch-hold` |
| [#62](../../pull/62) | fix(ledger): reconnect the shared ledger, keep the duel loop alive, require it for live writes | `fix/shared-ledger-reconnect` |
| [#61](../../pull/61) | fix(dealer): never close at the dealer's opening ask; busy accept slot waits; desk settle timeout | `fix/dealer-ladder-counter` |
| [#60](../../pull/60) | fix(duels): keep two-issue duel offers strictly inside our limit (+ duel_inside_limit guardrail) | `fix/duel-offers-inside-limit` |
| [#46](../../pull/46) | docs(adr): trace agent behavior in Phoenix — turns, typed spans, sessions | `docs/adr-agent-tracing` |

<!-- BAZAAR:ACTIVITY:END -->
