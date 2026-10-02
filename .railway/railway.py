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
        "questions/**",
        ".railway/**",
    ],
}
ALWAYS = {"restartPolicyType": "ALWAYS"}


def runtime_env() -> dict:
    """What `bazaar monitor` and `bazaar duel run` read (src/bazaar_agent/config.py, telemetry.py)."""
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


def runtime(name: str, command: str, data: object, enabled: bool = True) -> object:
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
        env=runtime_env(),
    )


@define_railway
def main(ctx=None):
    phoenix_data = volume("phoenix-data", region=REGION, sizeMB=VOLUME_MB)
    monitor_data = volume("bazaar-monitor-data", region=REGION, sizeMB=VOLUME_MB)
    duels_data = volume("bazaar-duels-data", region=REGION, sizeMB=VOLUME_MB)

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
    duels = runtime("bazaar-duels", "duel run --play", duels_data)

    return project(
        "heartfelt-warmth",
        resources=[phoenix, monitor, duels, phoenix_data, monitor_data, duels_data],
    )
