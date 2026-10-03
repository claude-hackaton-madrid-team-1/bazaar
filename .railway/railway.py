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


# No BAZAAR_URL and no BAZAAR_SIM anywhere in this file: every service plays the real game
# (https://bazaar.causaprima.ai with BAZAAR_KEY), the target src/bazaar_agent/config.py picks when the
# flag is unset. Only bazaar-sim below IS the simulator; nothing here points a client at it.
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
        # set by hand per service (README "Live services": duels 0, taker 2.5, maker 5, mcp 7.5); kept by every apply
        "BAZAAR_TICK_OFFSET_S": preserve(),
        # jev (unset) or llm: who answers every judge() verdict (src/bazaar_agent/jev/decider.py), and its
        # model and budget per service (README "Decider switch"). Set by hand; preserve() so an apply keeps them.
        "BAZAAR_DECIDER": preserve(),
        "BAZAAR_DECIDER_MODEL": preserve(),
        "BAZAAR_DECIDER_TIMEOUT_S": preserve(),
        "BAZAAR_DECIDER_MAX_CALLS": preserve(),
        "BAZAAR_DECIDER_MAX_CONCURRENT": preserve(),
        "BAZAAR_DECIDER_CACHE_S": preserve(),
        "BAZAAR_DECIDER_WINDOW_S": preserve(),
    }


def llm_env() -> dict:
    """The runtime LLM on the Claude subscription (README "LLM on the Claude subscription"), for the
    services that write negotiation words. The `claude-agent-sdk` wheel bundles the Claude Code CLI,
    so the build needs nothing more. The value is the operator's `claude setup-token` token, set by
    hand with `railway variable set ... --stdin`; unset, every LLM path falls back to its template.

    BAZAAR_BLUFF=0 (set by hand) turns every bluff tactic off on that service without a code deploy (N16);
    unset or 1, tactics are on. preserve() keeps a hand-set value across applies (an undeclared one is deleted)."""
    return {"CLAUDE_CODE_OAUTH_TOKEN": preserve(), "BAZAAR_BLUFF": preserve()}


def runtime(name: str, command: str, data: object, llm: bool = False) -> object:
    """A worker on main (`bazaar <command>`). There is no OFF variant: Railway has no 0 replicas, and an off
    service (no source) is still redeployed from its last image by any apply that changes its config, so a
    service we do not run is not declared at all (tests/test_railway_iac.py)."""
    return service(
        name,
        source=github(REPO, branch=BRANCH),
        build=BUILD,
        start=f"/app/.venv/bin/bazaar {command}",
        deploy=ALWAYS,
        replicas={REGION: 1},
        volumeMounts={APP_DATA: data},
        env={**runtime_env(), **(llm_env() if llm else {})},
    )


def agent(name: str, command: str, data: object, env: dict[str, object] | None = None) -> object:
    """An autonomous agent (`bazaar agent taker|maker`) and its public read-only status on AGENT_PORT.

    Live or dry run is decided by hand, never here: this file never sets BAZAAR_LIVE (README "Autonomous
    agents"). It is declared `preserve()` so an apply keeps whatever was set by hand: undeclared, `railway
    config plan` proposed to delete it (2026-10-03), which would have put a live agent back in dry run. The
    taker and the maker are LIVE since Sat 2026-10-03 01:45 Madrid."""
    return service(
        name,
        source=github(REPO, branch=BRANCH),
        build=BUILD,
        start=f"/app/.venv/bin/bazaar {command}",
        deploy=ALWAYS,
        replicas={REGION: 1},
        healthcheck="/health",
        volumeMounts={APP_DATA: data},
        # BAZAAR_LEARN=0 / BAZAAR_LLM_READ=0 (set by hand) turn the feed reader / its LLM pass off, and
        # BAZAAR_TEAM_THREADS=0 the team desk (N17): all set by hand like BAZAAR_LIVE, declared preserve() so an
        # apply keeps them (an undeclared hand-set variable is deleted by `railway config apply`).
        # `env` (one service's own hand-set switches) goes first: it can never override the fixed keys below.
        env={
            **(env or {}),
            **runtime_env(),
            **llm_env(),
            "PORT": AGENT_PORT,
            "BAZAAR_LIVE": preserve(),
            "BAZAAR_LEARN": preserve(),
            "BAZAAR_LLM_READ": preserve(),
            "BAZAAR_TEAM_THREADS": preserve(),
        },
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
            "SIM_CLIENT_IP_HEADER": "x-real-ip",  # set by Railway's edge; a client-sent X-Forwarded-For is ignored
            "SIM_ADMIN_TOKEN": preserve(),
        },
    )


LIVE_REPO = "claude-hackaton-madrid-team-1/bazaar-live"
LIVE_PORT = "8080"
LIVE_NODE = "22.23.3"  # node runs server/*.ts by stripping types (>= 22.18); same pin as .nvmrc there


def live_show() -> object:
    """Bazaar Live (repo bazaar-live): the buyer and the seller at a Rastro stall, a static React show
    plus a tiny TTS proxy in one Node process (`node server/index.ts`: dist/, GET /health, POST /api/tts).
    The browser reads only the agents' public /health, /state and WS /events; it sends nothing to them.

    The voice keys and the show's read-only database URL are set once by hand with
    `railway variable set ... --stdin` and declared preserve() so an apply keeps them (an undeclared
    hand-set variable is deleted by an apply); with neither key
    the show speaks with the browser's own voice. Any other override (model, voices, TTS_* limits; see
    the bazaar-live README) must be declared here before it is set. Its public domain is generated once
    with `railway domain --service bazaar-live --port 8080`: Railway IaC does not declare generated domains."""
    return service(
        "bazaar-live",
        source=github(LIVE_REPO, branch=BRANCH),
        build={"buildCommand": "npm run build"},
        start="node server/index.ts",
        deploy=ALWAYS,
        replicas={REGION: 1},
        healthcheck="/health",
        env={
            "RAILPACK_NODE_VERSION": LIVE_NODE,
            "PORT": LIVE_PORT,
            "ELEVENLABS_API_KEY": preserve(),
            "GEMINI_API_KEY": preserve(),
            # LIVE-T1 (bazaar-live #5): the read-only role bazaar_live_reader on the private
            # postgres.railway.internal host (two views in schema show, no table grants), set by hand
            # with --stdin; SHOW_DUELS stays unset (off) until the last duel session is over.
            "SHOW_DATABASE_URL": preserve(),
            "SHOW_DUELS": preserve(),
            "TRANSCRIPT_SPEAK_QUOTES": preserve(),  # opt-in: voice dealer quotes (captions only by default)
            "TRANSCRIPT_STREAMS_PER_ADDRESS": preserve(),  # SSE streams per address (default 24)
            "TTS_DAILY_CHARS": preserve(),  # daily ElevenLabs budget (chars ~ credits): guards the 10k weekend credits
            "ELEVENLABS_VOICE_SELLER": preserve(),  # the seller's voice id, set by hand
            # Game screens (bazaar-live #9): the relay reads the real game with the team key and serves
            # private state (cash, cards, album) only to a page that carries ?token=GAME_VIEW_TOKEN.
            # Both are set by hand with --stdin; undeclared, an apply would delete them and open the page.
            "BAZAAR_KEY": preserve(),
            "GAME_VIEW_TOKEN": preserve(),
        },
    )


@define_railway
def main(ctx=None):
    phoenix_data = volume("phoenix-data", region=REGION, sizeMB=VOLUME_MB)
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
    # bazaar-monitor: leaves this file 2026-10-03 (Omar deletes the service and its volume by hand; apply
    # nothing until a re-plan shows 0 destroy). The monitor runs in the CLI on a laptop
    # (one per team). An off service still redeploys its last image whenever an apply changes its config,
    # source or not (Fri 23:14 UTC), so it is not declared at all. To run it on Railway again, re-add
    # `volume("bazaar-monitor-data", ...)` and `runtime("bazaar-monitor", "monitor", <that volume>)`.
    duels = runtime("bazaar-duels", "duel run --play", duels_data, llm=True)
    # The autonomous agents share ONE accept per tick with bazaar-duels through the Postgres ledger
    # (duels first; the maker never accepts). Both are LIVE since Sat 2026-10-03 01:45 Madrid: BAZAAR_LIVE=1
    # was set by hand on each service, and agent() preserve()s it (delete the variable to go back to dry run).
    taker = agent("bazaar-taker", "agent taker", taker_data)
    # BAZAAR_BENCH_POLICY=edge (set by hand; unset or exact: today's matching) has our venue's broker match the
    # Market Test with the bench edge (agents/bench_edge.py), behind BAZAAR_BENCH_GUARD_MARGIN (default 10; none =
    # unguarded); edge runs only with BAZAAR_BENCH_EDGE_CONFIRM=yes too. All declared preserve() so an apply keeps
    # the hand-set values.
    bench_env = {
        "BAZAAR_BENCH_POLICY": preserve(),
        "BAZAAR_BENCH_GUARD_MARGIN": preserve(),
        "BAZAAR_BENCH_EDGE_CONFIRM": preserve(),
    }
    maker = agent("bazaar-maker", "agent maker", maker_data, bench_env)
    # The runtime tools for teammates' Claude Code, over MCP: bearer token, rate limits, DRY RUN.
    mcp = mcp_server("bazaar-mcp", mcp_data)
    sim = simulator()
    live = live_show()

    return project(
        "heartfelt-warmth",
        resources=[
            phoenix,
            duels,
            taker,
            maker,
            mcp,
            sim,
            live,
            phoenix_data,
            duels_data,
            taker_data,
            maker_data,
            mcp_data,
        ],
    )
