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
uv run bazaar monitor --notify           # KEEP RUNNING: feed + traders + /me snapshots + new-dealer alerts
uv run bazaar traders                     # every dealer and team the monitor has seen
uv run bazaar alerts                      # new dealers, levels going active, announcements
uv run bazaar curves --dealer abuela      # Abuela's concession curve from every team's threads
uv run bazaar teams                       # the competition: flow, spend, inferred ×1.6 set
uv run bazaar book                        # El Rastro order book, pseudonyms resolved to teams
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
  second monitor doubles the team's API reads against the 5 req/s limit. Readers (`traders`,
  `db tables`, `db check`, analyses) can run anywhere.
- **Tests on the shared database.** `tests/test_db.py` runs in its own `bazaar_pytest_<random>`
  schema per test and drops it, so teammates can run the suite at once and real tables are never
  touched.

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
| `monitor tick N` | `bazaar monitor` | new events, newest id, gap flag, dealer/team/level counts, our cash/level/score; every `alert` and new or changed `trader` as an event; DB failures as exceptions (the tick goes on) |
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
- **README stays current by itself:** the pre-commit hook runs `scripts/readme_status.py`, which
  rebuilds the block below from the plan, the CLI and the memory log and stages README.md.
  On a conflict inside the block, take either side and rerun `python3 scripts/readme_status.py`.
- **Backlog:** GitHub issues are the source of truth; the plan mirrors them.
- **Never** push from an agent, never commit `.env`, one team key only.

## Live status

<!-- BAZAAR:STATUS:START -->
<!-- Generated by scripts/readme_status.py on every commit. Do not edit by hand. -->

### Backlog (from `.ai/specs/02-plan.md`)

| Task id | Title | Phase | Status |
|---|---|---|---|
| [#21](https://github.com/claude-hackaton-madrid-team-1/bazaar/issues/21) | Feed capture + dealer curves | 0 → 1 | ✅ `bazaar monitor` (feed, traders, alerts, snapshots) |
| [#2](https://github.com/claude-hackaton-madrid-team-1/bazaar/issues/2) | Team key + API client + fixtures | 0 | ⬜ (blocked on P1) |
| [#3](https://github.com/claude-hackaton-madrid-team-1/bazaar/issues/3) | Tick loop, governor, scheduler, kill switch | 0 → 1 | ⬜ |
| [#8](https://github.com/claude-hackaton-madrid-team-1/bazaar/issues/8) | Abuela negotiator (concession curve) | 0 | ✅ 4 negotiated deals (7/9/9/22), score 11.3 |
| [#9](https://github.com/claude-hackaton-madrid-team-1/bazaar/issues/9) | Ladder maximizer + reach L2 | 0 → 2 | ⬜ |
| [#4](https://github.com/claude-hackaton-madrid-team-1/bazaar/issues/4) | Duel logger (practice h2) | 0 | 🔵 `bazaar duel run --play` running, waiting for h2 |
| N1 (new) | Memory schema + repository + Friday backfill | 1 | 🔵 schema + `db load` done |
| N2 (new) | Intel: order book, tape, competitor profiles | 1 | ⬜ |
| N3 (new) | Learner + embeddings + RAG context | 1 | ⬜ |
| [#1](https://github.com/claude-hackaton-madrid-team-1/bazaar/issues/1) | Decision model: decider + Jev packs + policy | 1 | ⬜ |
| [#10](https://github.com/claude-hackaton-madrid-team-1/bazaar/issues/10) / [#24](https://github.com/claude-hackaton-madrid-team-1/bazaar/issues/24) | Executor firewall, offer inspector, flags | 1 → 2 | ⬜ |
| N4 (new) | `service.py` + CLI + bazaar skill + commands | 1 | 🔵 first CLI + table commands done |
| N5 (new) | Jev port to Python (judge, mask, log, report, parity) | 0 → 1 | 🔵 judge/mask/log/report done (135 tests, live parity); recorded-fixture parity test left |
| N6 (new) | Voice interface: ElevenLabs agent + Python tool server | 4 | ⬜ |
| N7 (new) | Observability: OTel traces → Phoenix (negotiations, duels, monitor, console), `bazaar thread(s)` | 1 | 🔵 PR open (`obs up`, `obs status`, `thread 115`) |
| [#14](https://github.com/claude-hackaton-madrid-team-1/bazaar/issues/14) / [#23](https://github.com/claude-hackaton-madrid-team-1/bazaar/issues/23) | Valuation, buy/sell lists | 1 | ⬜ |
| [#11](https://github.com/claude-hackaton-madrid-team-1/bazaar/issues/11) / [#12](https://github.com/claude-hackaton-madrid-team-1/bazaar/issues/12) | Venue + limit-estimating broker | 1 → 2 | ⬜ |
| [#13](https://github.com/claude-hackaton-madrid-team-1/bazaar/issues/13) | Organic market making | 2 | ⬜ |
| [#5](https://github.com/claude-hackaton-madrid-team-1/bazaar/issues/5) / [#7](https://github.com/claude-hackaton-madrid-team-1/bazaar/issues/7) | Duel policy, days module | 1 → 2 | ⬜ |
| [#15](https://github.com/claude-hackaton-madrid-team-1/bazaar/issues/15) | Score simulator + dashboard | 2 (nice-to-have) | ⬜ |
| [#16](https://github.com/claude-hackaton-madrid-team-1/bazaar/issues/16) / [#17](https://github.com/claude-hackaton-madrid-team-1/bazaar/issues/17) | Pitch + scoring tracker | 3 | ⬜ |

### CLI commands (from `src/bazaar_agent/cli.py`)

| Command | What it does |
|---|---|
| `uv run bazaar clock` | Current tick, pace, doors, per-tick limits and the action budget left in this tick. |
| `uv run bazaar dealers` | Dealers in play: traits, menu, list prices, hourly quotas. |
| `uv run bazaar tape` | Every settlement (trade print): who bought what from whom, at what price. |
| `uv run bazaar curves` | Dealer concession curves rebuilt from every team's public threads. |
| `uv run bazaar teams` | The competition: each team's flow (dealer bids, buys, sells, listings, inferred ×1.6 set). |
| `uv run bazaar book` | Live order book of a venue, with board pseudonyms resolved to team ids from the feed. |
| `uv run bazaar status` | Our cash, level, score, album pages with missing cards, and cards (GET /api/me). |
| `uv run bazaar threads` | Our negotiation threads (GET /api/me/threads): who, what, status and the last message. |
| `uv run bazaar thread` | One whole conversation (GET /api/threads/{id}): every message with sender, text and price. |
| `uv run bazaar dealer buy` | Buy one card or pack from a dealer: rising distinct bids, accept at our next bid, hard max. |
| `uv run bazaar duel run` | Every tick: log raw /api/duels to .local/duels; with --play, offer/accept inside our limit. |
| `uv run bazaar rules show` | Every guardrail from GUARDRAILS.md, its value, and the code that enforces it. |
| `uv run bazaar rules check` | Dry-run one action against the guardrails with our live /me, clock and ledger. |
| `uv run bazaar monitor` | The monitoring agent: per tick feed → JSONL + Postgres, traders sync, /me snapshot, new-trader alerts. |
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

### Latest team memory (from `.ai/memory.md`, newest first)

- [2026-10-02] gotcha — typer 0.27 vendors click: `import click` fails
- [2026-10-02] build-error — a CLI test with a frozen fake clock hung forever
- [2026-10-02] gotcha — Phoenix's hosted cloud is gone; share a self-hosted Phoenix instead
- [2026-10-02] gotcha — libpq echoes the password when it cannot parse DATABASE_URL
- [2026-10-02] finding — Railway's default Postgres image ships pgvector, despite its docs
- [2026-10-02] gotcha — `python -m bazaar_agent.jev` reads TYPESAFE_API_KEY only from the environment
- [2026-10-02] finding — El Chato announced (next dealer), seen by the monitor at tick 76
- [2026-10-02] finding — LAV-04 bought at 9 (thread 101, 5 ticks); Abuela accepted OUR bid

<!-- BAZAAR:STATUS:END -->
