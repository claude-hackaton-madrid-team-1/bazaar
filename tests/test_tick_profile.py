"""scripts/tick_profile.py: the report's arithmetic, and the guard that keeps it on a local simulator."""

import importlib.util
import json
import sys

import pytest

from bazaar_agent.config import REPO_ROOT

_spec = importlib.util.spec_from_file_location("tick_profile", REPO_ROOT / "scripts" / "tick_profile.py")
assert _spec is not None and _spec.loader is not None
tp = importlib.util.module_from_spec(_spec)
sys.modules["tick_profile"] = tp
_spec.loader.exec_module(tp)


def write(path, rows):
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))


def tick(n, wall, budget=12.6, next_in=14.0, expired=0, **stages):
    return {
        "agent": "taker",
        "tick": n,
        "tick_seconds": 15.0,
        "next_tick_in": next_in,
        "budget_s": budget,
        "wall_s": wall,
        "expired": expired,
        "stages": stages,
    }


def request(t, keyed=True, in_tick=True, status="ok", path="/api/me"):
    return {"t": t, "keyed": keyed, "method": "GET", "path": path, "s": 0.1, "status": status, "in_tick": in_tick}


def test_the_report_counts_walls_budgets_drops_and_the_keys_busiest_second(tmp_path):
    write(
        tmp_path / "taker.ticks.jsonl",
        [
            tick(1, 1.0, jev=[0.3]),
            tick(2, 2.0, jev=[0.3, 0.4]),
            tick(3, 13.0, expired=2),  # over the budget, not past the next tick
            tick(4, 15.0, next_in=14.0),  # late
        ],
    )
    write(
        tmp_path / "taker.requests.jsonl",
        [request(100.0), request(100.2), request(100.9), request(101.5), request(100.1, keyed=False)]
        + [request(103.0, status="rate_limited"), request(104.0, in_tick=False)],
    )
    write(tmp_path / "duels.ticks.jsonl", [tick(1, 0.5)])
    write(tmp_path / "duels.requests.jsonl", [request(100.5)])
    summary = tp.report(tmp_path)
    taker = summary["agents"]["taker"]
    assert (taker["ticks"], taker["wall_max"], taker["over_budget"], taker["late"]) == (4, 15.0, 2, 1)
    assert taker["expired"] == 2 and taker["rate_limited"] == 1
    assert taker["keyed_per_tick"] == 1.25 and taker["keyed_bg"] == 1 and taker["public_per_tick"] == 0.25
    assert taker["stages"]["jev"]["calls"] == 3 and taker["stages"]["jev"]["per_tick_p95"] == 0.7
    assert summary["key"]["max_in_1s"] == 4  # 100.0, 100.2, 100.5 (duels) and 100.9: every agent shares the key
    assert summary["key"]["requests"] == 7  # public reads never count against the key


def test_a_run_refuses_any_target_but_a_local_simulator(monkeypatch, tmp_path):
    monkeypatch.delenv("BAZAAR_SIM", raising=False)
    with pytest.raises(SystemExit, match="only against a local simulator"):
        tp.run("taker", 8915, tmp_path, [])
    monkeypatch.setenv("BAZAAR_SIM", "local")
    with pytest.raises(SystemExit, match="agent must be one of"):
        tp.run("broker", 8915, tmp_path, [])


def test_a_run_refuses_when_the_settings_target_is_not_the_local_simulator(monkeypatch, tmp_path):
    from types import SimpleNamespace

    from bazaar_agent import config

    monkeypatch.setenv("BAZAAR_SIM", "local")
    monkeypatch.setattr(config, "LOCAL_SIM_URL", config.LOCAL_SIM_URL)  # restored after the test
    monkeypatch.setattr(config, "load_settings", lambda: SimpleNamespace(bazaar_url=config.DEFAULT_URL))
    with pytest.raises(SystemExit, match="the target is not the local simulator"):
        tp.run("taker", 8915, tmp_path, [])
    assert not list(tmp_path.iterdir())  # refused before anything was patched or written


def test_a_run_refuses_a_database_that_is_not_on_this_machine():
    from types import SimpleNamespace

    from pydantic import SecretStr

    def settings(url):
        return SimpleNamespace(database_url=SecretStr(url))

    assert tp._local_database(settings("postgresql://bazaar:bazaar@localhost:5433/bazaar_sp1"))
    assert tp._local_database(settings("postgresql://u:p@127.0.0.1:5432/x"))
    assert not tp._local_database(settings("postgresql://u:p@postgres.railway.internal:5432/bazaar_sim"))


def test_the_simulator_command_refuses_a_busy_port():
    import socket

    with socket.socket() as taken:
        taken.bind(("127.0.0.1", 0))
        taken.listen(1)
        port = taken.getsockname()[1]
        with pytest.raises(SystemExit, match="is busy"):
            tp.serve_sim(port, 15.0)
