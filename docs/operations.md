# Operating Bazaar

Start with the [README](../README.md) for local setup and the component map.
This guide covers the operator workflow. [Service contracts](services.md) describe
the HTTP, WebSocket and MCP interfaces; [GUARDRAILS.md](../GUARDRAILS.md),
[STRATEGY.md](../STRATEGY.md) and [RUNTIME.md](../RUNTIME.md) hold current policy.
The official [game rules](../vendor/bazaar-kit/RULES.md) govern play.

## Real game setup

1. Copy `.env.example` to `.env` and set `BAZAAR_KEY` privately. Do not overwrite an
   existing `.env`. Keep keys, database URLs and tokens out of commits and logs.
2. Leave `BAZAAR_SIM` unset or set it to `0` for the real game. Remove any obsolete
   `BAZAAR_URL`; the application rejects it rather than sending a key to an arbitrary host.
3. Set the shared `DATABASE_URL` for live writers. Run `uv run bazaar db check`
   to verify connectivity without printing the password.
4. Run `uv run bazaar status`, then `uv run bazaar clock` and `uv run bazaar rules`.
   Check the target banner, holdings, available tick budget and active rules before acting.

The process environment overrides `.env`. `BAZAAR_ENV_FILE` can select a different,
existing file by absolute path. Live mode and the decider switches below are
process-level settings; putting them in `.env` does not activate them.

Re-read holdings after a deal. Duels, dealer negotiations, team trades and market
matching have different scoring models; do not apply lessons from one to another.
The [briefing](briefing.md) explains those distinctions.

## Simulator

| Setting | Target |
|---|---|
| `BAZAAR_SIM=local` | Local simulator at `127.0.0.1:8765` |
| `BAZAAR_SIM=1` | [Hosted simulator](https://bazaar-sim-production-1d48.up.railway.app) |
| Unset or `0` | Real game |

The default simulator key is `sim-team1`; use `BAZAAR_SIM_KEY=sim-team2` through
`sim-team8` to avoid sharing another developer's team. Simulator mode does not load
the real team key. It stores client files under `.local/sim-client/` by default.
The simulator covers the implemented routes and mechanics; it is not a guarantee
of equivalence to every change in the organisers' live game.

```sh
# Terminal 1: a disposable local world.
SIM_TICK_SECONDS=2 SIM_DATABASE_URL=memory uv run bazaar-sim serve

# Terminal 2: explicitly select that world for every command.
BAZAAR_SIM=local uv run bazaar status
BAZAAR_SIM=local uv run bazaar agent taker --max-ticks 5
BAZAAR_SIM=local uv run bazaar agent taker --live --max-ticks 10
BAZAAR_SIM=local uv run bazaar duel run --play --max-ticks 20
```

The last two commands send simulated actions. `BAZAAR_SIM_PORT` selects another
local port. Set `BAZAAR_SIM_DATABASE_URL` to a separate `bazaar_sim` database to
persist client decisions; `SIM_DATABASE_URL` controls the simulator server's world.
Do not use the real-game database for either. The application explicitly rejects
simulator use of the team's `railway` database.

`uv run python scripts/sim_smoke.py` is an optional local diagnostic. It starts its
own server, uses an isolated environment and blocks non-loopback connections.
Stop another server on the chosen port before running it. It is not a CI gate.

The shared simulator can be reset with `uv run bazaar-sim reset --url <simulator-url>`.
Resetting destroys that world's current state: coordinate with other users first.
Supply `SIM_ADMIN_TOKEN` privately through the environment; never paste it into a
command argument, document or chat. Local in-memory worlds disappear on restart.

## Shared database

Local development uses `docker-compose.yml`: Postgres 17 with pgvector, bound to
`127.0.0.1:5433`. Run `bazaar db up`, `db init`, then `db check` through `uv run`.
Schema initialization is repeatable. `bazaar db tables` lists tables and row counts;
`bazaar db load` imports captured data and may read live game data, so inspect its
flags and target before running it.

Railway services use the private Postgres address. Laptops need the public proxy
URL from Postgres's **Connect** panel, stored privately as `DATABASE_URL`; the
`*.railway.internal` hostname is not reachable from a laptop. Require TLS for the
public connection. Do not copy the URL into diagnostic output.

The shared database holds the team ledger, holdings snapshots, feed events,
counterparty intelligence, learnings, decisions, executions and outcomes. A valid
same-tick holdings snapshot can save a `/me` request; sends invalidate it, and
stale or uncertain snapshots cause a fresh read. `bazaar status --no-db` requests a
live `/me` directly. See [table contracts](services.md#holdings-and-catalog-tables-postgres).

All live writers must share the ledger. Missing or unreachable Postgres stops live
writes. Local dry runs may use local files. Some state remains per process or per
data directory: a laptop's steering or pause file does not reach Railway.

Integration tests use their own development database and can change its contents.
Never run them against the shared game's `DATABASE_URL`. Set it explicitly to the
local test database when following the README's test commands.

## Agents and modes

| Command | Behavior |
|---|---|
| `uv run bazaar agent taker` | Venue purchases, dealer negotiations and team desk |
| `uv run bazaar agent maker` | Listings, repricing, venue broker; optional dealer sell desk |
| `uv run bazaar duel run` | Scheduled duel policy |
| `uv run bazaar broker run` | Standalone broker for our venue |
| `uv run bazaar agent chat` | Interactive Claude desk with guarded runtime tools |

The taker and maker are dry runs unless `--live` or process-level `BAZAAR_LIVE=1`
is set. The duel player sends with `--play`. Check each command's `--help` rather
than assuming every command has the same live switch. Never start another live
copy casually: all copies consume the same team budget.

The maker's dealer sell desk is implemented but `dealer_sell_enabled` is off in
the checked-in policy. When enabled it can accept dealer offers, so it also shares
the accept budget. The venue broker uses its separate broker key; matching other
teams' orders is distinct from trading our own cards. The default broker policy is
`exact`; `BAZAAR_BENCH_POLICY=edge` selects the experimental guarded policy.

Every tick is driven by `/api/clock`. `BAZAAR_TICK_OFFSET_S` staggers service reads;
see [the documented offsets](services.md#one-key-staggered-ticks-bazaar_tick_offset_s).
Laptop commands share the same budget: run live inspections serially and keep one
team monitor. A refused or late action is not permission to retry in a tight loop.

## Pause writes

For a local process, create `PAUSE` in its actual data directory:

```sh
mkdir -p .local
touch .local/PAUSE
```

The default real-game directory is `.local/`; simulator clients default to
`.local/sim-client/`. `BAZAAR_DATA_DIR` overrides it. Each Railway worker has its own
volume, so a local file does not stop the deployed services. With the coordinator,
pause each affected service, for example:

```sh
railway ssh --service bazaar-taker -- touch /app/.local/PAUSE
railway ssh --service bazaar-maker -- touch /app/.local/PAUSE
railway ssh --service bazaar-duels -- touch /app/.local/PAUSE
railway ssh --service bazaar-mcp -- touch /app/.local/PAUSE
```

A pause or a switch to dry mode does **not** withdraw standing offers. Inspect
`uv run bazaar sell offers` and arrange deliberate cancellations separately.
Resume only after checking the reason for the pause, by removing the correct
service's `PAUSE` file. Never use this to bypass a denied trade or approval.

`uv run bazaar breaker list` shows shared circuit breakers. The watchdog can trip
scopes after harmful behavior; inspect the reason before any reset. Human approval
is a separate control (`uv run bazaar approve --help`); a breaker reset never
replaces it. Protect sole page-card copies and the configured selling floor.

## Models and the desk

`uv run bazaar llm` shows configuration and whether credentials exist, without
printing them. Set only the credentials needed for the path you use:

| Setting | Purpose |
|---|---|
| `TYPESAFE_API_KEY` | Jev decisions |
| `ANTHROPIC_API_KEY` | Claude via API |
| `CLAUDE_CODE_OAUTH_TOKEN` | Claude subscription path when no Anthropic API key is set |
| `OPENAI_API_KEY` | OpenAI model choices |
| `BAZAAR_LLM_RUNTIME` / `--llm-runtime` | Pin the runtime model instead of automatic choice |
| `BAZAAR_DECIDER=llm` | Use Claude for the verdict interface instead of default Jev |

The decider's model, timeout, cache and per-process call limits are configured by
`BAZAAR_DECIDER_*` environment variables; see
[`jev/decider.py`](../src/bazaar_agent/jev/decider.py) for supported values.
An undecided or failed model call follows the caller's fallback; it never grants
permission to skip a guardrail.

[RUNTIME.md](../RUNTIME.md) controls model selection, word generation, feed reading
and desk budgets. In the checked-in defaults `llm_words` and `llm_read_feed` are
false. Enabling the CLI feed-reader switch alone does not override those settings.
The code sets structured prices and validates tool actions; model-written words
do not authorize a trade.

```sh
uv run bazaar ask "show opportunities for our missing cards"
uv run bazaar agent chat
uv run bazaar steer --show
```

`ask` only proposes, even when live mode is set. `agent chat` can use live guarded
tools when explicitly enabled. `steer` stores bounded, temporary strategy changes
in the process's data directory; a local change does not steer another container.
The desk gives its agents scoped tools rather than shell or filesystem access.

The [remote MCP server](services.md#bazaar-mcp-the-runtime-tools-over-mcp-not-read-only-bearer-token)
exposes those tools to Team 1 clients. It requires a bearer token, and human approval
tools additionally require the separate approver credential. Never give another
team access to it. Read-only agent status endpoints are a different interface.

For a teammate's MCP client, use the Streamable HTTP endpoint
`https://bazaar-mcp-production.up.railway.app/mcp` and configure its `Authorization`
header as `Bearer <BAZAAR_MCP_TOKEN>` using the client's private secret settings.
Obtain the existing token privately from the team; do not generate a replacement
just to connect another client. Ordinary clients do not need the approver credential.

## Monitoring, learning and evals

Run `uv run bazaar monitor` on one team laptop to capture feed history and update
shared intelligence. `--notify` enables local notifications; `--no-db` keeps a
local capture. There is no monitor service in the checked-in Railway declaration.

Useful inspections include `bazaar traders`, `alerts`, `curves`, `teams`, `book`,
`tape`, `supply`, `threads` and `thread <id>` (all through `uv run`). Prefer existing
captured data over redundant API reads. A full supply scan uses the team's budget;
check its flags and coordinate it with the active workers.

Agents learn from feed events and negotiation outcomes, with scoped recall from
Postgres. `BAZAAR_LEARN=0` disables an agent's learning path;
`BAZAAR_LLM_READ=0` disables the taker's optional language-model feed pass. See
[`learn/`](../src/bazaar_agent/learn/) and `uv run bazaar learn --help`.

For local tracing, run `uv run bazaar obs up`, set `BAZAAR_TRACING=1` and inspect
`uv run bazaar obs status`. The UI is at `http://127.0.0.1:6006`. Shared Phoenix
uses its configured collector URL and ingestion key. Keep the local bind on
loopback unless you have deliberately secured access.
[Observability](observability.md) explains how to replay a negotiation.

To send laptop traces to shared Phoenix, create your own API key in its
**Settings → API Keys**, then set these values in your private `.env`:

```dotenv
BAZAAR_TRACING=1
PHOENIX_COLLECTOR_ENDPOINT=https://phoenix-production-6aa3.up.railway.app
PHOENIX_API_KEY=<your-private-key>
```

Verify with `uv run bazaar obs status`; `uv run bazaar obs spans` lists recent
spans using that key. Never commit the populated file.

`uv run bazaar evals run` scores stored settled outcomes; `evals report` displays
them. Active agents also run evals in the background (`--evals-every`, `0` disables).
The evals are diagnostics with documented estimation limits, not a replacement for
the organisers' score. See [evals contracts](services.md#evals-scorecard-postgres).

## Production on Railway

[`.railway/railway.py`](../.railway/railway.py) declares the duel worker, taker,
maker, MCP server, simulator, Phoenix and the separate-repository Bazaar Live show.
Postgres is an existing external resource referenced by this partial declaration.
The configured resources do not establish current uptime or live/dry mode.

Use [the service URLs](services.md) to inspect health and the agent mode. Logs such
as `railway logs --service bazaar-taker` can explain refusals; do not publish raw
logs without checking them for private state. Never dump Railway variable values.

Deployment changes belong to the coordinator. First preview with
`uv run --group infra railway config plan`; review every change, especially resource
deletions. Runtime watch patterns determine what redeploys after a merge. Operator
secrets and live-mode overrides are preserved by the declaration, not committed.

Before merging a reviewed PR with green CI, the coordinator runs
`scripts/merge_safe.sh <number>`. The deploy guard reads the clock, duels and
schedule and refuses unsafe windows or unreadable state. Do not bypass it to force
a deployment during a duel or Market Test.

## Common problems

| Symptom | Check |
|---|---|
| Wrong target or obsolete URL error | `BAZAAR_SIM`, process environment and `.env`; remove `BAZAAR_URL` |
| Live writer refuses to start | Shared `DATABASE_URL`, connectivity and initialized schema |
| Nothing sent | Target, live mode, `PAUSE`, breaker state, guardrail verdict and tick budget |
| Local steering or pause has no production effect | Each process's data directory and Railway volume |
| Repeated rate limits | Duplicate workers/monitors and stagger settings; one team shares the budget |
| Model calls fail | Credential presence, decider budgets and runtime settings; inspect scrubbed logs |
| Documentation becomes stale | Regenerate from canonical sources as described in the README |
