""".railway/railway.py rules that a `railway config apply` must keep, checked on the COMPILED graph.

The file is compiled with the real `railway_sdk` (a pure-Python dev dependency, no Railway call), called
as the CLI calls it (`main(ctx)`), so every spelling the SDK accepts (env or variables, start, run or
deploy commands, nested resources, a context-only branch) is checked as Railway would receive it:

1. BAZAAR_LIVE is decided by hand: taker and maker declare it `preserve` and nothing else declares it
   (an undeclared hand-set variable is deleted by the next apply; a declared value would set it).
2. Live is BAZAAR_LIVE only: no start or pre-deploy command carries `--live` or BAZAAR_LIVE, and only
   the duel player `--play`s.
3. Every service builds our repo on main (Phoenix: its pinned image). An OFF service, without a
   source, is redeployed from its last image by any apply that changes its config (bazaar-monitor,
   2026-10-02 23:14 UTC).
4. Exactly the services and volumes we run are declared (an allowlist: add a new one here on
   purpose). The monitor runs in the CLI and the evals inside the agents, so neither is declared.
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
SERVICES = frozenset({"phoenix", "bazaar-duels", "bazaar-taker", "bazaar-maker", "bazaar-mcp", "bazaar-sim"})
VOLUMES = frozenset({"phoenix-data", "bazaar-duels-data", "bazaar-taker-data", "bazaar-maker-data", "bazaar-mcp-data"})
LIVE_AGENTS = frozenset({"bazaar-taker", "bazaar-maker"})
LIVE_IN_COMMAND = re.compile(r"--live\b|BAZAAR_LIVE")
PHOENIX_IMAGE = "arizephoenix/phoenix:"


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
    names = [r["name"] for r in resources]
    assert len(names) == len(set(names)), names  # a second service("bazaar-taker", ...) would win
    assert {r["name"] for r in resources if r["type"] == "service"} == SERVICES
    attached = {a for r in resources for a in (r.get("volumeAttachments") or {})}
    assert {r["name"] for r in resources if r["type"] == "volume"} | attached == VOLUMES


def test_only_the_live_agents_declare_bazaar_live_and_only_as_preserve(services: dict[str, dict[str, Any]]) -> None:
    live = {name: (s.get("variables") or {}).get("BAZAAR_LIVE") for name, s in services.items()}
    assert {n for n, v in live.items() if v == {"type": "preserve"}} == LIVE_AGENTS
    assert all(v is None for n, v in live.items() if n not in LIVE_AGENTS), live


def test_no_command_turns_a_service_live(services: dict[str, dict[str, Any]]) -> None:
    for name, s in services.items():
        deploy = s.get("deploy") or {}
        pre = deploy.get("preDeployCommand") or []
        commands = [deploy.get("startCommand") or "", *(pre if isinstance(pre, list) else [pre])]
        assert not any(LIVE_IN_COMMAND.search(str(c)) for c in commands), (name, commands)
        assert ("--play" in " ".join(map(str, commands))) == (name == "bazaar-duels"), (name, commands)


def test_every_service_builds_our_repo_on_main_or_the_pinned_phoenix(services: dict[str, dict[str, Any]]) -> None:
    for name, s in services.items():
        source = s.get("source") or {}
        if name == "phoenix":
            assert source.get("type") == "image" and str(source.get("image")).startswith(PHOENIX_IMAGE), source
        else:
            assert (source.get("type"), source.get("repo"), source.get("branch")) == ("github", REPO, "main"), (
                name,
                source,
            )
