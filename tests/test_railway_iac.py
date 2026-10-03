""".railway/railway.py rules that a `railway config apply` must keep, checked on the EVALUATED file.

The file runs against a stub `railway_sdk` (no Railway, no network) and the test reads what each
service would be declared with, so a renamed key, a `dict()` override or a helper outside `main()`
cannot slip past a syntax check:

1. BAZAAR_LIVE is decided by hand: taker and maker declare it `preserve()` and nothing else declares it
   (an undeclared hand-set variable is deleted by the next apply; a declared value would set it).
2. Every service has a source: Railway redeploys an OFF service's last image whenever an apply changes
   its config (bazaar-monitor, 2026-10-02 23:14 UTC).
3. Exactly the services and volumes we run are declared (an allowlist: a new one is added here on
   purpose): the monitor runs in the CLI and the evals inside the agents, so neither is declared.
4. Live is BAZAAR_LIVE only: no start command carries `--live`, and only the duel player `--play`s.
"""

from __future__ import annotations

import runpy
import sys
import types
from pathlib import Path
from typing import Any

import pytest

IAC = Path(__file__).resolve().parents[1] / ".railway" / "railway.py"
SERVICES = frozenset({"phoenix", "bazaar-duels", "bazaar-taker", "bazaar-maker", "bazaar-mcp", "bazaar-sim"})
VOLUMES = frozenset({"phoenix-data", "bazaar-duels-data", "bazaar-taker-data", "bazaar-maker-data", "bazaar-mcp-data"})
LIVE_AGENTS = frozenset({"bazaar-taker", "bazaar-maker"})
PRESERVE = object()


def _flat(items: Any) -> Any:
    for item in items:
        if isinstance(item, list | tuple) and item and not isinstance(item[0], str):
            yield from _flat(item)
        else:
            yield item


def declared(monkeypatch: pytest.MonkeyPatch, path: Path | None = None) -> tuple[dict[str, dict[str, Any]], set[str]]:
    """(services by name with their keyword arguments, volume names) the file's `main()` declares."""
    services: dict[str, dict[str, Any]] = {}
    sdk = types.ModuleType("railway_sdk")

    def service(name: str, **kw: Any) -> tuple[str, str]:
        services[name] = kw
        return ("service", name)

    sdk.preserve = lambda: PRESERVE  # type: ignore[attr-defined]
    sdk.github = lambda repo, branch=None: ("github", repo, branch)  # type: ignore[attr-defined]
    sdk.image = lambda ref: ("image", ref)  # type: ignore[attr-defined]
    sdk.service = service  # type: ignore[attr-defined]
    sdk.volume = lambda name, **kw: ("volume", name)  # type: ignore[attr-defined]
    sdk.project = lambda name, resources: list(_flat(resources))  # type: ignore[attr-defined]  # the SDK flattens
    sdk.define_railway = lambda fn: fn  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "railway_sdk", sdk)
    resources = runpy.run_path(str(path or IAC))["main"]()
    kept = {name for kind, name in resources if kind == "service"}
    return {n: kw for n, kw in services.items() if n in kept}, {n for kind, n in resources if kind == "volume"}


def _variables(kw: dict[str, Any]) -> dict[str, Any]:
    """`env` and `variables`: the SDK merges both into the service's variables."""
    return {**(kw.get("variables") or {}), **(kw.get("env") or {})}


def test_only_the_live_agents_declare_bazaar_live_and_only_as_preserve(monkeypatch: pytest.MonkeyPatch) -> None:
    services, _ = declared(monkeypatch)
    live = {name: _variables(kw).get("BAZAAR_LIVE", "absent") for name, kw in services.items()}
    assert {n for n, v in live.items() if v is PRESERVE} == LIVE_AGENTS
    assert all(v == "absent" for n, v in live.items() if n not in LIVE_AGENTS), live


def _starts(kw: dict[str, Any]) -> str:
    deploy = kw.get("deploy") if isinstance(kw.get("deploy"), dict) else {}
    return " ".join(str(c) for c in (kw.get("start"), kw.get("startCommand"), deploy.get("startCommand")) if c)


def test_no_start_command_turns_an_agent_live(monkeypatch: pytest.MonkeyPatch) -> None:
    services, _ = declared(monkeypatch)
    starts = {n: _starts(kw).split() for n, kw in services.items()}
    assert not [n for n, words in starts.items() if "--live" in words], starts  # live is BAZAAR_LIVE only
    assert [n for n, words in starts.items() if "--play" in words] == ["bazaar-duels"], starts


def test_every_service_has_a_source(monkeypatch: pytest.MonkeyPatch) -> None:
    services, _ = declared(monkeypatch)
    kinds = {n: kw.get("source") for n, kw in services.items()}
    sourceless = sorted(n for n, src in kinds.items() if not (isinstance(src, tuple) and src[0] in ("github", "image")))
    assert services and sourceless == [], sourceless  # None or {} would be an OFF service
    branches = {src[2] for src in kinds.values() if src[0] == "github"}
    assert branches == {"main"}, branches


def test_exactly_the_services_we_run_are_declared(monkeypatch: pytest.MonkeyPatch) -> None:
    services, volumes = declared(monkeypatch)
    assert (set(services), volumes) == (SERVICES, VOLUMES)  # no bazaar-monitor, no bazaar-evals
