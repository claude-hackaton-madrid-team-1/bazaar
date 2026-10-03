"""The broker loop with `bench_policy = "edge"` and extra bench reads per tick. In-process fakes, no network."""

import json

from pydantic import SecretStr
from typer.testing import CliRunner

from bazaar_agent import cli
from bazaar_agent import venue as vn
from bazaar_agent.agents.broker import BrokerAgent, BrokerConfig
from bazaar_agent.config import Settings
from bazaar_agent.decisions import DecisionLog
from bazaar_agent.guardrails import Guardrails, parse_guardrails
from tests.agent_fakes import FakePublic, clock, rows
from tests.test_broker_agent import ClockedBroker, FakeBroker, FakeTeam
from tests.test_matcher import bench_buy, bench_sell, book_buy, book_sell


def agent(tmp_path, broker, *, live=False, sleeps=None, events=None, **config):
    return BrokerAgent(
        broker,
        FakeTeam(),
        us="t01",
        rules=Guardrails(allow_venue_open=True, pause_file=str(tmp_path / "PAUSE")),
        decisions=DecisionLog(tmp_path),
        live=live,
        log=lambda line: None,
        events=events,
        stats_dir=tmp_path / "agents",
        config=BrokerConfig(**config),
        sleep=(sleeps.append if sleeps is not None else (lambda s: None)),
    )


class ArrivingBroker(FakeBroker):
    """A bench whose second read in a tick shows a new pair (traders arrive, or a refusal frees one)."""

    def __init__(self, later, **kw):
        super().__init__(**kw)
        self.later, self.reads = list(later), 0

    def book(self):
        self.reads += 1
        if self.reads == 2:
            self.bench += self.later
        return super().book()


def test_the_edge_policy_matches_the_bench_first_then_the_public_book(tmp_path):
    broker = FakeBroker(
        offers=[book_sell(1, "LAV-03", 20, "mA"), book_buy(2, "LAV-03", 30, "mB")],
        bench=[bench_sell("b5-0", 30), bench_buy("b5-1", 40), bench_sell("b5-2", 45)],
    )
    agent(tmp_path, broker, live=True, bench_policy="edge").on_tick(clock())
    assert broker.sent == [("b5-0", "b5-1", 35), (1, 2, 25)]
    reasons = [d["reason"] for d in rows(tmp_path) if "reason" in d]
    assert reasons[0].startswith("bench edge") and reasons[1].startswith("maximum-surplus matching (exact)")


def test_the_edge_respects_the_tick_cap_bench_first(tmp_path):
    bench = [bench_sell(f"b5-{i}", 30) for i in range(0, 6, 2)] + [bench_buy(f"b5-{i}", 40) for i in range(1, 7, 2)]
    broker = FakeBroker(offers=[book_sell(1, "LAV-03", 20, "mA"), book_buy(2, "LAV-03", 30, "mB")], bench=bench)
    agent(tmp_path, broker, live=True, bench_policy="edge", max_matches_per_tick=2).on_tick(clock())
    assert len(broker.sent) == 2 and all(isinstance(s, str) for s, _, _ in broker.sent)


def test_a_second_read_in_the_tick_matches_a_pair_that_appeared(tmp_path):
    sleeps = []
    broker = ArrivingBroker(
        [bench_sell("b5-2", 30), bench_buy("b5-3", 50)], bench=[bench_sell("b5-0", 30), bench_buy("b5-1", 40)]
    )
    a = agent(tmp_path, broker, live=True, sleeps=sleeps, bench_policy="edge", bench_reads_per_tick=2)
    a.on_tick(clock())
    assert broker.sent == [("b5-0", "b5-1", 35), ("b5-2", "b5-3", 40)]
    assert broker.reads == 2 and len(sleeps) == 1 and 0 < sleeps[0] < 40
    assert a.history[-1].sent == 2


def test_one_read_a_tick_stays_todays_behaviour(tmp_path):
    broker = ArrivingBroker(
        [bench_sell("b5-2", 30), bench_buy("b5-3", 50)], bench=[bench_sell("b5-0", 30), bench_buy("b5-1", 40)]
    )
    agent(tmp_path, broker, live=True).on_tick(clock())
    assert broker.reads == 1 and broker.sent == [("b5-0", "b5-1", 35)]


def test_extra_reads_only_happen_while_a_bench_run_is_in_the_book(tmp_path):
    broker = ArrivingBroker([], offers=[book_sell(1, "LAV-03", 20, "mA"), book_buy(2, "LAV-03", 30, "mB")])
    agent(tmp_path, broker, live=True, bench_policy="edge", bench_reads_per_tick=3).on_tick(clock())
    assert broker.reads == 1


def test_a_dry_run_rereads_without_proposing_the_same_pair_twice(tmp_path):
    broker = FakeBroker(bench=[bench_sell("b5-0", 30), bench_buy("b5-1", 40)])
    agent(tmp_path, broker, bench_policy="edge", bench_reads_per_tick=3).on_tick(clock())
    assert broker.sent == [] and [d["move"]["sell"] for d in rows(tmp_path)] == ["b5-0"]


def test_no_extra_read_once_the_tick_window_is_closed(tmp_path):
    broker = ArrivingBroker([bench_sell("b5-2", 30), bench_buy("b5-3", 50)], bench=[bench_sell("b5-0", 30)])
    agent(tmp_path, broker, live=True, bench_policy="edge", bench_reads_per_tick=2).on_tick(clock(next_tick_in=0.5))
    assert broker.reads == 0 and broker.sent == []  # #71: a closed window skips even the first read


def test_a_refused_limit_probe_is_remembered_by_the_edge(tmp_path):
    broker = FakeBroker(bench=[bench_sell("b5-0", 62), bench_buy("b5-1", 58)], refuse={"b5-0"})
    a = agent(tmp_path, broker, live=True, bench_policy="edge", bench_cross="limit")
    a.on_tick(clock())
    ((sell, buy, price),) = broker.sent
    assert (sell, buy) == ("b5-0", "b5-1") and not 58 >= price >= 62
    assert a.edge.refused == {("b5-0", "b5-1"): [price]} and a.edge.probes.refused == 1
    (decision,) = [d for d in rows(tmp_path) if "reason" in d]
    assert decision["reason"].startswith("limit probe")
    stats = a.history[-1]
    assert (stats.probes, stats.proposed_surplus, stats.refused) == (1, 0, 1)


def test_quote_mode_never_sends_a_non_crossing_bench_pair(tmp_path):
    broker = FakeBroker(bench=[bench_sell("b5-0", 62), bench_buy("b5-1", 58)])
    agent(tmp_path, broker, live=True, bench_policy="edge").on_tick(clock())
    assert broker.sent == []


def test_the_bench_started_event_gives_the_session_length(tmp_path):
    events = [{"id": 1, "type": "bench.started", "tick": 100, "payload": {"run": 5, "ticks": 16}}]
    a = agent(tmp_path, FakeBroker(bench=[bench_sell("b5-0", 30)]), events=lambda: events, bench_policy="edge")
    a.on_tick(clock())
    assert a.sessions.open["b5"].ticks == 16 and a.edge.first_tick["b5"] == 100


def _cli(tmp_path, monkeypatch, *args):
    monkeypatch.setattr("bazaar_agent.agents.broker.time.sleep", lambda s: None)
    broker = ClockedBroker(bench=[bench_sell("b5-0", 30), bench_buy("b5-1", 40)])
    settings = Settings(data_dir=tmp_path, team_id="t01", broker_key=SecretStr("simbk-test-only-key"))
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(cli, "public_client", lambda s: FakePublic())
    monkeypatch.setattr(vn, "broker_client", lambda s: broker)
    monkeypatch.setattr(cli, "_db_connect", lambda app: None)
    monkeypatch.setattr(cli, "_rules", lambda: parse_guardrails("- `allow_venue_open` = false — build only"))
    return broker, CliRunner().invoke(cli.app, ["broker", "run", "--max-ticks", "1", *args])


def test_cli_broker_run_takes_the_edge_options_and_stays_a_dry_run(tmp_path, monkeypatch):
    broker, result = _cli(
        tmp_path, monkeypatch, "--bench-policy", "edge", "--bench-reads", "2", "--bench-preset", "hard"
    )
    assert result.exit_code == 0, result.output
    assert "bench edge (hard, accepts by quote, 2 read(s)/tick)" in " ".join(result.output.split())
    assert broker.sent == [] and [d["move"]["sell"] for d in rows(tmp_path)] == ["b5-0"]


def test_cli_broker_run_refuses_an_unknown_bench_policy(tmp_path, monkeypatch):
    broker, result = _cli(tmp_path, monkeypatch, "--bench-policy", "magic")
    assert result.exit_code == 1 and "--bench-policy must be one of exact, edge" in result.output


def test_the_shape_of_a_bench_offer_is_logged_once_per_run(tmp_path):
    offer = {**bench_sell("b5-0", 30), "expires_tick": 120}
    broker = FakeBroker(bench=[offer, bench_buy("b5-1", 40)])
    a = agent(tmp_path, broker, bench_policy="edge")
    a.on_tick(clock())
    a.on_tick(clock(tick=101))
    shapes = (tmp_path / "agents" / "broker_bench_shapes.jsonl").read_text().splitlines()
    assert len(shapes) == 1 and '"expires_tick"' in shapes[0]
    assert a.edge.models["b5-0"].expires == 119  # one tick early (`EdgeConfig.expiry_margin`)


def test_a_rate_limited_probe_teaches_nothing_and_is_not_retried_in_the_tick(tmp_path):
    from bazaar_agent.sdk import BazaarError

    class RateLimited(FakeBroker):
        def match(self, sell, buy, price):
            self.sent.append((sell, buy, price))
            raise BazaarError("rate_limited", "slow down", 429)

    broker = RateLimited(bench=[bench_sell("b5-0", 62), bench_buy("b5-1", 58)])
    a = agent(tmp_path, broker, live=True, bench_policy="edge", bench_cross="limit", bench_reads_per_tick=3)
    a.on_tick(clock())
    assert len(broker.sent) == 1 and a.edge.probes.sent == 0 and a.edge.refused == {}


def test_a_probe_refused_on_its_price_is_repriced_by_a_later_read(tmp_path):
    broker = FakeBroker(bench=[bench_sell("b5-0", 62), bench_buy("b5-1", 58)], refuse={"b5-0"})
    a = agent(tmp_path, broker, live=True, bench_policy="edge", bench_cross="limit", bench_reads_per_tick=2)
    a.on_tick(clock())
    assert len(broker.sent) == 2 and broker.sent[0][2] != broker.sent[1][2]


def test_the_shape_log_takes_every_offer_and_survives_odd_shapes(tmp_path):
    odd = {"id": "b5-1", "give": [{"cash": 40}], "want": {"cash": 0}, "ticks_left": 3}
    a = agent(tmp_path, FakeBroker(bench=[bench_sell("b5-0", 30), odd]), bench_policy="edge")
    a.on_tick(clock())
    (shape,) = [json.loads(x) for x in (tmp_path / "agents" / "broker_bench_shapes.jsonl").read_text().splitlines()]
    assert "ticks_left" in shape["offer"] and "<list>" in shape["give"] and "assets" in shape["give"]


def test_a_closed_session_is_forgotten_by_the_edge(tmp_path):
    broker = FakeBroker(bench=[bench_sell("b5-0", 30)])
    a = agent(tmp_path, broker, bench_policy="edge")
    a.on_tick(clock())
    broker.bench = []
    a.on_tick(clock(tick=101))
    assert a.edge.models == {}


def _probe(tmp_path, monkeypatch, *args, allow=False, refuse=()):
    broker = ClockedBroker(bench=[bench_sell("b5-0", 62), bench_buy("b5-1", 58)], refuse=refuse)
    settings = Settings(data_dir=tmp_path, team_id="t01", broker_key=SecretStr("simbk-test-only-key"))
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(vn, "broker_client", lambda s: broker)
    monkeypatch.setattr(cli, "_db_connect", lambda app: None)
    rule = "true" if allow else "false"
    monkeypatch.setattr(cli, "_rules", lambda: parse_guardrails(f"- `allow_venue_open` = {rule} — test"))
    return broker, CliRunner().invoke(cli.app, ["broker", "probe", *args])


def test_cli_broker_probe_is_a_dry_run_by_default(tmp_path, monkeypatch):
    broker, result = _probe(tmp_path, monkeypatch, "b5-0", "b5-1", "60", allow=True)
    assert result.exit_code == 0, result.output
    assert "DRY RUN" in result.output and broker.sent == []
    (decision,) = [d for d in rows(tmp_path) if "reason" in d]
    assert decision["move"] == {"sell": "b5-0", "buy": "b5-1", "price": 60} and decision["inputs"]["probe"]


def test_cli_broker_probe_live_stays_build_only(tmp_path, monkeypatch):
    broker, result = _probe(tmp_path, monkeypatch, "b5-0", "b5-1", "60", "--live")
    assert result.exit_code == 1 and "allow_venue_open = false" in " ".join(result.output.split())
    assert broker.sent == []


def test_cli_broker_probe_live_prints_the_venues_verdict(tmp_path, monkeypatch):
    broker, result = _probe(tmp_path, monkeypatch, "b5-0", "b5-1", "60", "--live", allow=True, refuse={"b5-0"})
    assert broker.sent == [("b5-0", "b5-1", 60)]
    assert "REFUSED invalid (HTTP 400)" in result.output
    broker, result = _probe(tmp_path, monkeypatch, "7", "8", "60", "--live", allow=True)
    assert broker.sent == [(7, 8, 60)] and "ACCEPTED" in result.output


def test_cli_broker_probe_never_sends_on_bazaar_live_alone(tmp_path, monkeypatch):
    monkeypatch.setenv("BAZAAR_LIVE", "1")
    broker, result = _probe(tmp_path, monkeypatch, "b5-0", "b5-1", "60", allow=True)
    assert result.exit_code == 0, result.output
    assert broker.sent == [] and "DRY RUN" in result.output


def test_the_keepers_broker_takes_its_bench_options_from_the_environment():

    from bazaar_agent.agents.broker import bench_config_from_env

    base = BrokerConfig(pace_s=0.2)
    assert bench_config_from_env(base, {}) is base
    edge = bench_config_from_env(base, {"BAZAAR_BENCH_POLICY": "edge", "BAZAAR_BENCH_CROSS": "limit"})
    assert (edge.bench_policy, edge.bench_cross, edge.pace_s, edge.bench_reads_per_tick) == ("edge", "limit", 0.2, 1)
    lines = []
    assert bench_config_from_env(base, {"BAZAAR_BENCH_POLICY": "Edge", "BAZAAR_BENCH_CROSS": "LIMIT"}) == edge
    kept = bench_config_from_env(base, {"BAZAAR_BENCH_POLICY": "magic", "BAZAAR_BENCH_CROSS": "limit"}, lines.append)
    assert (kept.bench_policy, kept.bench_cross) == ("exact", "limit")  # a typo never stops the maker
    assert lines and "IGNORED BAZAAR_BENCH_POLICY='magic'" in lines[0]
