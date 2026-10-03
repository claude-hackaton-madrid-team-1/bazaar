""".railway/railway.py rules that a `railway config apply` must keep, checked on the COMPILED graph.

The file is compiled with the real `railway_sdk` (a pure-Python dev dependency, no Railway call), called
as the CLI calls it (`main(ctx)`), so every spelling the SDK accepts (env or variables, start, run or
deploy commands, nested resources, a context-only branch) is checked as Railway would receive it:

1. BAZAAR_LIVE is decided by hand: taker and maker declare it `preserve` and nothing else declares it
   (an undeclared hand-set variable is deleted by the next apply; a declared value would set it).
2. Live is BAZAAR_LIVE only: no start or pre-deploy command carries `--live` or BAZAAR_LIVE, and only
   the duel player `--play`s.
3. Every service builds our repo on main (Phoenix: its pinned image; bazaar-live: the bazaar-live repo on
   main). An OFF service, without a source, is redeployed from its last image by any apply that changes
   its config (bazaar-monitor, 2026-10-02 23:14 UTC).
4. Exactly the services and volumes we run are declared (an allowlist: add a new one here on
   purpose). The monitor runs in the CLI and the evals inside the agents, so neither is declared.
5. The show (bazaar-live) holds no team key: only its runtime settings, the two optional voice keys and
   the read-only show database URL (role bazaar_live_reader, two views) plus its SHOW_DUELS flag, all preserve().
6. The bluff kill switch BAZAAR_BLUFF (N16) is hand-set: preserve() on the services that write words, nowhere else.
"""

from __future__ import annotations

import re
import runpy
from pathlib import Path
from typing import Any

import pytest
import railway_sdk

IAC = Path(__file__).resolve().parents[1] / ".railway" / "railway.py"
REPO = "claude-hackaton-madrid-team-1/bazaar"
SERVICES = frozenset(
    {"phoenix", "bazaar-duels", "bazaar-taker", "bazaar-maker", "bazaar-mcp", "bazaar-sim", "bazaar-live"}
)
LIVE_SHOW = "bazaar-live"
LIVE_SHOW_REPO = "claude-hackaton-madrid-team-1/bazaar-live"
LIVE_SHOW_VARIABLES = {
    "RAILPACK_NODE_VERSION": {"type": "literal", "value": "22.23.3"},
    "PORT": {"type": "literal", "value": "8080"},
    "ELEVENLABS_API_KEY": {"type": "preserve"},
    "GEMINI_API_KEY": {"type": "preserve"},
    "SHOW_DATABASE_URL": {"type": "preserve"},
    "SHOW_DUELS": {"type": "preserve"},
    "TRANSCRIPT_SPEAK_QUOTES": {"type": "preserve"},
    "TRANSCRIPT_STREAMS_PER_ADDRESS": {"type": "preserve"},
    "TTS_DAILY_CHARS": {"type": "preserve"},
}
VOLUMES = frozenset({"phoenix-data", "bazaar-duels-data", "bazaar-taker-data", "bazaar-maker-data", "bazaar-mcp-data"})
LIVE_AGENTS = frozenset({"bazaar-taker", "bazaar-maker"})
LIVE_IN_COMMAND = re.compile(r"--live\b|BAZAAR_LIVE")
PHOENIX_IMAGE = "arizephoenix/phoenix:version-20.19.0"  # the exact pin (docker-compose.yml): never a moving tag
BUILD_COMMAND = "uv sync --locked --no-dev"  # a build runs no game command
LIVE_SHOW_BUILD = "npm run build"  # the show's Vite build: no game command either
# The exact start commands: a wrapper script could add `--live` behind a clean-looking command, and
# live is decided by BAZAAR_LIVE alone. Changing one is a reviewed edit of this map.
START_COMMANDS = {
    "phoenix": None,
    "bazaar-duels": "/app/.venv/bin/bazaar duel run --play",
    "bazaar-taker": "/app/.venv/bin/bazaar agent taker",
    "bazaar-maker": "/app/.venv/bin/bazaar agent maker",
    "bazaar-mcp": "/app/.venv/bin/bazaar mcp serve --host 0.0.0.0",
    "bazaar-sim": "/app/.venv/bin/bazaar-sim serve --host 0.0.0.0",
    "bazaar-live": "node server/index.ts",
}


def compiled(path: Path = IAC) -> list[dict[str, Any]]:
    """The resources Railway would receive for `path`, compiled the way the CLI does: `main(ctx)`."""
    ctx = railway_sdk.create_railway_context(environment="production")
    graph: dict[str, Any] = runpy.run_path(str(path))["main"](ctx).to_graph()
    return list(graph["resources"])


@pytest.fixture(scope="module")
def resources() -> list[dict[str, Any]]:
    return compiled()


@pytest.fixture(scope="module")
def services(resources: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {r["name"]: r for r in resources if r["type"] == "service"}


def test_exactly_the_services_and_volumes_we_run_are_declared(resources: list[dict[str, Any]]) -> None:
    assert {r["type"] for r in resources} <= {"service", "volume"}  # no unreviewed database or bucket
    names = [r["name"] for r in resources]
    assert len(names) == len(set(names)), names  # a second service("bazaar-taker", ...) would win
    assert {r["name"] for r in resources if r["type"] == "service"} == SERVICES
    attached = {a for r in resources for a in (r.get("volumeAttachments") or {})}
    assert {r["name"] for r in resources if r["type"] == "volume"} | attached == VOLUMES


def test_only_the_live_agents_declare_bazaar_live_and_only_as_preserve(services: dict[str, dict[str, Any]]) -> None:
    live = {name: (s.get("variables") or {}).get("BAZAAR_LIVE") for name, s in services.items()}
    assert {n for n, v in live.items() if v == {"type": "preserve"}} == LIVE_AGENTS
    assert all(v is None for n, v in live.items() if n not in LIVE_AGENTS), live


WORDS_WRITERS = frozenset({"bazaar-duels", "bazaar-taker", "bazaar-maker"})  # llm_env(): the words services


def test_the_bluff_kill_switch_is_hand_set_on_every_words_service_and_never_valued_here(
    services: dict[str, dict[str, Any]],
) -> None:
    """BAZAAR_BLUFF=0 turns the tactics off without a deploy (N16): declared preserve() (an undeclared hand-set
    variable is deleted by an apply), never a value in this file, and only where words are written."""
    bluff = {name: (s.get("variables") or {}).get("BAZAAR_BLUFF") for name, s in services.items()}
    assert {n for n, v in bluff.items() if v == {"type": "preserve"}} == WORDS_WRITERS
    assert all(v is None for n, v in bluff.items() if n not in WORDS_WRITERS), bluff


def test_no_command_turns_a_service_live(services: dict[str, dict[str, Any]]) -> None:
    for name, s in services.items():
        deploy = s.get("deploy") or {}
        pre = deploy.get("preDeployCommand") or []
        commands = [deploy.get("startCommand") or "", *(pre if isinstance(pre, list) else [pre])]
        assert not any(LIVE_IN_COMMAND.search(str(c)) for c in commands), (name, commands)
        assert ("--play" in " ".join(map(str, commands))) == (name == "bazaar-duels"), (name, commands)
    starts = {name: (s.get("deploy") or {}).get("startCommand") for name, s in services.items()}
    assert starts == START_COMMANDS
    builds = {name: (s.get("build") or {}).get("buildCommand") for name, s in services.items()}
    expected = {"phoenix": None, LIVE_SHOW: LIVE_SHOW_BUILD}
    assert builds == {name: expected.get(name, BUILD_COMMAND) for name in services}, builds


def test_every_service_builds_our_repo_on_main_or_the_pinned_phoenix(services: dict[str, dict[str, Any]]) -> None:
    for name, s in services.items():
        source = s.get("source") or {}
        if name == "phoenix":
            assert source == {"type": "image", "image": PHOENIX_IMAGE}, source
        else:
            repo = LIVE_SHOW_REPO if name == LIVE_SHOW else REPO
            assert (source.get("type"), source.get("repo"), source.get("branch")) == ("github", repo, "main"), (
                name,
                source,
            )


def test_the_show_holds_no_team_key_and_no_database(services: dict[str, dict[str, Any]]) -> None:
    show = services[LIVE_SHOW]
    # Exactly these variables: no BAZAAR_KEY, no DATABASE_URL, no Phoenix key; the voice keys only preserve().
    assert show.get("variables") == LIVE_SHOW_VARIABLES, show.get("variables")
    assert not show.get("volumeAttachments"), show.get("volumeAttachments")
