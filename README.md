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
uv run bazaar monitor --notify           # KEEP RUNNING: live stream + per-tick poll, traders, /me, alerts in seconds
(cd web && npm ci && npm run build)       # once per web change: static export to web/out
uv run --project tui tui/serve.py         # web view on http://localhost:8777, fed by the monitor (--mock: mock game)
uv run bazaar traders                     # every dealer and team the monitor has seen (our row: status `us`)
uv run bazaar alerts                      # new dealers, levels going active, announcements
uv run bazaar curves --dealer abuela      # Abuela's concession curve from every team's threads (--ours/--theirs)
uv run bazaar teams                       # the competition: flow, spend, inferred ×1.6 set (us apart)
uv run bazaar book                        # El Rastro order book, pseudonyms resolved to teams (ours apart)
uv run bazaar tape                        # every settlement with price
uv run bazaar status                      # our cash, level, score, cards (needs BAZAAR_KEY)
uv run bazaar threads                     # our negotiation threads; `bazaar thread <id>` for one
uv run bazaar obs up                      # Phoenix traces UI (then BAZAAR_TRACING=1, see Observability)

uv run bazaar db up && uv run bazaar db init && uv run bazaar db load   # Postgres + pgvector memory
uv run bazaar db tables                   # every table with its row count
```

Tests: `uv run pytest` (the DB tests are skipped when Postgres is unreachable).
Lint: `uv run ruff check . && uv run ruff format --check .` · Types: `uv run mypy src`.

## Shared database (Railway)

Every process (CLI, monitor, agents, tests) connects with ONE variable, `DATABASE_URL`, read from
the environment first, then `.env`. Unset means the local docker Postgres
(`postgresql://bazaar:bazaar@localhost:5433/bazaar`). To share one memory across laptops:

1. In Railway, open the Postgres service → **Settings → Networking → Public Access**. That creates
   the TCP proxy and the `DATABASE_PUBLIC_URL` variable
   (`postgresql://postgres:<password>@<name>.proxy.rlwy.net:<port>/railway`). Laptops need this
   public URL: Railway's own `DATABASE_URL` is the private `*.railway.internal` address, which only
   works inside the Railway project. Proxy traffic is billed as egress.
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
  An older service may need a redeploy, or use the pgvector template. Without pgvector the
  schema still creates every table and skips only the `embedding vector(384)` columns; run
  `db init` again after enabling it and they are added.
- **One monitor writes per team.** Run a single `uv run bazaar monitor` against the shared database.
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

Phoenix runs on one machine. Teammates can reach it in either of two ways. Neither is deployed yet.

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
| [#1](https://github.com/claude-hackaton-madrid-team-1/bazaar/issues/1) | Decision model: decider + Jev packs + policy | 1 | ⬜ not started (autonomous loop) |
| [#10](https://github.com/claude-hackaton-madrid-team-1/bazaar/issues/10) / [#24](https://github.com/claude-hackaton-madrid-team-1/bazaar/issues/24) | Executor firewall, offer inspector, flags | 1 → 2 | 🔵 guardrails + offer-term check done (#30, #31); executor ⬜ |
| N4 (new) | `service.py` + CLI + bazaar skill + commands | 1 | 🔵 CLI + skill done; `service.py` seam ⬜ |
| N5 (new) | Jev port to Python (judge, mask, log, report, parity) | 0 → 1 | ✅ (#29, #31); recorded-fixture parity test ⬜ |
| N6 (new) | Voice interface: ElevenLabs agent + Python tool server | 4 | ⬜ later |
| N7 (new) | Observability: OTel traces → Phoenix, `bazaar thread(s)` | 1 | ✅ (#34, #35) |
| N8 (new) | Runtime LLM: Jev-chosen model, `--llm-runtime`, ask, words, steer | 1 | 🔵 worker |
| N9 (new) | Guardrails rule book (GUARDRAILS.md) | 1 | ✅ (#30) |
| [#14](https://github.com/claude-hackaton-madrid-team-1/bazaar/issues/14) / [#23](https://github.com/claude-hackaton-madrid-team-1/bazaar/issues/23) | Strategy engine (scarcity, valuation, buy/sell, 3-pack quota) | 1 | 🔵 worker |
| [#11](https://github.com/claude-hackaton-madrid-team-1/bazaar/issues/11) / [#12](https://github.com/claude-hackaton-madrid-team-1/bazaar/issues/12) | Venue + limit-estimating broker | 1 → 2 | ⬜ not started (Market Test, Saturday) |
| [#13](https://github.com/claude-hackaton-madrid-team-1/bazaar/issues/13) | Organic market making | 2 | ⬜ |
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

- [2026-10-02] finding — the stream runs up to a tick ahead of the poll (ticks 123–129)
- [2026-10-02] gotcha — the SSE stream is the feed plus `tick` events, with no `id:` lines
- [2026-10-02] build-error — a rival's text with `[/red]` would crash `duel run --play`
- [2026-10-02] gotcha — OpenAI's id is `gpt-6.1-sol` (dot), not `gpt-6-1-sol`
- [2026-10-02] finding — Jev picks the runtime LLM decisively when the state has stakes and time
- [2026-10-02] finding — strategy engine, first live ranking (tick 95): rares first, LAT-09 is our best sell
- [2026-10-02] gotcha — typer 0.27 vendors click: `import click` fails
- [2026-10-02] build-error — a CLI test with a frozen fake clock hung forever

<!-- BAZAAR:STATUS:END -->

## Activity

<!-- BAZAAR:ACTIVITY:START -->
<!-- Generated by CI on every push to main (.github/workflows/readme.yml). Do not edit by hand. -->

### Recently merged

| PR | Title | Merged | Commit |
|---|---|---|---|
| [#40](../../pull/40) | feat: real-time monitor over the live stream, with our team told apart | Fri 22:34 | `a7c09df` |
| [#39](../../pull/39) | feat: runtime LLM layer (Jev picks the model, ask, words, steer) | Fri 22:31 | `7813244` |
| [#38](../../pull/38) | fix: greet the dealer we are actually talking to | Fri 22:16 | `5894941` |
| [#37](../../pull/37) | feat: strategy engine, sell/bid offers, pack quota and Jev pack gate | Fri 22:15 | `83007fb` |
| [#36](../../pull/36) | ci: keep the root README current after every merge to main | Fri 22:08 | `a23f074` |
| [#35](../../pull/35) | fix: a failing tracing hook can never break a live negotiation | Fri 22:04 | `3bf4527` |
| [#34](../../pull/34) | feat: observability — trace negotiations, duels, monitor and CLI output to Arize Phoenix | Fri 22:03 | `ae5293b` |
| [#33](../../pull/33) | feat: shared Railway Postgres with DATABASE_URL alone | Fri 21:57 | `4c6726f` |
| [#32](../../pull/32) | feat: monitoring agent: feed, traders DB, /me snapshots, new-dealer alerts | Fri 21:38 | `da01693` |
| [#31](../../pull/31) | fix: close the Greptile P1s on the live trading path | Fri 21:34 | `808dc3e` |
| [#30](../../pull/30) | feat: GUARDRAILS.md rule book, enforced by the runtime and shown in the CLI | Fri 21:29 | `239bb72` |
| [#29](../../pull/29) | feat: bazaar CLI, feed capture, market intel, Postgres memory, Jev in Python, dealer negotiator | Fri 21:29 | `6218d83` |

### Open pull requests

_No open PRs (or `gh` unavailable)._

<!-- BAZAAR:ACTIVITY:END -->
