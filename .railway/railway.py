"""Team 1's always-on runtime on Railway, as code (Railway Infrastructure as Code, Python authoring).

    railway config plan     # preview: what would change in project heartfelt-warmth / production
    railway config apply    # apply after review (README "Production on Railway")

This is a NAMED PARTIAL: it manages only the resources declared below. The `Postgres` service is
not declared, so no apply can change or delete it; the runtime only references its private URL.

No secret value lives in this file. `preserve()` keeps the value already set in Railway, which is
set once with `railway variable set NAME --stdin` so it never appears on a command line or in a log.
"""

from railway_sdk import define_railway, github, image, preserve, project, service, volume

PARTIAL = "bazaar-runtime"

REPO = "claude-hackaton-madrid-team-1/bazaar"
BRANCH = "main"
REGION = "europe-west4-drams3a"  # where Postgres runs: keeps the private network hop short
PHOENIX_IMAGE = "arizephoenix/phoenix:version-20.19.0"  # the same pin as docker-compose.yml
PHOENIX_PORT = "6006"  # UI, REST API and OTLP/HTTP (/v1/traces)
PHOENIX_DATA = "/mnt/data"  # Phoenix's SQLite and exports, on a volume
APP_DATA = "/app/.local"  # Railpack serves the repo from /app; .local is our data dir (and PAUSE)
VOLUME_MB = 50_000  # the size Railway gave each volume; a volume can grow but never shrink

# Railpack's default for uv is `uv sync --no-editable`, which moves bazaar_agent into site-packages
# and breaks REPO_ROOT (vendor/bazaar-kit, GUARDRAILS.md, questions/). Editable keeps it at /app.
BUILD = {
    "buildCommand": "uv sync --locked --no-dev",
    # Redeploy only when the runtime changes: README bot commits must not restart the loops.
    "watchPatterns": [
        "src/**",
        "vendor/bazaar-kit/**",
        "pyproject.toml",
        "uv.lock",
        "GUARDRAILS.md",
        "STRATEGY.md",
        "RUNTIME.md",
        "questions/**",
        ".railway/**",
    ],
}
ALWAYS = {"restartPolicyType": "ALWAYS"}
AGENT_PORT = "8080"  # each agent's read-only status: GET /health, GET /state, WS /events (public domain)


def runtime_env() -> dict:
    """What `bazaar monitor`, `bazaar duel run` and `bazaar agent` read (config.py, telemetry.py)."""
    return {
        "RAILPACK_PYTHON_VERSION": "3.12",
        "BAZAAR_DATA_DIR": APP_DATA,
        "DATABASE_URL": "${{Postgres.DATABASE_URL}}",  # private *.railway.internal URL
        "BAZAAR_TRACING": "1",
        "PHOENIX_COLLECTOR_ENDPOINT": "http://${{phoenix.RAILWAY_PRIVATE_DOMAIN}}:" + PHOENIX_PORT,
        "PHOENIX_API_KEY": "${{phoenix.PHOENIX_API_KEY}}",
        "PHOENIX_PROJECT": "bazaar",
        "COLUMNS": "200",  # rich wraps at 80 columns without a terminal: one log line per tick
        "BAZAAR_KEY": preserve(),
        "TYPESAFE_API_KEY": preserve(),
    }


def llm_env() -> dict:
    """The runtime LLM on the Claude subscription (README "LLM on the Claude subscription"), for the
    services that write negotiation words. The `claude-agent-sdk` wheel bundles the Claude Code CLI,
    so the build needs nothing more. The value is the operator's `claude setup-token` token, set by
    hand with `railway variable set ... --stdin`; unset, every LLM path falls back to its template."""
    return {"CLAUDE_CODE_OAUTH_TOKEN": preserve()}


def runtime(name: str, command: str, data: object, enabled: bool = True, llm: bool = False) -> object:
    """`enabled=False` declares no source: the service, its volume and variables stay, and no push
    can deploy it. Railway has no 0-replica setting (the minimum is 1), so this is how it is off."""
    return service(
        name,
        source=github(REPO, branch=BRANCH) if enabled else None,
        build=BUILD,
        start=f"/app/.venv/bin/bazaar {command}",
        deploy=ALWAYS,
        replicas={REGION: 1},
        volumeMounts={APP_DATA: data},
        env={**runtime_env(), **(llm_env() if llm else {})},
    )


def agent(name: str, command: str, data: object, enabled: bool = True) -> object:
    """An autonomous agent (`bazaar agent taker|maker`) and its public read-only status on AGENT_PORT.

    DRY RUN on purpose: this file never sets BAZAAR_LIVE. Live trading needs BAZAAR_LIVE=1 set by hand
    on the service (README "Autonomous agents"), never here. `enabled=False`: no source (see runtime())."""
    return service(
        name,
        source=github(REPO, branch=BRANCH) if enabled else None,
        build=BUILD,
        start=f"/app/.venv/bin/bazaar {command}",
        deploy=ALWAYS,
        replicas={REGION: 1},
        healthcheck="/health",
        volumeMounts={APP_DATA: data},
        env={**runtime_env(), **llm_env(), "PORT": AGENT_PORT},
    )


def mcp_server(name: str, data: object) -> object:
    """The runtime tools as a remote MCP server (`bazaar mcp serve`, README "Agent runtime"): Streamable
    HTTP at /mcp on AGENT_PORT, behind `Authorization: Bearer <BAZAAR_MCP_TOKEN>`. The token is set once
    by hand through stdin (`railway variable set BAZAAR_MCP_TOKEN --stdin`), never here.

    DRY RUN on purpose: this file never sets BAZAAR_LIVE, so every write tool answers what it WOULD send.
    The public domain is generated once with `railway domain --service bazaar-mcp --port 8080`, like the agents'."""
    return service(
        name,
        source=github(REPO, branch=BRANCH),
        build=BUILD,
        start="/app/.venv/bin/bazaar mcp serve --host 0.0.0.0",
        deploy=ALWAYS,
        replicas={REGION: 1},
        healthcheck="/health",
        volumeMounts={APP_DATA: data},
        env={**runtime_env(), "PORT": AGENT_PORT, "BAZAAR_MCP_TOKEN": preserve()},
    )


EVALS_EVERY_TICKS = "6"  # bazaar-evals looks for new inputs every 6 game ticks (3 min at 30 s, 90 s at 15 s)


def evals_service() -> object:
    """`bazaar evals run` on a loop (README "Evals"): Postgres in, Postgres and Phoenix annotations out.

    It never uses the team key, so it gets no BAZAAR_KEY and adds nothing to the key's 5 req/s budget: its
    loop follows the game clock through the keyless public /api/clock (tick discipline).
    Both secrets it needs are references to the services that own them, so nothing here is preserve()d.
    It writes no file: no volume."""
    return service(
        "bazaar-evals",
        source=github(REPO, branch=BRANCH),
        build=BUILD,
        start=f"/app/.venv/bin/bazaar evals run --every-ticks {EVALS_EVERY_TICKS}",
        deploy=ALWAYS,
        replicas={REGION: 1},
        env={
            "RAILPACK_PYTHON_VERSION": "3.12",
            "DATABASE_URL": "${{Postgres.DATABASE_URL}}",  # private *.railway.internal URL
            "PHOENIX_COLLECTOR_ENDPOINT": "http://${{phoenix.RAILWAY_PRIVATE_DOMAIN}}:" + PHOENIX_PORT,
            "PHOENIX_API_KEY": "${{phoenix.PHOENIX_API_KEY}}",
            "PHOENIX_PROJECT": "bazaar",
            "COLUMNS": "200",
        },
    )


SIM_PORT = "8080"
# The simulator's world lives in its OWN database on the team's Postgres server, created once with
# `create database bazaar_sim` (README "Simulator"). The simulator refuses any other database name,
# so this can never point at the real `railway` one.
SIM_DATABASE_URL = (
    "postgresql://${{Postgres.PGUSER}}:${{Postgres.PGPASSWORD}}@${{Postgres.RAILWAY_PRIVATE_DOMAIN}}:5432/bazaar_sim"
)


def simulator() -> object:
    """`bazaar-sim serve`: a simulated Bazaar API for testing every agent while the game is closed.

    Team keys are `sim-team1` ... `sim-team8`; a real key is refused. SIM_ADMIN_TOKEN (reset and
    manual ticks) is generated once and set with `railway variable set ... --stdin`, never here.
    Its public domain is a Railway-generated `*.up.railway.app` one: Railway IaC does not declare
    generated domains (docs.railway.com/infrastructure-as-code/reference, "Custom domains"), so it was
    created once with `railway domain --service bazaar-sim` and is listed in the README."""
    return service(
        "bazaar-sim",
        source=github(REPO, branch=BRANCH),
        build=BUILD,
        start="/app/.venv/bin/bazaar-sim serve --host 0.0.0.0",
        deploy=ALWAYS,
        replicas={REGION: 1},
        healthcheck="/api/health",
        env={
            "RAILPACK_PYTHON_VERSION": "3.12",
            "PORT": SIM_PORT,
            "SIM_DATABASE_URL": SIM_DATABASE_URL,
            "SIM_TICK_SECONDS": "10",
            "SIM_ADMIN_TOKEN": preserve(),
        },
    )


@define_railway
def main(ctx=None):
    phoenix_data = volume("phoenix-data", region=REGION, sizeMB=VOLUME_MB)
    monitor_data = volume("bazaar-monitor-data", region=REGION, sizeMB=VOLUME_MB)
    duels_data = volume("bazaar-duels-data", region=REGION, sizeMB=VOLUME_MB)
    taker_data = volume("bazaar-taker-data", region=REGION, sizeMB=VOLUME_MB)
    maker_data = volume("bazaar-maker-data", region=REGION, sizeMB=VOLUME_MB)
    mcp_data = volume("bazaar-mcp-data", region=REGION, sizeMB=VOLUME_MB)

    phoenix = service(
        "phoenix",
        source=image(PHOENIX_IMAGE),
        deploy=ALWAYS,
        replicas={REGION: 1},
        healthcheck="/healthz",
        volumeMounts={PHOENIX_DATA: phoenix_data},
        env={
            "PORT": PHOENIX_PORT,  # the port Railway's public domain routes to
            "PHOENIX_PORT": PHOENIX_PORT,
            "PHOENIX_WORKING_DIR": PHOENIX_DATA,
            "PHOENIX_ENABLE_AUTH": "true",
            "PHOENIX_USE_SECURE_COOKIES": "true",  # the public domain is HTTPS only
            "PHOENIX_SECRET": preserve(),
            "PHOENIX_DEFAULT_ADMIN_INITIAL_PASSWORD": preserve(),
            "PHOENIX_API_KEY": preserve(),  # a system key for span ingestion: `bazaar obs bootstrap`
        },
    )
    # OFF by team decision: the monitor runs in the CLI on a laptop (one monitor per team). Its
    # source was disconnected and its deployment removed (`railway down`). To turn it back on: set
    # enabled=True, `railway config apply` (it reconnects the repo and deploys main), and stop the
    # laptop monitor.
    monitor = runtime("bazaar-monitor", "monitor", monitor_data, enabled=False)
    duels = runtime("bazaar-duels", "duel run --play", duels_data, llm=True)
    # The autonomous agents share ONE accept per tick with bazaar-duels through the Postgres ledger
    # (duels first; the maker never accepts). Both stay in DRY RUN until BAZAAR_LIVE=1 is set by hand.
    taker = agent("bazaar-taker", "agent taker", taker_data)
    maker = agent("bazaar-maker", "agent maker", maker_data)
    # The runtime tools for teammates' Claude Code, over MCP: bearer token, rate limits, DRY RUN.
    mcp = mcp_server("bazaar-mcp", mcp_data)
    evals = evals_service()
    sim = simulator()

    return project(
        "heartfelt-warmth",
        resources=[
            phoenix,
            monitor,
            duels,
            taker,
            maker,
            mcp,
            evals,
            sim,
            phoenix_data,
            monitor_data,
            duels_data,
            taker_data,
            maker_data,
            mcp_data,
        ],
    )
