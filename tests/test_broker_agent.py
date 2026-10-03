"""The broker loop with in-process fakes: dry run sends nothing, build only refuses live matches, the kill
switch stops them, ours are never matched, and the per-tick / per-session telemetry adds up. No network."""

import json
from copy import deepcopy

from pydantic import SecretStr
from typer.testing import CliRunner

from bazaar_agent import cli
from bazaar_agent import venue as vn
from bazaar_agent.agents.broker import BrokerAgent, BrokerConfig, bench_run
from bazaar_agent.config import Settings
from bazaar_agent.decisions import DecisionLog
from bazaar_agent.guardrails import Guardrails, parse_guardrails
from bazaar_agent.sdk import BazaarError
from tests.agent_fakes import FakePublic, clock, rows
from tests.test_matcher import bench_buy, bench_sell, book_buy, book_sell


class FakeBroker:
    """GET /api/broker/book and POST /api/broker/matches, like the simulator: a match takes both offers."""

    def __init__(self, offers=(), bench=(), fee_bps=0, fee_per_card=0, refuse=()):
        self.offers, self.bench = list(offers), list(bench)
        self.fee_bps, self.fee_per_card = fee_bps, fee_per_card
        self.refuse = set(refuse)  # sell ids the server refuses (taken since the read)
        self.sent: list[tuple] = []

    def book(self):
        return deepcopy(
            {
                "offers": self.offers,
                "bench_offers": self.bench,
                "fee_bps": self.fee_bps,
                "fee_per_card": self.fee_per_card,
            }
        )

    def match(self, sell, buy, price):
        self.sent.append((sell, buy, price))
        if sell in self.refuse:
            raise BazaarError("invalid", "sell is not an open offer", 400)
        self.offers = [o for o in self.offers if o["id"] not in (sell, buy)]
        self.bench = [o for o in self.bench if o["id"] not in (sell, buy)]
        return {"ok": True, "sell": sell, "buy": buy, "price": price}


class FakeTeam:
    def __init__(self, offers=(), fail=False):
        self.offers, self.fail = list(offers), fail

    def my_offers(self):
        if self.fail:
            raise BazaarError("unavailable", "down", 503)
        return {"offers": deepcopy(self.offers)}


def agent(tmp_path, broker, team=None, *, live=False, events=None, lines=None, **rules):
    rules.setdefault("pause_file", str(tmp_path / "PAUSE"))
    log = lines.append if lines is not None else (lambda line: None)
    return BrokerAgent(
        broker,
        team if team is not None else FakeTeam(),
        us="t01",
        rules=Guardrails(**rules),
        decisions=DecisionLog(tmp_path),
        live=live,
        log=log,
        events=events,
        stats_dir=tmp_path / "agents",
        config=BrokerConfig(),
    )


def crossing_book():
    return FakeBroker(
        offers=[
            book_sell(1, "LAV-03", 20, "mA"),
            book_buy(2, "LAV-03", 30, "mB"),
            book_sell(3, "LAV-04", 10, "mC"),
            book_buy(4, "LAV-04", 14, "mA"),
        ],
        bench=[bench_sell("b5-0", 30), bench_buy("b5-1", 40)],
    )


def test_dry_run_sends_nothing_and_logs_would_matches(tmp_path):
    broker, lines = crossing_book(), []
    agent(tmp_path, broker, lines=lines, allow_venue_open=True).on_tick(clock())
    assert broker.sent == []
    decisions = rows(tmp_path)
    assert [d["move"] for d in decisions] == [
        {"sell": "b5-0", "buy": "b5-1", "price": 35},
        {"sell": 1, "buy": 2, "price": 25},
        {"sell": 3, "buy": 4, "price": 12},
    ]
    assert all(d["dry_run"] and d["chosen"] and d["kind"] == "broker_match" for d in decisions)
    assert rows(tmp_path, "executions.jsonl") == []
    assert any("WOULD match bench" in line for line in lines)


def test_allow_venue_open_false_refuses_every_match_even_live(tmp_path):
    broker = crossing_book()
    agent(tmp_path, broker, live=True).on_tick(clock())  # allow_venue_open defaults to false
    assert broker.sent == []
    decisions = rows(tmp_path)
    assert len(decisions) == 3 and all(d["status"] == "rejected" and not d["chosen"] for d in decisions)
    assert all("allow_venue_open = false" in d["guardrail"] for d in decisions)


def test_the_kill_switch_and_the_pause_file_block_broker_matches(tmp_path, monkeypatch):
    from bazaar_agent import guardrails as gr

    # The kill switch is read live (#68): GUARDRAILS.md as it is now wins over the rules loaded at start.
    live_file = tmp_path / "GUARDRAILS.md"
    text = gr.GUARDRAILS_FILE.read_text(encoding="utf-8")
    live_file.write_text(text.replace("- `trading_enabled` = true", "- `trading_enabled` = false"), encoding="utf-8")
    monkeypatch.setattr(gr, "GUARDRAILS_FILE", live_file)
    broker = crossing_book()
    a = agent(tmp_path, broker, live=True, allow_venue_open=True)  # started with trading enabled
    a.on_tick(clock())
    live_file.write_text(text, encoding="utf-8")
    (tmp_path / "PAUSE").touch()
    a.on_tick(clock(tick=101))
    assert broker.sent == []
    guardrails = [d["guardrail"] for d in rows(tmp_path)]
    assert len(guardrails) == 6
    assert all("trading_enabled = false" in g for g in guardrails[:3])
    assert all("pause file" in g for g in guardrails[3:])


def test_live_and_allowed_sends_each_match_once_and_records_the_execution(tmp_path):
    broker = crossing_book()
    a = agent(tmp_path, broker, live=True, allow_venue_open=True)
    a.on_tick(clock())
    assert broker.sent == [("b5-0", "b5-1", 35), (1, 2, 25), (3, 4, 12)]
    assert [e["sdk_method"] for e in rows(tmp_path, "executions.jsonl")] == ["broker_match"] * 3
    a.on_tick(clock(tick=101))  # the book is empty now: nothing is matched twice
    assert len(broker.sent) == 3


def test_our_own_offers_are_never_matched(tmp_path):
    broker = FakeBroker(
        offers=[
            book_sell(1, "LAV-03", 20, "mUs"),
            book_buy(2, "LAV-03", 40, "mThem"),
            book_buy(3, "LAV-03", 35, "mUs"),  # same pseudonym as our offer 1
            book_sell(4, "LAV-03", 25, "mOther"),
        ]
    )
    team = FakeTeam(offers=[{"id": 1, "maker": "t01", "status": "open"}])
    agent(tmp_path, broker, team, live=True, allow_venue_open=True).on_tick(clock())
    assert broker.sent == [(4, 2, 32)]


def test_an_offer_addressed_to_us_is_not_ours(tmp_path):
    broker = FakeBroker(offers=[book_sell(9, "LAV-03", 20, "mThem"), book_buy(2, "LAV-03", 40, "mOther")])
    team = FakeTeam(offers=[{"id": 9, "maker": "t05", "to": "t01", "status": "open"}])
    agent(tmp_path, broker, team, live=True, allow_venue_open=True).on_tick(clock())
    assert broker.sent == [(9, 2, 30)]


def test_when_our_offers_cannot_be_read_only_the_bench_is_matched(tmp_path):
    broker, lines = crossing_book(), []
    agent(tmp_path, broker, FakeTeam(fail=True), live=True, lines=lines, allow_venue_open=True).on_tick(clock())
    assert broker.sent == [("b5-0", "b5-1", 35)]
    assert any("bench only this tick" in line for line in lines)


def test_a_refused_match_is_logged_and_the_rest_still_go(tmp_path):
    broker = crossing_book()
    broker.refuse = {1}
    a = agent(tmp_path, broker, live=True, allow_venue_open=True)
    a.on_tick(clock())
    assert len(broker.sent) == 3
    stats = a.history[-1]
    assert (stats.sent, stats.refused) == (2, 1)
    assert [e["error_code"] for e in rows(tmp_path, "executions.jsonl")] == [None, "invalid", None]


def test_a_closed_tick_window_drops_matches_instead_of_sending_late(tmp_path):
    broker = crossing_book()
    agent(tmp_path, broker, live=True, allow_venue_open=True).on_tick(clock(next_tick_in=0.5))
    assert broker.sent == []
    assert {d["status"] for d in rows(tmp_path)} == {"expired"}


def test_per_tick_telemetry_counts_distinct_pairs_and_surplus(tmp_path):
    broker = FakeBroker(
        offers=[
            book_sell(1, "LAV-03", 20, "mA"),
            book_buy(2, "LAV-03", 30, "mB"),
            book_sell(3, "LAV-04", 10, "mA"),
            book_buy(4, "LAV-04", 14, "mB"),  # the same pair of makers again: still one distinct pair
            book_sell(5, "LAV-05", 10, "mC"),
            book_buy(6, "LAV-05", 11, "mA"),
        ],
        bench=[bench_sell("b5-0", 30), bench_buy("b5-1", 40)],
    )
    a = agent(tmp_path, broker, live=True, allow_venue_open=True)
    a.on_tick(clock())
    s = a.history[-1]
    assert (s.proposed, s.sent, s.distinct_pairs, s.pairs_so_far) == (4, 4, 2, 2)
    assert (s.surplus_public, s.surplus_bench, s.proposed_surplus) == (15, 10, 25)
    written = [json.loads(x) for x in (tmp_path / "agents" / "broker_ticks.jsonl").read_text().splitlines()]
    assert written[-1]["distinct_pairs"] == 2


def test_a_bench_session_closes_when_its_offers_leave_the_book(tmp_path):
    broker, lines = FakeBroker(bench=[bench_sell("b7-0", 30), bench_buy("b7-1", 40), bench_sell("b7-2", 50)]), []
    a = agent(tmp_path, broker, live=True, lines=lines, allow_venue_open=True)
    a.on_tick(clock(tick=100))  # matches b7-0 × b7-1; b7-2 is left open
    assert set(a.sessions.open) == {"b7"}
    broker.bench = []  # the session is over
    a.on_tick(clock(tick=101))
    assert a.sessions.open == {} and [(s.run, s.pairs, s.surplus) for s in a.sessions.closed] == [("b7", 1, 10)]
    session = json.loads((tmp_path / "agents" / "broker_sessions.jsonl").read_text())
    assert session == {
        "run": "b7",
        "first_tick": 100,
        "last_tick": 101,
        "pairs": 1,
        "surplus": 10,
        "refused": 0,
        "live": True,
    }
    assert any("Market Test b7 over" in line for line in lines)


def test_bench_sessions_follow_the_feed_events_when_there_are_any(tmp_path):
    events = [
        {"id": 1, "tick": 99, "type": "bench.started", "payload": {"run": 8, "ticks": 4}},
    ]
    broker = FakeBroker(bench=[bench_sell("b8-0", 30), bench_buy("b8-1", 40)])
    a = agent(tmp_path, broker, events=lambda: deepcopy(events), allow_venue_open=True)
    a.on_tick(clock(tick=100))
    assert a.sessions.open["b8"].first_tick == 99
    a.on_tick(clock(tick=101))  # a dry run sees the same pair again: counted once per session
    assert a.sessions.open["b8"].pairs == 1
    events.append({"id": 2, "tick": 102, "type": "bench.finished", "payload": {"run": 8, "possible": 40}})
    a.on_tick(clock(tick=102))
    assert [(s.run, s.first_tick, s.last_tick, s.pairs) for s in a.sessions.closed] == [("b8", 99, 102, 1)]


def test_a_refused_book_read_matches_nothing(tmp_path):
    class Down(FakeBroker):
        def book(self):
            raise BazaarError("bad_key", "unknown broker key", 401)

    broker, lines = Down(), []
    agent(tmp_path, broker, lines=lines, allow_venue_open=True).on_tick(clock())
    assert broker.sent == [] and any("book refused bad_key" in line for line in lines)


def test_a_pause_file_touched_mid_tick_stops_the_next_match(tmp_path):
    class PausingBroker(FakeBroker):
        def match(self, sell, buy, price):
            (tmp_path / "PAUSE").touch()  # someone pauses every agent right after our first send
            return super().match(sell, buy, price)

    broker = PausingBroker(offers=crossing_book().offers, bench=crossing_book().bench)
    agent(tmp_path, broker, live=True, allow_venue_open=True).on_tick(clock())
    assert broker.sent == [("b5-0", "b5-1", 35)]
    assert [d["status"] for d in rows(tmp_path)] == ["approved", "done", "rejected", "rejected"]


def test_a_match_whose_logging_eats_the_tick_is_dropped_not_sent_late(tmp_path):
    t = [0.0]

    class SlowLog(DecisionLog):
        def decide(self, d):
            t[0] = 1_000.0  # writing the decision took the rest of the tick
            return super().decide(d)

    broker = crossing_book()
    a = BrokerAgent(
        broker,
        FakeTeam(),
        us="t01",
        rules=Guardrails(allow_venue_open=True, pause_file=str(tmp_path / "PAUSE")),
        decisions=SlowLog(tmp_path),
        live=True,
        log=lambda line: None,
        now=lambda: t[0],
    )
    a.on_tick(clock())
    assert broker.sent == []
    assert a.history[-1].expired == 3
    assert [d["status"] for d in rows(tmp_path) if d.get("update")] == ["expired"]


def test_a_session_closed_by_its_event_never_reopens_from_a_late_book(tmp_path):
    events = [{"id": 1, "tick": 99, "type": "bench.finished", "payload": {"run": 8}}]
    broker = FakeBroker(bench=[bench_sell("b8-0", 30), bench_buy("b8-1", 40)])  # read just before it ended
    a = agent(tmp_path, broker, events=lambda: deepcopy(events), allow_venue_open=True)
    a.on_tick(clock(tick=100))
    a.on_tick(clock(tick=101))
    assert a.sessions.open == {} and not (tmp_path / "agents" / "broker_sessions.jsonl").exists()


def test_bench_run_ids_normalise():
    assert bench_run(12) == bench_run("12") == bench_run("b12") == "b12"


class ClockedBroker(FakeBroker):
    def clock(self):
        return clock().model_dump()


def test_cli_broker_run_live_stays_build_only_and_sends_nothing(tmp_path, monkeypatch):
    broker = ClockedBroker(offers=crossing_book().offers, bench=crossing_book().bench)
    settings = Settings(data_dir=tmp_path, team_id="t01", broker_key=SecretStr("simbk-test-only-key"))
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(cli, "public_client", lambda s: FakePublic())
    monkeypatch.setattr(vn, "broker_client", lambda s: broker)
    monkeypatch.setattr(cli, "_db_connect", lambda app: None)
    monkeypatch.setattr(cli, "_rules", lambda: parse_guardrails("- `allow_venue_open` = false — build only"))
    result = CliRunner().invoke(cli.app, ["broker", "run", "--live", "--max-ticks", "1"])
    assert result.exit_code == 0, result.output
    assert "allow_venue_open = false" in " ".join(result.output.split())
    assert broker.sent == [] and "simbk-test-only-key" not in result.output
    assert {d["status"] for d in rows(tmp_path)} == {"rejected"}
