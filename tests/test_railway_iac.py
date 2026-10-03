""".railway/railway.py rules that a `railway config apply` must keep, checked on the EVALUATED file.

The file runs against a stub `railway_sdk` (no Railway, no network) and the test reads what each
service would be declared with, so a renamed key, a `dict()` override or a helper outside `main()`
cannot slip past a syntax check:

1. BAZAAR_LIVE is decided by hand: taker and maker declare it `preserve()` and nothing else declares it
   (an undeclared hand-set variable is deleted by the next apply; a declared value would set it).
2. Every service has a source: Railway redeploys an OFF service's last image whenever an apply changes
   its config (bazaar-monitor, 2026-10-02 23:14 UTC).
3. A service we do not run is not declared: the monitor runs in the CLI and the evals inside the agents.
"""

from __future__ import annotations

import runpy
import sys
import types
from pathlib import Path
from typing import Any

import pytest

IAC = Path(__file__).resolve().parents[1] / ".railway" / "railway.py"
NOT_ON_RAILWAY = frozenset({"bazaar-monitor", "bazaar-monitor-data", "bazaar-evals"})
LIVE_AGENTS = frozenset({"bazaar-taker", "bazaar-maker"})
PRESERVE = object()


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
    sdk.project = lambda name, resources: resources  # type: ignore[attr-defined]
    sdk.define_railway = lambda fn: fn  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "railway_sdk", sdk)
    resources = runpy.run_path(str(path or IAC))["main"]()
    kept = {name for kind, name in resources if kind == "service"}
    return {n: kw for n, kw in services.items() if n in kept}, {n for kind, n in resources if kind == "volume"}


def test_only_the_live_agents_declare_bazaar_live_and_only_as_preserve(monkeypatch: pytest.MonkeyPatch) -> None:
    services, _ = declared(monkeypatch)
    live = {name: kw.get("env", {}).get("BAZAAR_LIVE", "absent") for name, kw in services.items()}
    assert {n for n, v in live.items() if v is PRESERVE} == LIVE_AGENTS
    assert all(v == "absent" for n, v in live.items() if n not in LIVE_AGENTS), live


def test_every_service_has_a_source(monkeypatch: pytest.MonkeyPatch) -> None:
    services, _ = declared(monkeypatch)
    sourceless = sorted(n for n, kw in services.items() if kw.get("source") is None)
    assert services and sourceless == [], sourceless


def test_the_monitor_and_the_evals_are_not_declared(monkeypatch: pytest.MonkeyPatch) -> None:
    services, volumes = declared(monkeypatch)
    assert (set(services) | volumes).isdisjoint(NOT_ON_RAILWAY)
