"""B23: live opportunity alerts in the monitor. One alert per opportunity, a log of each one's life, thresholds as
params, never a write to the game; off by default (the monitor then reads no board at all)."""

from copy import deepcopy
from pathlib import Path

from typer.testing import CliRunner

from bazaar_agent import opp_watch as ow
from bazaar_agent.agents.monitoring import MonitorLoop, Options
from bazaar_agent.cli import app
from bazaar_agent.feed import FeedStore
from bazaar_agent.guardrails import Guardrails
from bazaar_agent.monitor import Watcher
from bazaar_agent.ticks import Clock
from tests.agent_fakes import CHEAP, RASTRO, ask, bid
from tests.test_monitor_loop import Recorder
from tests.test_strategy import CATALOG, ME, PARAMS
from tests.test_telemetry_cli import FakePublic as FeedPublic

P = ow.WatchParams(arb_alert_net=3, dup_alert_surplus=3, opp_alert_surplus=5, log_floor=0, b4=False)


def seen(key="arb:1:2", kind="arb", value=5.0, takeable=""):
    return ow.Seen(key, kind, "LAV-01", value, "buy 5 → sell 16", ("t06", "t17"), takeable)


# ---------------------------------------------------------------- the tracker


def test_one_alert_per_opportunity_and_its_life_is_logged():
    t = ow.Tracker(P)
    alerts, rows = t.update(10, [seen()])
    assert [a.kind for a in alerts] == ["opportunity:arb"] and [r["event"] for r in rows] == ["open"]
    alerts, rows = t.update(11, [seen(value=7.0)])
    assert alerts == [] and rows == []  # deduplicated: still the same opportunity
    alerts, rows = t.update(12, [])
    (closed,) = rows
    assert closed["event"] == "closed" and (closed["first_tick"], closed["last_tick"], closed["scans"]) == (10, 11, 2)
    assert closed["best"] == 7.0 and closed["alerted"] is True


def test_below_the_threshold_it_is_logged_and_alerts_the_first_scan_it_crosses():
    t = ow.Tracker(P)
    alerts, rows = t.update(10, [seen(value=1.0)])
    assert alerts == [] and rows[0]["event"] == "open"  # logged: it gains us something, not yet worth an alert
    alerts, _ = t.update(11, [seen(value=4.0)])
    assert len(alerts) == 1
    alerts, _ = t.update(12, [seen(value=9.0)])
    assert alerts == []


def test_the_log_floor_and_the_thresholds_are_params():
    t = ow.Tracker(ow.WatchParams(arb_alert_net=10, log_floor=2, b4=False))
    alerts, rows = t.update(10, [seen(value=1.0), seen("arb:3:4", value=5.0)])
    assert [r["key"] for r in rows] == ["arb:3:4"] and alerts == []  # 1 < floor 2 is not logged; 5 < 10 no alert
    assert ow.WatchParams().threshold("dup") == 3.0 and ow.WatchParams().threshold("sell") == 5.0


def test_an_alert_says_when_the_taker_would_not_take_it():
    (alert,), _ = ow.Tracker(P).update(10, [seen(takeable="maker unknown")])
    assert "the taker would not: maker unknown" in alert.detail


# ---------------------------------------------------------------- one scan, from boards


class Public(FeedPublic):
    """The monitor's public client plus the board reads the scanner makes (counted)."""

    def __init__(self, boards):
        self.boards, self.reads = boards, []

    def venues(self):
        self.reads.append("venues")
        return {"venues": [RASTRO, CHEAP]}

    def board(self, venue="rastro"):
        self.reads.append(venue)
        return {"offers": deepcopy(self.boards.get(venue, []))}

    def catalog(self):
        self.reads.append("catalog")
        return deepcopy(CATALOG)


BOARDS = {
    "rastro": [ask(1, "LAV-01", 5, maker="t06")],  # we hold LAV-01: buy 5 + fee 2 ...
    "v02": [
        bid(2, "LAV-01", 16, venue="v02", maker="t17"),  # ... sell into 16 on a 0 bps venue: net +9
        ask(3, "LAV-06", 5, venue="v02", asset=903, maker="t08"),  # one more LAV-06 is worth 10: +5
    ],
}


def scanner(**params):
    return ow.Scanner(ow.WatchParams(**{"b4": False, **params}), Guardrails(), PARAMS)


def test_a_scan_finds_the_crossing_and_the_duplicate_with_public_reads_only():
    s, public = scanner(), Public(BOARDS)
    s.refresh(CATALOG, [], "t01", ME)
    found, reads = s.scan(public, ME, 100)
    assert sorted((x.kind, x.ref, x.value) for x in found) == [("arb", "LAV-01", 9.0), ("dup", "LAV-06", 5.0)]
    assert reads == 3 and public.reads == ["venues", "rastro", "v02"]


def test_b4s_scanner_adds_buys_and_sells():
    s, public = scanner(b4=True), Public({"rastro": [ask(4, "LAV-02", 10, asset=904, maker="t05")]})
    me = {**ME, "affinity": {"LAV": 1.6, "LAT": 0.5, "RET": 0.7, "MAL": 0.9, "SAL": 1.1, "CHA": 1.3}}
    s.refresh(CATALOG, [], "t01", me)
    found, _ = s.scan(public, me, 100)
    assert [(x.kind, x.ref) for x in found] == [("buy", "LAV-02")]  # a missing page card below its value to us


# ---------------------------------------------------------------- in the monitor


def make(tmp_path, public, scanner=None):
    said: list[str] = []
    watcher = Watcher(FeedStore(tmp_path / "feed"), "t01")
    team = type("Team", (), {"me": lambda self: deepcopy(ME)})()
    loop = MonitorLoop(public, team, watcher, tmp_path, Options(db_enabled=False), said.append, scanner=scanner)
    return loop, said


def test_off_by_default_the_monitor_reads_no_board(tmp_path, monkeypatch):
    Recorder(monkeypatch)
    public = Public(BOARDS)
    loop, _ = make(tmp_path, public)
    loop.on_tick(Clock(tick=100, next_tick_in=30))
    assert public.reads == [] and not (tmp_path / ow.LOG_FILE).exists()


def test_the_monitor_alerts_once_logs_and_closes(tmp_path, monkeypatch):
    Recorder(monkeypatch)
    public = Public(deepcopy(BOARDS))
    loop, said = make(tmp_path, public, scanner())
    loop.on_tick(Clock(tick=100, next_tick_in=30))
    loop.on_tick(Clock(tick=101, next_tick_in=30))
    public.boards["v02"] = []  # the bid and the duplicate ask are gone
    loop.on_tick(Clock(tick=102, next_tick_in=30))
    alerts = [line for line in said if "ALERT" in line and "opportunity" in line]
    assert len(alerts) == 2  # the crossing and the duplicate, once each over three ticks
    rows = ow.read_rows(tmp_path / ow.LOG_FILE)
    closed = {r["kind"]: r for r in rows if r["event"] == "closed"}
    assert (closed["arb"]["first_tick"], closed["arb"]["last_tick"], closed["arb"]["scans"]) == (100, 101, 2)
    assert set(closed) == {"arb", "dup"}


def test_a_failed_scan_never_stops_the_monitor(tmp_path, monkeypatch):
    Recorder(monkeypatch)

    class Broken(Public):
        def venues(self):
            raise RuntimeError("boom")

    loop, said = make(tmp_path, Broken({}), scanner())
    loop.on_tick(Clock(tick=100, next_tick_in=30))
    assert any("opportunity scan failed" in line for line in said) and "tick 100" in said[-1]


def test_every_n_ticks(tmp_path, monkeypatch):
    Recorder(monkeypatch)
    public = Public(BOARDS)
    loop, _ = make(tmp_path, public, scanner(every_ticks=3))
    for t in range(100, 104):
        loop.on_tick(Clock(tick=t, next_tick_in=30))
    assert public.reads.count("venues") == 2  # ticks 1 and 4 of the loop


# ---------------------------------------------------------------- the summary for the switch decision


def test_summary_and_the_watch_log_command(tmp_path: Path):
    t = ow.Tracker(P)
    rows = []
    for tick, found in (
        (10, [seen(), seen("dup:9", "dup", 4.0)]),
        (11, [seen(value=6.0)]),
        (12, [seen("arb:5:6", value=3.0, takeable="maker unknown")]),
        (13, []),
    ):
        rows += t.update(tick, found)[1]
    arb, dup = ow.summarise(rows)
    assert (arb.kind, arb.seen, arb.alerted, arb.takeable, arb.lasted_2, arb.max_ticks) == ("arb", 2, 2, 1, 1, 2)
    assert arb.total_best == 6.0 and (dup.seen, dup.lasted_2) == (1, 0)
    path = tmp_path / "opportunities.jsonl"
    ow.append_rows(path, rows)
    out = CliRunner().invoke(app, ["arb", "watch-log", str(path)])
    assert out.exit_code == 0 and "| arb | 2 | 2 | 1 | 1 |" in out.stdout


def test_against_the_simulator_over_http(tmp_path, monkeypatch):
    """The scanner's public reads against the in-process simulator: a crossing a rival set up is alerted."""
    from bazaar_agent.sdk import Bazaar, PublicBazaar
    from bazaar_agent.strategy import load_strategy
    from bazaar_sim import broker, market
    from tests.simkit import QUIET, running_sim

    Recorder(monkeypatch)
    with running_sim(QUIET, run_clock=False) as (url, sim):
        w = sim.world
        w.team("t04").unlocked.append("chato")
        vid = broker.open_venue(w, "t04", {"name": "Zero", "fee_bps": 0, "rules": {"mechanism": "board"}})["venue"]
        card = next(a for a in w.holdings("t02") if a.kind == "card")
        market.offer_from_input(w, "t02", {"venue": "rastro", "give": {"assets": [card.id]}, "want": {"cash": 6}})
        market.offer_from_input(w, "t03", {"venue": vid, "give": {"cash": 16}, "want": {"cards": [card.ref]}})
        public, team = PublicBazaar(url), Bazaar(url, "sim-team1", wait_on_tick=False, retries=0)
        s = ow.Scanner(ow.WatchParams(b4=False), Guardrails(), load_strategy().params)
        said: list[str] = []
        loop = MonitorLoop(public, team, Watcher(FeedStore(tmp_path / "feed"), "t01"), tmp_path,
                           Options(db_enabled=False), said.append, scanner=s)  # fmt: skip
        loop.on_tick(Clock.model_validate(team.clock()))
    assert any("opportunity:arb" in line and card.ref in line for line in said)
    assert sim.world.asset(card.id).owner == "t02"  # read-only: nothing was bought
