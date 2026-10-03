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
    monkeypatch.setattr(tp, "_our_sim", lambda port: True)
    monkeypatch.setattr(config, "LOCAL_SIM_URL", config.LOCAL_SIM_URL)  # restored after the test
    monkeypatch.setattr(config, "load_settings", lambda: SimpleNamespace(bazaar_url=config.DEFAULT_URL))
    with pytest.raises(SystemExit, match="the target is not the local simulator"):
        tp.run("taker", 8915, tmp_path, [])
    assert not list(tmp_path.iterdir())  # refused before anything was patched or written


def test_a_run_refuses_a_simulator_it_did_not_start(monkeypatch, tmp_path):
    monkeypatch.setenv("BAZAAR_SIM", "local")
    monkeypatch.setattr(tp, "_sim_pid_file", lambda port: tmp_path / "none.pid")
    with pytest.raises(SystemExit, match="no simulator of ours on 8915"):
        tp.run("taker", 8915, tmp_path, [])
    (tmp_path / "none.pid").write_text("999999999")  # a pid that is not running
    with pytest.raises(SystemExit, match="no simulator of ours"):
        tp.run("taker", 8915, tmp_path, [])
    import os

    (tmp_path / "none.pid").write_text(str(os.getpid()))  # a live one passes this check
    assert tp._our_sim(8915)


@pytest.mark.parametrize(
    "url, explicit, local",
    [
        ("postgresql://bazaar:bazaar@localhost:5433/bazaar_sp1", True, True),
        ("postgresql://u:p@127.0.0.1:5432/x", True, True),
        ("postgresql://u:p@/x?host=/tmp", True, True),  # a local socket
        ("postgresql://u:p@localhost:5433/bazaar_sp1", False, False),  # only the DATABASE_URL fallback
        ("postgresql://bazaar:bazaar@localhost:5433/bazaar", True, False),  # the laptop's real-game database
        ("postgresql://u:p@postgres.railway.internal:5432/bazaar_sim", True, False),
        ("postgresql://u:p@localhost/x?host=db.example.com", True, False),  # a query override wins
        ("postgresql://u:p@localhost/x?hostaddr=10.0.0.5", True, False),
    ],
    ids=["loopback", "ip", "socket", "fallback", "real-db", "remote", "host-override", "hostaddr-override"],
)
def test_a_run_profiles_only_a_database_of_its_own_on_this_machine(monkeypatch, url, explicit, local):
    from types import SimpleNamespace

    from pydantic import SecretStr

    if explicit:
        monkeypatch.setenv("BAZAAR_SIM_DATABASE_URL", url)
    else:
        monkeypatch.delenv("BAZAAR_SIM_DATABASE_URL", raising=False)
    assert tp._local_database(SimpleNamespace(database_url=SecretStr(url))) is local


def test_the_simulator_command_refuses_a_busy_port():
    import socket

    with socket.socket() as taken:
        taken.bind(("127.0.0.1", 0))
        taken.listen(1)
        port = taken.getsockname()[1]
        with pytest.raises(SystemExit, match="is busy"):
            tp.serve_sim(port, 15.0)
