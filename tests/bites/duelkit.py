"""Shared kit for the duel bite tests: a live-shaped duel, worst-case valuation, and a fake duel server.

Version-agnostic on purpose: only `duelist.duel_move`, `cli` and `guardrails` are used, so the same
files run on origin/main, origin/fix/duel-offers-inside-limit (#60) and origin/night/w2b-duel-v2 (#86).
No network, no database (`DATABASE_URL` is removed and every Postgres connect raises), no simulator.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from typer.testing import CliRunner

# The real GET /api/duels shape (tests/fixtures/evals/duels_done.json, practice session 2026-10-02).
BASE: dict[str, Any] = {
    "duel": 501,
    "session": 3,
    "status": "live",
    "role": "seller",
    "item": "Taxi Blanco",
    "issues": ["price"],
    "your_days_weight": None,
    "days_meaning": None,
    "your_limit": 100,
    "limit_meaning": "never sell below your cost",
    "rival": "Rival Azul",
    "deadline_tick": 136,
    "decay_per_round": 0.08,
    "rounds": 0,
    "your_offer": None,
    "rival_offer": None,
    "messages": [],
    "result": None,
    "price": None,
    "days": None,
}


def duel(**kw: Any) -> dict[str, Any]:
    d = deepcopy(BASE)
    d.update(kw)
    return d


def worst_case(d: dict[str, Any], price: float, days: Any) -> float | None:
    """A price with its days at |your_days_weight| per day against us (the RULES' unverified sign: worst case).

    Days are valued whenever the payload carries a numeric weight, whatever `issues` says."""
    weight = d.get("your_days_weight")
    if days in (None, 0):
        return float(price)
    if not isinstance(weight, int | float):
        return None  # days we cannot value
    penalty = abs(weight) * days
    return price - penalty if d["role"] == "seller" else price + penalty


def strictly_inside(value: float, limit: float, role: str) -> bool:
    return value > limit if role == "seller" else value < limit


# ---------------------------------------------------------------- a fake /api/duels + /api/clock


class FakeDuelServer:
    """The team client `duel run` uses. Ticks come from `ticks` (one per clock read, the last one repeats).

    A duel is live while tick < deadline_tick and nobody accepted. An accept at tick t settles if
    t <= last_settle(duel) (default deadline - 1, as the simulator settles it on the next tick).
    `rival(d, tick)` may update the rival's standing offer each tick (default: static)."""

    def __init__(self, duels, ticks, *, rival=None, last_settle=None, accepts_per_tick=1):
        self.state = {d["duel"]: deepcopy(d) for d in duels}
        self.ticks, self.i = list(ticks), 0
        self.tick = self.ticks[0]
        self.rival = rival
        self.last_settle = last_settle or (lambda d: d["deadline_tick"] - 1)
        self.accepts_per_tick = accepts_per_tick
        self.accepted: dict[int, int] = {}  # duel -> tick we accepted
        self.said: list[tuple[int, int, Any, Any]] = []  # (tick, duel, price, days)
        self.accept_log: list[tuple[int, int]] = []  # (tick, duel)

    def clock(self):
        self.tick = self.ticks[min(self.i, len(self.ticks) - 1)]
        self.i += 1
        return {
            "tick": self.tick,
            "next_tick_in": 25.0,
            "tick_seconds": 30.0,
            "t_hours": self.tick / 120,
            "doors": "open",
            "paused": False,
            "limits": {"accepts_per_team_per_tick": self.accepts_per_tick, "messages_per_side_per_tick": 1},
        }

    def _live(self):
        out = []
        for did, d in sorted(self.state.items()):
            if did in self.accepted or self.tick >= d["deadline_tick"]:
                continue
            if self.rival is not None:
                self.rival(d, self.tick)
            out.append(deepcopy(d))
        return out

    def duels(self, done=False):
        return {"duels": [] if done else self._live()}

    def duel_accept(self, did):
        self.accept_log.append((self.tick, did))
        self.accepted[did] = self.tick
        return {"ok": True, "duel": did, "settles_tick": self.tick + 1}

    def duel_say(self, did, text, price=None, days=None):
        self.said.append((self.tick, did, price, days))
        d = self.state[did]
        d["messages"].append({"tick": self.tick, "from": "you", "text": text, "price": price, "days": days})
        if price is not None:
            d["your_offer"] = {"id": 9000 + len(self.said), "price": price, "tick": self.tick, "days": days or 0}
            d["rounds"] = d.get("rounds", 0) + 1
        return {"ok": True}

    def deals(self) -> list[int]:
        return sorted(did for did, t in self.accepted.items() if t <= self.last_settle(self.state[did]))


def patch_cli(monkeypatch, tmp_path, server, rules=None, words=None):
    """`bazaar duel run` against `server`: no network, no Postgres, no sleeping between ticks."""
    import psycopg

    from bazaar_agent import cli, db
    from bazaar_agent.config import Settings

    def down(*args, **kwargs):
        raise psycopg.OperationalError("no database in bite tests")

    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(
        cli, "load_settings", lambda: Settings(data_dir=tmp_path, bazaar_url="http://127.0.0.1:9")
    )  # never the real game
    monkeypatch.setattr(cli, "team_client", lambda settings: server)
    monkeypatch.setattr(db, "connect", down)
    monkeypatch.setattr(db, "connect_ready", down)
    from bazaar_agent.ticks import run_per_tick as real_run

    def no_sleep(clock, on_tick, **kw):
        return real_run(clock, on_tick, **{**kw, "sleep": lambda s: None})

    monkeypatch.setattr(cli, "run_per_tick", no_sleep)
    if rules is not None:
        from types import SimpleNamespace

        monkeypatch.setattr(cli, "_rules", lambda: SimpleNamespace(rules=rules))
    if words is not None:
        monkeypatch.setattr(cli.llm_cli, "words_for", lambda settings, rules, fallback: words)
    return cli


def run_duels(cli, ticks: int, *extra: str) -> str:
    result = CliRunner().invoke(cli.app, ["duel", "run", "--play", "--no-jev", "--max-ticks", str(ticks), *extra])
    assert result.exit_code == 0, result.output + repr(result.exception)
    return result.output
