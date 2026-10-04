# Bazaar · Team 1

Autonomous trading agents for **The Bazaar**, Causa Prima's Madrid hackathon
(2–4 October 2026). We negotiate with dealers and other teams, trade collectible
cards, and run a market that matches other players' orders.

The system combines tick-driven trading, a shared Postgres memory and ledger,
Jev or Claude verdicts, and guarded execution through the organisers' Python SDK.
The [Bazaar Live](https://github.com/claude-hackaton-madrid-team-1/bazaar-live)
frontend lives in a separate repository.

[Architecture](docs/architecture.html) · [Operations](docs/operations.md) ·
[Service contracts](docs/services.md) · [Game rules](vendor/bazaar-kit/RULES.md) ·
[Presentation](docs/pitch/story.md)

## What is here

| Component | Responsibility | Source |
|---|---|---|
| Taker | Buys from venue boards, negotiates with dealers, handles team conversations | [`agents/`](src/bazaar_agent/agents/) |
| Maker | Lists and reprices orders, runs our broker; optional dealer sell desk | [`agents/maker.py`](src/bazaar_agent/agents/maker.py) |
| Duel player | Negotiates scheduled duels with a separate scoring model | [`agents/duelist.py`](src/bazaar_agent/agents/duelist.py) |
| Strategy and decisions | Ranks opportunities; requests Jev or Claude verdicts | [`strategy.py`](src/bazaar_agent/strategy.py), [`jev/`](src/bazaar_agent/jev/) |
| Execution and controls | Checks prices, inventory, approvals, deadlines and team-wide budgets | [`guardrails.py`](src/bazaar_agent/guardrails.py), [`runtime/`](src/bazaar_agent/runtime/) |
| Memory and learning | Stores feed events, holdings, counterparties, decisions and outcomes | [`db.py`](src/bazaar_agent/db.py), [`learn/`](src/bazaar_agent/learn/) |
| CLI, desk and MCP | Operator commands, a Claude Agent SDK desk, authenticated runtime tools | [`cli.py`](src/bazaar_agent/cli.py), [`runtime/`](src/bazaar_agent/runtime/) |
| Simulator | Local HTTP game implementation for repeatable development | [`bazaar_sim/`](src/bazaar_sim/) |
| Observability and evals | Phoenix traces and outcome scorecards | [`telemetry.py`](src/bazaar_agent/telemetry.py), [`evals/`](src/bazaar_agent/evals/) |

The [architecture page](docs/architecture.html) maps these components to their
processes, data stores and boundaries. It describes the repository's implementation;
service health and operator-selected modes must be checked separately.

## Start locally

Use Python 3.12, [uv](https://docs.astral.sh/uv/) and Docker for Postgres.
No game credentials are needed to explore the CLI or run the local simulator.

```sh
git clone https://github.com/claude-hackaton-madrid-team-1/bazaar.git
cd bazaar
uv sync --locked
git config core.hooksPath .githooks
uv run bazaar --help
```

Copy `.env.example` to `.env` if you need local settings; keep credentials out of
Git. For AI contributors, `sh scripts/install.sh` also generates the local Claude
commands and skills. See [the agent harness](docs/agent-harness.md).

### Simulator

Start an isolated, in-memory game in one terminal:

```sh
SIM_DATABASE_URL=memory uv run bazaar-sim serve
```

In another terminal:

```sh
BAZAAR_SIM=local uv run bazaar status
BAZAAR_SIM=local uv run bazaar clock
BAZAAR_SIM=local uv run bazaar agent taker --max-ticks 5
```

The taker command is a dry run unless `--live` or process-level `BAZAAR_LIVE=1`
is set. Keep that variable unset while exploring. The target banner should say
**SIMULATOR**. `BAZAAR_SIM=1` selects the shared hosted simulator; unset or `0`
selects the real game. `BAZAAR_URL` is rejected. See
[simulator configuration](docs/operations.md#simulator) for persistence and reset.

### Database

```sh
uv run bazaar db up
uv run bazaar db init
uv run bazaar db check
```

The local database is Postgres 17 with pgvector on `localhost:5433`.
`DATABASE_URL` selects another database. Live writers share one database and ledger;
never point integration tests at that database. See
[shared database setup](docs/operations.md#shared-database).

## Everyday commands

Run `uv run bazaar <command> --help` for the current flags. Commands against the
real game require the team's `BAZAAR_KEY`; use `BAZAAR_SIM=local` for development.

| Command | Purpose |
|---|---|
| `uv run bazaar status` | Holdings, cash, album and score |
| `uv run bazaar clock` | Current tick and available action budget |
| `uv run bazaar strategy` | Ranked opportunities and guardrail verdicts |
| `uv run bazaar rules` | Active limits and the code enforcing them |
| `uv run bazaar monitor` | Capture the feed and update shared intelligence |
| `uv run bazaar agent taker` / `agent maker` | Run a trading agent; dry by default |
| `uv run bazaar duel run` | Run the duel policy; `--play` enables sends |
| `uv run bazaar ask "…"` | Ask the desk for a proposal; never executes trades |
| `uv run bazaar llm` | Model settings and credential presence, without values |
| `uv run bazaar evals report` | Stored outcome scorecard |
| `uv run bazaar obs status` | Tracing configuration and Phoenix connectivity |

## Runtime rules

- **The game clock drives every loop.** All processes share one team's API and
  action budgets. The ledger coordinates accepts, listings and spend.
- **Inventory is checked before a trade.** Never sell or swap our only copy of a
  page card, or sell below the configured floor. Trades at the human-approval
  threshold require explicit approval.
- **Writes are guarded.** A live writer needs the shared ledger. Missing ledger,
  expired tick budget or a guardrail denial prevents the send.
- **Pause applies per data directory.** `touch .local/PAUSE` stops writes for this
  checkout. Railway services have separate volumes; pause each affected service.
  Existing offers remain open until cancelled.

The executable policies are [GUARDRAILS.md](GUARDRAILS.md),
[STRATEGY.md](STRATEGY.md) and [RUNTIME.md](RUNTIME.md). Read their current values
rather than copying historical limits. The [operations guide](docs/operations.md)
covers modes, approvals, pause, model configuration and deployment.

## Test and format

[Depot CI](.depot/workflows/tests.yml) runs exactly four checks:

```sh
uv run ruff check src tests scripts
uv run black --check src tests scripts
uv run pytest tests -m 'not integration' -q
DATABASE_URL=postgresql://bazaar:bazaar@localhost:5433/bazaar uv run pytest tests -m integration -q
```

Start the local database first for integration tests. CI supplies its own isolated
Postgres 17 + pgvector service. Database tests can change data: use a development
database, never the shared game database.

For the full local contribution gate:

```sh
DATABASE_URL=postgresql://bazaar:bazaar@localhost:5433/bazaar uv run pytest --cov
uv run mypy src
uv run ruff format --check src tests scripts
sh scripts/sync-ai-docs.sh
python3 scripts/readme_status.py --check
python3 scripts/architecture_page.py --check
```

Coverage must reach 80% under the [contribution contract](AGENTS.md). Format changes
with `uv run black src tests scripts`. The manual
`uv run python scripts/sim_smoke.py` diagnostic is optional, outside CI.

## Documentation and repository map

| Need | Read |
|---|---|
| Operate or troubleshoot the agents | [Operations](docs/operations.md), [service contracts](docs/services.md) |
| Understand game mechanics and scoring | [Official rules](vendor/bazaar-kit/RULES.md), [briefing](docs/briefing.md) |
| Understand the implementation | [Architecture](docs/architecture.html), [project spec](.ai/specs/01-spec.md) |
| Replay decisions and prepare the demo | [Observability](docs/observability.md), [pitch materials](docs/pitch/) |
| Find work or historical decisions | [Local backlog](.ai/specs/02-plan.md), [decision log](docs/decisions.md), [team memory](.ai/memory.md) |
| Connect a teammate’s MCP client | [MCP skill](.ai/skills/bazaar-mcp/SKILL.md), [reviewed operator proposals](docs/operations.md#exact-operator-proposals) |
| Contribute with an AI agent | [AGENTS.md](AGENTS.md), [agent harness](docs/agent-harness.md) |

`src/` holds runtime code; `tests/` holds unit and integration tests; `scripts/`
holds development and maintenance tools. `vendor/` contains the organisers' kit
and reference SDKs. `.railway/` declares deployment resources, `.depot/` declares
CI, and `.ai/` holds the project contract, local backlog and shared memory.
Historical investigations remain in `docs/night/` and the decision log; they are
not current deployment instructions.

## Contributing and generated files

Work on a feature branch and open a PR. Run `/pr-review <number>` and resolve
blocking findings before requesting a merge. The coordinator merges through
`scripts/merge_safe.sh <number>` after review and green CI; runtime changes can
redeploy the agents, so the game-aware deploy guard must pass.

Edit `.ai/context.md` or `.ai/pipeline.md`, then run `sh scripts/sync-ai-docs.sh`
to update generated `AGENTS.md` and `CLAUDE.md`. The architecture source is
`docs/architecture.status.json` with `scripts/templates/architecture.html.tmpl`;
run `python3 scripts/architecture_page.py` to regenerate the HTML.

The commit hook refreshes the compact project metadata below and the architecture
page. It does not copy the backlog, activity feed or team memory into this README.
After a merge that changes the HTML, the coordinator republishes the
[shared architecture artifact](https://claude.ai/artifact/SDYmzHVWNbUpUb6UyGnVkR).

<!-- BAZAAR:STATUS:START -->
<!-- Generated by scripts/readme_status.py from pyproject.toml. -->
Python `>=3.12` · CLI entry points: `bazaar`, `bazaar-sim`.
<!-- BAZAAR:STATUS:END -->
