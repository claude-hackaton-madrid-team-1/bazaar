""".railway/railway.py rules that a `railway config apply` must keep, read with ast (no railway_sdk needed).

1. BAZAAR_LIVE is decided by hand: the agents preserve() it and the file never sets a value.
2. No service is declared OFF: Railway redeploys a service's last image whenever an apply changes its
   config, source or not (bazaar-monitor, 2026-10-02 23:14 UTC). A service we do not run is not declared:
   the monitor runs in the CLI and the evals inside the agents (removed 2026-10-03).
"""

from __future__ import annotations

import ast
from pathlib import Path

IAC = ast.parse((Path(__file__).resolve().parents[1] / ".railway" / "railway.py").read_text(encoding="utf-8"))
NOT_ON_RAILWAY = ("bazaar-monitor", "bazaar-monitor-data", "bazaar-evals")


def _function(name: str) -> ast.FunctionDef:
    found = [n for n in IAC.body if isinstance(n, ast.FunctionDef) and n.name == name]
    assert found, f"{name}() is not defined in .railway/railway.py"
    return found[0]


def _is_preserve(node: ast.expr) -> bool:
    return isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "preserve"


def _values(tree: ast.AST, key: str) -> list[ast.expr]:
    """Every value given to `key` in a dict literal under `tree`."""
    return [
        v
        for d in ast.walk(tree)
        if isinstance(d, ast.Dict)
        for k, v in zip(d.keys, d.values, strict=True)
        if isinstance(k, ast.Constant) and k.value == key
    ]


def test_the_agents_preserve_bazaar_live_and_the_file_never_sets_it() -> None:
    in_agent = _values(_function("agent"), "BAZAAR_LIVE")
    assert in_agent and all(_is_preserve(v) for v in in_agent)
    assert all(_is_preserve(v) for v in _values(IAC, "BAZAAR_LIVE"))  # no "BAZAAR_LIVE": "1" anywhere


def test_no_service_is_declared_off() -> None:
    off = [
        n
        for n in ast.walk(_function("main"))
        if isinstance(n, ast.Call)
        and any(k.arg == "enabled" and isinstance(k.value, ast.Constant) and k.value.value is False for k in n.keywords)
    ]
    assert off == [], "an OFF service gets redeployed by any apply that changes its config: do not declare it"


def test_the_monitor_and_the_evals_are_not_railway_services() -> None:
    names = {
        n.args[0].value
        for n in ast.walk(_function("main"))
        if isinstance(n, ast.Call)
        and n.args
        and isinstance(n.args[0], ast.Constant)
        and isinstance(n.args[0].value, str)
    }
    assert names.isdisjoint(NOT_ON_RAILWAY), names & set(NOT_ON_RAILWAY)
