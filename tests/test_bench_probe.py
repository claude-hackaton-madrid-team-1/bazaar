"""BAZAAR_BENCH_POLICY=probe: the exact plan goes out first and unchanged, then a few bench pairs whose quotes do
not cross, priced between the quotes. Refused (the server checks quotes): the broker realises exactly what the exact
broker realises. Accepted (it checks hidden limits): gains the stall cannot take. No network."""

from __future__ import annotations

from typing import Any

import pytest

from bazaar_agent.agents.bench_probe import BenchProbe, ProbeConfig, probe_price
from bazaar_agent.agents.broker import BrokerConfig, bench_config_from_env, bench_text
from bazaar_agent.agents.matcher import BrokerBook, Fee, plan_matches, quotes_from
from bazaar_agent.sdk import BazaarError
from bazaar_sim.bench import HARD, NORMAL, simulate
from tests.agent_fakes import clock, rows
from tests.test_broker_agent import FakeBroker, agent
from tests.test_matcher import bench_buy, bench_sell
from tests.test_venue_keeper import Team, keeper

PROBE = "bench probe: quotes do not cross, price between them (accepted only if limits are checked)"
EXACT = "maximum-surplus matching (exact), midpoint price"


def quotes(bench):
    return quotes_from(BrokerBook.model_validate({"bench_offers": bench})).quotes


def plan(bench, probe=None, fee=None, room=15):
    fee = fee or Fee()
    qs = quotes(bench)
    exact = plan_matches(qs, fee, room)
    probes = (probe or BenchProbe()).plan(qs, exact, fee, room - len(exact))
    return exact, probes


def ids(matches):
    return [(m.sell.id, m.buy.id, m.price) for m in matches]


# ---------------------------------------------------------------- the planner


def test_the_probe_price_sits_between_the_quotes_and_leaves_room_for_the_fee():
    assert probe_price(71, 66, Fee()) == 68
    assert probe_price(60, 50, Fee()) == 55
    price = probe_price(60, 50, Fee(per_card=2))
    assert price == 54 and price >= 50 and price + 2 <= 60  # [54, 56] centred in [50, 60]


def test_probes_take_only_the_traders_the_exact_plan_leaves_out_smallest_gap_first():
    bench = [
        bench_sell("b7-0", 30),
        bench_buy("b7-1", 90),  # crosses b7-0 (and b7-3 does too, for less): the exact plan's pair
        bench_sell("b7-2", 71),
        bench_buy("b7-3", 66),  # gap 5
        bench_sell("b7-4", 95),
        bench_buy("b7-5", 60),  # gap 35 against b7-4: past the default max gap
    ]
    exact, probes = plan(bench)
    assert ids(exact) == [("b7-0", "b7-1", 60)]
    assert ids(probes) == [("b7-2", "b7-3", 68)]


def test_probes_never_cross_runs_and_respect_the_caps():
    bench = [bench_sell(f"b7-{k}", 60 + k) for k in range(6)] + [bench_buy(f"b7-{k}", 55 - k) for k in range(6, 12)]
    bench += [bench_sell("b8-0", 50), bench_buy("b9-0", 49)]  # one gap of 1, but two runs: never a pair
    _, probes = plan(bench)
    assert len(probes) == 4 and all(m.sell.item == m.buy.item == "bench:b7" for m in probes)
    _, probes = plan(bench, BenchProbe(ProbeConfig(per_tick=8, max_gap=100)))
    assert len(probes) == 6
    _, probes = plan(bench, room=2)
    assert len(probes) == 2  # the slots the exact plan (and the public matches) leave
    _, probes = plan(bench, BenchProbe(ProbeConfig(max_gap=11)))
    assert ids(probes) == [("b7-0", "b7-6", 54)]  # gap 11; the next one (13) is past the gap


def test_a_refused_pair_is_not_proposed_again_at_the_same_price_and_runs_give_up():
    probe = BenchProbe(ProbeConfig(give_up_after=3, tries_per_pair=2))
    bench = [bench_sell("b7-0", 71), bench_buy("b7-1", 66)]
    _, [m] = plan(bench, probe)
    probe.record(m, accepted=False, code="invalid")
    assert plan(bench, probe)[1] == []  # same quotes, same price: not again
    moved = [bench_sell("b7-0", 69), bench_buy("b7-1", 66)]  # the seller relaxed: a new price
    _, [m2] = plan(moved, probe)
    assert m2.price == 67
    probe.record(m2, accepted=False, code="invalid")
    assert plan([bench_sell("b7-0", 68), bench_buy("b7-1", 66)], probe)[1] == []  # tried twice: done with it
    other = [bench_sell("b7-2", 80), bench_buy("b7-3", 75)]
    _, [m3] = plan(other, probe)
    probe.record(m3, accepted=False, code="invalid")
    assert not probe.active("bench:b7") and plan(other + bench, probe)[1] == []  # 3 refusals, none accepted
    assert probe.codes == {"invalid": 3}
    assert probe.quote_rule and not probe.active("bench:b8")  # the server checks quotes: no later run probes
    assert probe.summary("bench:b8") == "probes 0 accepted, 0 refused (stopped)"


def test_queued_probes_settle_by_the_next_book():
    probe = BenchProbe()
    _, [m] = plan([bench_sell("b7-0", 71), bench_buy("b7-1", 66)], probe)
    probe.sent(m)
    assert probe.resolve(quotes([bench_sell("b7-5", 80)])) == [(m, True)]
    _, [m2] = plan([bench_sell("b7-2", 71), bench_buy("b7-3", 66)], probe)
    probe.sent(m2)
    assert probe.resolve(quotes([bench_buy("b7-3", 67)])) == [(m2, False)]
    assert probe.codes == {"dropped": 1} and probe.pending == []


def test_one_accepted_probe_keeps_the_run_probing():
    probe = BenchProbe(ProbeConfig(give_up_after=1))
    _, [m] = plan([bench_sell("b7-0", 71), bench_buy("b7-1", 66)], probe)
    probe.record(m, accepted=True)
    _, [m2] = plan([bench_sell("b7-2", 80), bench_buy("b7-3", 75)], probe)
    probe.record(m2, accepted=False)
    assert probe.active("bench:b7") and probe.summary("bench:b7") == "probes 1 accepted, 1 refused (probing)"


# ---------------------------------------------------------------- the broker


class QuoteRuleBroker(FakeBroker):
    """The server as openapi documents it: a bench match must cross by quote."""

    def match(self, sell: Any, buy: Any, price: int) -> dict[str, Any]:
        quote = {o["id"]: (o["want"].get("cash") or o["give"].get("cash")) for o in self.bench}
        if sell in quote and buy in quote and not quote[sell] <= price <= quote[buy]:
            self.sent.append((sell, buy, price))
            raise BazaarError("invalid", "price outside the quotes", 400)
        return super().match(sell, buy, price)


def probe_broker(tmp_path, broker, lines=None, policy="probe"):
    a = agent(tmp_path, broker, live=True, lines=lines, allow_venue_open=True)
    a.config = BrokerConfig(bench_policy=policy, pace_s=0.0)
    return a


def two_books():
    return [bench_sell("b7-0", 30), bench_buy("b7-1", 90), bench_sell("b7-2", 71), bench_buy("b7-3", 66)]


def test_the_exact_plan_goes_out_first_then_the_probe(tmp_path):
    lines: list[str] = []
    broker = QuoteRuleBroker(bench=two_books())
    probe_broker(tmp_path, broker, lines).on_tick(clock(tick=5))
    assert broker.sent == [("b7-0", "b7-1", 60), ("b7-2", "b7-3", 68)]
    decisions = [d for d in rows(tmp_path) if "reason" in d]  # live: the settle rows follow
    assert [d["reason"] for d in decisions] == [EXACT, PROBE]
    assert any("bench probe b7-2×b7-3 at 68 REFUSED invalid" in line for line in lines)
    assert any("broker: bench book b7-0:s30 b7-1:b90 b7-2:s71 b7-3:b66" in line for line in lines)


def test_an_accepted_probe_takes_both_traders(tmp_path):
    lines: list[str] = []
    broker = FakeBroker(bench=two_books())  # accepts anything: the server checks limits (and these cross)
    a = probe_broker(tmp_path, broker, lines)
    a.on_tick(clock(tick=5))
    assert broker.bench == [] and "b7-2" not in a.done  # queued: the next book says whether it settled
    assert any("bench probe b7-2×b7-3 at 68 QUEUED" in line for line in lines)
    assert a.history[-1].surplus_bench == 60  # the probe's negative quoted surplus is not counted
    a.on_tick(clock(tick=6))
    assert any("SETTLED (both traders gone) · probes 1 accepted, 0 refused (probing)" in line for line in lines)


class SettlementDropBroker(FakeBroker):
    """Queues every match but settles only the ones that cross by quote: a dropped probe's traders come back."""

    def match(self, sell: Any, buy: Any, price: int) -> dict[str, Any]:
        quote = {o["id"]: (o["want"].get("cash") or o["give"].get("cash")) for o in self.bench}
        if quote.get(sell, 0) <= price <= quote.get(buy, 0):
            return super().match(sell, buy, price)
        self.sent.append((sell, buy, price))
        return {"queued": True, "settles_at_tick": 6}


def test_a_probe_dropped_at_settlement_counts_as_refused_and_its_traders_stay_open(tmp_path):
    lines: list[str] = []
    broker = SettlementDropBroker(bench=two_books())
    a = probe_broker(tmp_path, broker, lines)
    a.probe = BenchProbe(ProbeConfig(give_up_after=1))
    a.on_tick(clock(tick=5))
    broker.bench = [bench_sell("b7-2", 64), bench_buy("b7-3", 66)]  # back, and now they cross by quote
    a.on_tick(clock(tick=6))
    assert any("DROPPED at settlement (traders back in the book)" in line for line in lines)
    assert broker.sent[-1] == ("b7-2", "b7-3", 65)  # the exact plan takes them
    assert a.probe.codes == {"dropped": 1} and a.probe.quote_rule
    broker.bench = [bench_sell("b8-0", 71), bench_buy("b8-1", 66)]
    a.on_tick(clock(tick=7))
    assert broker.sent[-1] == ("b7-2", "b7-3", 65)  # a whole run refused: no more probes in this process


def test_with_the_quote_rule_the_probe_broker_matches_exactly_what_the_exact_broker_matches(tmp_path):
    exact_broker, probe_server = QuoteRuleBroker(bench=two_books()), QuoteRuleBroker(bench=two_books())
    exact = probe_broker(tmp_path / "e", exact_broker, policy="exact")
    probing = probe_broker(tmp_path / "p", probe_server)
    for tick in range(5, 9):
        exact.on_tick(clock(tick=tick))
        probing.on_tick(clock(tick=tick))
    assert exact_broker.bench == probe_server.bench and exact.done == probing.done
    accepted = [s for s in probe_server.sent if s[0] == "b7-0"]
    assert accepted == exact_broker.sent == [("b7-0", "b7-1", 60)]
    assert len(probe_server.sent) == 2  # one refused probe, not repeated while the quotes stand still


def test_a_failing_probe_planner_leaves_the_exact_plan_alone(tmp_path, monkeypatch):
    lines: list[str] = []
    broker = QuoteRuleBroker(bench=two_books())
    a = probe_broker(tmp_path, broker, lines)

    def boom(*args: object, **kwargs: object) -> list[Any]:
        raise RuntimeError("boom")

    monkeypatch.setattr(a.probe, "plan", boom)
    a.on_tick(clock(tick=5))
    assert broker.sent == [("b7-0", "b7-1", 60)]
    assert any("bench probe failed (RuntimeError); exact matching only" in line for line in lines)


def test_probe_comes_from_the_environment_and_names_itself(tmp_path, monkeypatch):
    assert bench_config_from_env(BrokerConfig(), {"BAZAAR_BENCH_POLICY": "Probe"}).bench_policy == "probe"
    assert bench_text(BrokerConfig(bench_policy="probe")) == "probe (exact + non-crossing probes)"
    monkeypatch.setenv("BAZAAR_BENCH_POLICY", "probe")
    lines: list[str] = []
    assert keeper(tmp_path, Team(), lines=lines).broker_config.bench_policy == "probe"
    assert any("broker bench probe (exact + non-crossing probes)" in line for line in lines)


# ---------------------------------------------------------------- on the simulator's Market Test


class SimProbe:
    """The broker's bench planning on the simulator's book: exact, then probes, each probe settled (or not) by the
    next read, as the real server's queued matches are."""

    def __init__(self, probing: bool, config: ProbeConfig | None = None) -> None:
        self.probing, self.probe = probing, BenchProbe(config)

    def __call__(self, book: dict[str, Any]) -> list[tuple[str, str, int]]:
        qs = quotes_from(BrokerBook.model_validate(book)).quotes
        self.probe.resolve(qs)
        exact = plan_matches(qs, Fee(), 15)
        probes = self.probe.plan(qs, exact, Fee(), 15 - len(exact)) if self.probing else []
        for m in probes:
            self.probe.sent(m)
        return [(str(m.sell.id), str(m.buy.id), m.price) for m in exact + probes]


@pytest.mark.parametrize("preset", [NORMAL, HARD])
def test_on_the_simulator_the_quote_rule_costs_nothing_and_the_limit_rule_gains(preset):
    seeds = range(60)
    gain = 0.0
    for seed in seeds:
        exact = simulate(SimProbe(False), preset, seed, rule="quote")
        probed = simulate(SimProbe(True), preset, seed, rule="quote")
        assert probed.realised == exact.realised and probed.matches == exact.matches  # refusals only
        assert exact.realised == exact.stall_realised
        gain += simulate(SimProbe(True), preset, seed, rule="limit").efficiency - exact.efficiency
    assert gain / len(seeds) > 0.01
