"""The operator's cockpit: pure panels from raw reads, and the read-only command against a local simulator."""

from __future__ import annotations

import json
import time
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from bazaar_agent import cli
from bazaar_agent import cockpit as ck
from bazaar_agent import timeline as tl
from bazaar_agent.guardrails import Ledger
from tests.simkit import QUIET, running_sim

US = "t01"
NIGHT = datetime.fromisoformat("2026-10-03T08:55:00+02:00")
PLAYS = json.loads(Path("docs/night/saturday-plays.json").read_text(encoding="utf-8"))
NOWHERE_DB = "postgresql://nobody:nothing@127.0.0.1:9/bazaar_cockpit_test"  # never reachable


def offer(oid: int, *, give_cash: int = 0, want_cash: int = 0, status: str = "open", maker: str = US) -> dict:
    return {
        "id": oid,
        "maker": maker,
        "status": status,
        "give": {"cash": give_cash, "assets": [], "types": []},
        "want": {"cash": want_cash, "assets": [], "types": []},
    }


def reads(**overrides: object) -> ck.Reads:
    """Saturday 08:55, doors closed at game hour 2.65: the fixtures plus a made-up team state (no real values)."""
    clock = tl.frozen(tl.load(tl.CLOCK_FIXTURE), 2.65, NIGHT)
    clock["tick"] = 159
    base = ck.Reads(
        now=NIGHT,
        clock=clock,
        schedule=tl.load(tl.SCHEDULE_FIXTURE),
        dealers={"personas": [{"id": "abuela", "level": 1}, {"id": "chato", "level": 2}]},
        me={"id": US, "cash": 353, "venue": None, "assets": [{"id": 425, "kind": "pack"}], "score": {}},
        offers={"offers": [offer(1, give_cash=20)]},
        threads={
            "threads": [
                {
                    "id": 7,
                    "kind": "persona",
                    "with": "abuela",
                    "status": "open",
                    "topic": {"buy": {"card": "SAL-02"}},
                    "messages": [{"offer": offer(9, give_cash=15)}],
                },
                {
                    "id": 5,
                    "kind": "persona",
                    "with": "abuela",
                    "status": "deal",
                    "messages": [{"offer": offer(4, give_cash=21, status="settled")}],
                },
            ]
        },
        duels={"duels": []},
        health={"taker": {"ok": True, "mode": "live", "target": {"mode": "real"}, "tick": 159}},
        ledger=ck.LedgerView("postgres ledger table", True, spent_last_hour=40, writers={"taker": 158}),
        plays=PLAYS,
    )
    for k, v in overrides.items():
        setattr(base, k, v)
    return base


def panel(panels: list[ck.Panel], title: str) -> ck.Panel:
    return next(p for p in panels if p.title == title)


def value(p: ck.Panel, label: str) -> ck.Line:
    return next(line for line in p.lines if line.label == label)


def test_saturday_morning_screen():
    panels = ck.build(reads(), ck.Limits())
    assert [p.title for p in panels] == [
        "Clock",
        "Next (playbook)",
        "Cash",
        "Ledger",
        "Agents",
        "Caps",
        "Duels",
        "Ladder",
        "Market Test",
        "Alerts",
    ]
    clock = panel(panels, "Clock")
    assert value(clock, "doors").status == "warn" and "2026-10-03T09:00" in value(clock, "next opening").value
    nxt = panel(panels, "Next (playbook)").lines
    assert nxt[0].label == "h4 Saturday opens" and "Sat 09:00 (resume, in 5 min) · jump 09:00" in nxt[0].value
    assert nxt[1].label == "h3 The Market Test" and "Sat 09:21 (resume, in 26 min)" in nxt[1].value
    assert "jump" not in nxt[1].value  # the jump column marks h3 overdue: no jump time shown
    round_2 = next(line for line in nxt if line.label == "h4 Saturday · Gran Vía")
    assert "jump 09:00" in round_2.value and "Round 2 starts" in round_2.value  # the play's first sentence
    market = panel(panels, "Market Test")
    assert "Sat 09:21 (resume, in 26 min)" in value(market, "next Market Test").value


def test_cash_counts_open_bids_and_the_reserve():
    cash = panel(ck.build(reads(), ck.Limits()), "Cash")
    assert value(cash, "cash").value == "353 P · open bids 35 P"  # board bid 20 + dealer-thread bid 15
    assert value(cash, "headroom").value.startswith("48 P") and value(cash, "headroom").status == "ok"
    assert value(cash, "spent, last game hour").value == "40 / 150 P"
    assert value(cash, "sealed packs").status == "warn" and "425" in value(cash, "sealed packs").value
    # PR #71's head: the venue reserve sits on top of the floor until our venue opens
    reserved = panel(ck.build(reads(), ck.Limits(cash_floor=100, venue_bond_reserve=270)), "Cash")
    assert value(reserved, "floor").value == "370 P (cash_floor 100 + venue reserve 270)"
    assert value(reserved, "headroom").status == "bad"
    me = {**reads().me, "venue": "v09"}  # type: ignore[dict-item]
    opened = panel(ck.build(reads(me=me), ck.Limits(cash_floor=100, venue_bond_reserve=270)), "Cash")
    assert value(opened, "floor").value == "100 P (cash_floor 100)"


def test_ledger_shared_or_not():
    shared = panel(ck.build(reads(), ck.Limits()), "Ledger")
    assert (
        value(shared, "where").status == "ok" and value(shared, "writer taker").value == "last row at tick 158 (1 ago)"
    )
    local = ck.LedgerView("file ledger.jsonl", False)
    alone = panel(ck.build(reads(ledger=local), ck.Limits()), "Ledger")
    assert value(alone, "where").status == "bad" and "THIS machine only" in value(alone, "where").value
    quiet = ck.LedgerView("postgres ledger table", True)
    assert value(panel(ck.build(reads(ledger=quiet), ck.Limits()), "Ledger"), "writers").status == "warn"


def test_agents_unreachable_or_behind():
    clock = {**reads().clock, "doors": "open", "tick": 170}  # type: ignore[dict-item]
    health = {"taker": {"ok": True, "mode": "live", "target": {"mode": "real"}, "tick": 160}, "maker": None}
    r = reads(clock=clock, health=health)
    r.errors["health:maker"] = "ConnectError: refused"
    agents = panel(ck.build(r, ck.Limits()), "Agents")
    assert value(agents, "taker").status == "bad" and "(10 behind)" in value(agents, "taker").value
    assert value(agents, "maker").status == "bad" and "refused" in value(agents, "maker").value


def test_caps_and_duels_near_their_limits():
    clock = {**reads().clock, "doors": "open", "tick": 100}  # type: ignore[dict-item]
    threads = {"threads": [{"id": i, "kind": "persona", "with": "abuela", "status": "open"} for i in range(6)]}
    duels = {
        "duels": [
            {"duel": 3, "status": "live", "session": 2, "role": "seller", "item": "X", "deadline_tick": 101},
            {"duel": 4, "status": "live", "session": 2, "role": "buyer", "item": "Y", "deadline_tick": 112,
             "your_offer": {"price": 50}, "rival_offer": {"price": 60}},
            {"duel": 2, "status": "deal", "session": 2, "deadline_tick": 90},
        ]
    }  # fmt: skip
    panels = ck.build(reads(clock=clock, threads=threads, duels=duels), ck.Limits())
    caps = panel(panels, "Caps")
    assert value(caps, "open threads").value == "6 / 6" and value(caps, "open threads").status == "bad"
    duel_panel = panel(panels, "Duels")
    assert value(duel_panel, "live").value.startswith("2 ")
    assert value(duel_panel, "duel 3").status == "warn" and "1 ticks left" in value(duel_panel, "duel 3").value
    assert value(duel_panel, "duel 4").status == "info" and "rival at 60" in value(duel_panel, "duel 4").value


def test_ladder_lists_our_dealer_deals():
    ladder = panel(ck.build(reads(), ck.Limits()), "Ladder")
    assert "1 deal(s)" in value(ladder, "L1 abuela").value and "21 P" in value(ladder, "L1 abuela").value
    assert value(ladder, "L1 abuela").status == "warn" and value(ladder, "L2 chato").status == "warn"


def test_a_failed_source_shows_its_error_and_the_rest_renders():
    r = ck.read_each(
        ck.Reads(now=NIGHT, plays=PLAYS),
        {
            "clock": lambda: reads().clock,
            "schedule": lambda: reads().schedule,
            "me": lambda: (_ for _ in ()).throw(RuntimeError("401 bad key")),
            "alerts": lambda: (_ for _ in ()).throw(OSError("no file")),
        },
    )
    assert r.me is None and r.errors["me"] == "RuntimeError: 401 bad key" and r.alerts == []
    panels = ck.build(r, ck.Limits())
    assert value(panel(panels, "Cash"), "me").status == "bad"
    assert panel(panels, "Next (playbook)").status != "bad"
    text = "\n".join(ck.render(panels, NIGHT))
    assert text.startswith("Team 1 cockpit · Sat 08:55:00 · overall BAD") and "unavailable: RuntimeError" in text
    assert {p["title"] for p in ck.as_dict(panels)} >= {"Clock", "Cash"}


def test_the_file_ledger_view(tmp_path):
    led = Ledger(tmp_path / "ledger.jsonl")
    led.record("spend", 160, 2.70, 25, "SAL-02")
    led.record("spend", 161, 2.71, 20, "sobre_barrio")
    led.record("accept", 161, 2.71, 20, "sobre_barrio")
    led.record("listing", 161, 2.71)
    view = ck.ledger_view(led, 161, 2.72)
    assert (view.shared, view.spent_last_hour, view.packs_last_hour) == (False, 45, 1)
    assert (view.accepts_this_tick, view.listings_this_tick) == (1, 1)


def test_read_ledger_falls_back_to_the_file_without_postgres(tmp_path):
    def refuse() -> object:
        raise ConnectionError("no database here")

    view = ck.read_ledger(tmp_path, 160, 2.7, connect=refuse)
    assert view.where == "file ledger.jsonl" and not view.shared


@pytest.fixture
def sim_session(tmp_path, monkeypatch):
    config = replace(QUIET, tick_seconds=0.5, rivals=2, duel_first_tick=2, duel_ticks=40, duel_every_ticks=400)
    with running_sim(config) as (url, sim):
        monkeypatch.setattr("bazaar_agent.config.SIM_URL", url)
        for name in ("BAZAAR_URL", "BAZAAR_KEY", "BAZAAR_SIM_KEY"):
            monkeypatch.delenv(name, raising=False)
        for name, val in {
            "BAZAAR_SIM": "1",
            "BAZAAR_TEAM_ID": US,
            "BAZAAR_DATA_DIR": str(tmp_path),
            "DATABASE_URL": NOWHERE_DB,
            "BAZAAR_SIM_DATABASE_URL": NOWHERE_DB,
        }.items():
            monkeypatch.setenv(name, val)
        yield url, sim


def test_the_command_against_a_local_simulator_reads_only(sim_session):
    url, sim = sim_session
    from bazaar_sdk import Bazaar

    team = Bazaar(url, "sim-team1")
    thread = team.open_thread("abuela", {"buy": {"pack": "sobre_barrio"}})
    deadline = time.monotonic() + 10
    while not team.duels().get("duels") and time.monotonic() < deadline:
        time.sleep(0.2)
    with sim.world.lock:
        before = (len(sim.world.state.offers), len(sim.world.state.threads))
    args = ["cockpit", "--json", "--health", "taker=http://127.0.0.1:9/health"]
    out = CliRunner().invoke(cli.app, args)
    assert out.exit_code == 0, out.output + repr(out.exception)
    panels = {p["title"]: p for p in json.loads(out.stdout[out.stdout.index("[") :])}
    lines = {p: {x["label"]: x for x in panels[p]["lines"]} for p in panels}
    assert lines["Clock"]["doors"]["value"] == "open"
    assert lines["Caps"]["open threads"]["value"] == "1 / 6"
    assert f"  thread {thread['id']}" in lines["Caps"]
    assert lines["Duels"]["live"]["status"] == "ok"
    assert lines["Ledger"]["where"]["value"] == "file ledger.jsonl (THIS machine only)"
    assert lines["Agents"]["taker"]["status"] == "bad"
    assert lines["Cash"]["cash"]["value"].endswith("open bids 0 P")
    with sim.world.lock:  # the cockpit sent nothing: no new offer, no new thread
        assert (len(sim.world.state.offers), len(sim.world.state.threads)) == before


def test_unreadable_bids_never_show_a_comfortable_headroom():
    r = reads(offers=None)
    r.errors["offers"] = "BazaarError: 429"
    cash = panel(ck.build(r, ck.Limits()), "Cash")
    assert value(cash, "cash").value == "353 P · open bids unknown"
    assert value(cash, "headroom").status == "bad" and "429" in value(cash, "headroom").value


def test_a_bid_in_both_lists_counts_once_and_queued_counts():
    bid = {**offer(9, give_cash=15), "thread": 7}
    queued = offer(11, give_cash=12, status="queued")
    dealer_ask = {**offer(12, want_cash=30, maker="abuela"), "to": US}  # the dealer's offer to us: not our cash
    threads = [{"id": 7, "kind": "persona", "status": "open", "messages": [{"offer": bid}, {"offer": dealer_ask}]}]
    assert ck.committed_cash({"offers": [bid, queued, offer(13, give_cash=5, status="expired")]}, threads, US) == 27
