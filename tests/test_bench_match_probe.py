"""The one Market Test match probe: a non-crossing pair, once per game, never at the exact matcher's expense."""

from pathlib import Path

import pytest

from bazaar_agent import venue as vn
from bazaar_agent.agents.bench_match_probe import CLOSE_GAP, PATIENCE_TICKS, pick_probe
from bazaar_agent.agents.broker import BrokerAgent, BrokerConfig, bench_config_from_env
from bazaar_agent.agents.matcher import Fee, Quote
from bazaar_agent.decisions import DecisionLog
from bazaar_agent.guardrails import Guardrails
from bazaar_agent.sdk import BazaarError
from tests.agent_fakes import clock, rows
from tests.test_broker_agent import FakeBroker, FakeTeam
from tests.test_matcher import bench_buy, bench_sell


def sell(oid, ask):
    return Quote(oid, "sell", "bench:b5", ask, oid)


def buy(oid, bid):
    return Quote(oid, "buy", "bench:b5", bid, oid)


class Conn:
    """The vault's psycopg surface over a shared dict: the claim row is created once, whoever asks."""

    def __init__(self, store, fail=False):
        self.store, self.fail, self.closed, self.autocommit, self.last = store, fail, False, False, None

    def execute(self, sql, params=()):
        if self.fail:
            raise RuntimeError("database down")
        self.last = None
        if sql.startswith("insert into venue_broker_keys") and "returning venue" in sql:
            target, venue, tick = params
            if (target, venue) not in self.store:
                self.store[(target, venue)] = ("", tick)
                self.last = (venue,)
        elif not sql.startswith("create table"):
            raise AssertionError(sql)
        return self

    def fetchone(self):
        return self.last

    def close(self):
        self.closed = True


def vault(tmp_path, store, fail=False):
    return vn.KeyVault(Path(tmp_path), lambda: Conn(store, fail), target="game")


# ---------------------------------------------------------------- the pick


def test_picks_the_smallest_gap_and_a_price_between_the_quotes():
    quotes = [sell("b5-0", 50), sell("b5-2", 60), buy("b5-1", 46), buy("b5-3", 40)]
    found = pick_probe(quotes, set(), Fee())
    assert (found.match.sell.id, found.match.buy.id, found.gap) == ("b5-0", "b5-1", 4)
    assert found.match.price == 48 and 46 < found.match.price < 50


def test_a_crossing_pair_is_never_a_probe_and_traders_in_use_are_never_taken():
    quotes = [sell("b5-0", 30), buy("b5-1", 40), Quote("b6-2", "sell", "bench:b6", 50, "b6-2"), buy("b5-3", 20)]
    quotes.append(Quote("b6-3", "buy", "bench:b6", 46, "b6-3"))
    assert pick_probe(quotes, set(), Fee()).match.sell.id == "b6-2"  # the crossing b5 pair is not a candidate
    assert pick_probe(quotes, {"b6-2"}, Fee()).match.sell.id == "b5-0"  # the far b5 pair is all that is left
    assert pick_probe(quotes, {"b6-3", "b5-3"}, Fee()) is None


def test_the_fee_counts_toward_the_gap():
    found = pick_probe([sell("b5-0", 40), buy("b5-1", 41)], set(), Fee(per_card=3))
    assert found is not None and found.gap == 2  # 40 + 3 - 41


def test_a_far_pair_waits_until_the_run_is_old():
    quotes = [sell("b5-0", 80), buy("b5-1", 80 - CLOSE_GAP - 5)]
    assert pick_probe(quotes, set(), Fee(), run_age={"b5": 1}) is None
    assert pick_probe(quotes, set(), Fee(), run_age={"b5": PATIENCE_TICKS}) is not None


# ---------------------------------------------------------------- the claim


def test_the_claim_is_taken_once_across_processes_and_never_reads_as_a_broker_key(tmp_path):
    store = {}
    first, second = vault(tmp_path, store), vault(tmp_path, store)
    assert first.claim_once("bench_match_probe", 10) is True
    assert second.claim_once("bench_match_probe", 11) is False and first.claim_once("bench_match_probe", 12) is False
    assert store == {("game", "_once:bench_match_probe"): ("", 10)}  # empty key column: `load` skips it


def test_an_unreadable_database_never_claims(tmp_path):
    assert vault(tmp_path, {}, fail=True).claim_once("bench_match_probe", 10) is False


# ---------------------------------------------------------------- the broker agent


def book():
    return FakeBroker(
        bench=[
            bench_sell("b5-0", 30),
            bench_buy("b5-1", 40),  # crosses: the exact matcher's pair
            bench_sell("b6-0", 50),
            bench_buy("b6-1", 46),  # another run, 4 short of crossing: the probe
        ]
    )


def agent(tmp_path, broker, *, claim, live=True, probe=True, lines=None):
    a = BrokerAgent(
        broker,
        FakeTeam(),
        us="t01",
        rules=Guardrails(pause_file=str(tmp_path / "PAUSE"), allow_venue_open=True),
        decisions=DecisionLog(tmp_path),
        live=live,
        log=(lines.append if lines is not None else (lambda line: None)),
        stats_dir=tmp_path / "agents",
        config=BrokerConfig(match_probe=probe),
    )
    a.probe_claim = claim
    return a


def test_the_probe_goes_out_once_after_the_exact_match_and_leaves_exact_alone(tmp_path):
    store, broker, lines = {}, book(), []
    v = vault(tmp_path, store)
    a = agent(tmp_path, broker, claim=v.claim_once, lines=lines)
    a.on_tick(clock(tick=201))
    assert broker.sent == [("b5-0", "b5-1", 35), ("b6-0", "b6-1", 48)]  # exact first, then the one probe
    a.on_tick(clock(tick=202))
    broker.bench = [bench_sell("b7-0", 60), bench_buy("b7-1", 57)]
    a.on_tick(clock(tick=203))
    assert len(broker.sent) == 2  # never a second probe
    probes = [d for d in rows(tmp_path) if d.get("kind") == "bench_probe"]
    assert [d["move"] for d in probes] == [{"sell": "b6-0", "buy": "b6-1", "price": 48}] * 2  # the ask + the answer
    assert probes[1]["inputs"]["answer"]["accepted"] is True
    assert not any(" refused " in line for line in lines if "PROBE" in line or "probe" in line)


def test_the_server_turning_it_down_is_recorded_whole_and_still_the_only_probe(tmp_path):
    broker = book()
    broker.refuse = {"b6-0"}
    a = agent(tmp_path, broker, claim=vault(tmp_path, {}).claim_once)
    a.on_tick(clock(tick=201))
    answer = next(d for d in rows(tmp_path) if d.get("kind") == "bench_probe" and "answer" in d["inputs"])
    assert answer["inputs"]["answer"]["accepted"] is False and answer["inputs"]["answer"]["code"] == "invalid"
    a.on_tick(clock(tick=202))
    assert [s for s in broker.sent if s[0] == "b6-0"] == [("b6-0", "b6-1", 48)]


def test_a_redeploy_or_second_process_finds_the_claim_and_sends_nothing(tmp_path):
    store, broker = {}, book()
    agent(tmp_path, broker, claim=vault(tmp_path, store).claim_once).on_tick(clock(tick=201))
    assert ("b6-0", "b6-1", 48) in broker.sent
    broker2 = book()
    agent(tmp_path, broker2, claim=vault(tmp_path, store).claim_once).on_tick(clock(tick=250))
    assert broker2.sent == [("b5-0", "b5-1", 35)]  # exact still runs; the probe does not


@pytest.mark.parametrize("claim", [None, lambda name, tick: False])
def test_without_a_readable_ledger_nothing_is_sent(tmp_path, claim):
    broker = book()
    agent(tmp_path, broker, claim=claim).on_tick(clock(tick=201))
    assert broker.sent == [("b5-0", "b5-1", 35)]


def test_off_by_default_and_never_in_a_dry_run(tmp_path):
    store = {}
    for kwargs in ({"probe": False}, {"live": False}):
        broker = book()
        agent(tmp_path, broker, claim=vault(tmp_path, store).claim_once, **kwargs).on_tick(clock(tick=201))
        assert all(s[0] != "b6-0" for s in broker.sent)
    assert store == {}  # nothing was claimed either


def test_a_failing_probe_never_costs_the_tick(tmp_path):
    def boom(name, tick):
        raise RuntimeError("claim exploded")

    lines, broker = [], book()
    agent(tmp_path, broker, claim=boom, lines=lines).on_tick(clock(tick=201))
    assert broker.sent == [("b5-0", "b5-1", 35)] and any("match probe failed" in line for line in lines)


def test_a_transport_error_on_the_probe_is_recorded(tmp_path):
    class Down(FakeBroker):
        def match(self, sell, buy, price):
            if sell == "b6-0":
                raise BazaarError("network", "timeout", 0)
            return super().match(sell, buy, price)

    a = agent(tmp_path, Down(bench=book().bench), claim=vault(tmp_path, {}).claim_once)
    a.on_tick(clock(tick=201))
    answer = next(d for d in rows(tmp_path) if d.get("kind") == "bench_probe" and "answer" in d["inputs"])
    assert answer["inputs"]["answer"]["accepted"] is False


# ---------------------------------------------------------------- the switch


def test_the_env_switch_is_off_by_default_and_ignores_typos_loudly():
    base, lines = BrokerConfig(), []
    assert bench_config_from_env(base, {}).match_probe is False
    assert bench_config_from_env(base, {"BAZAAR_BENCH_MATCH_PROBE": "Once"}).match_probe is True
    assert bench_config_from_env(base, {"BAZAAR_BENCH_MATCH_PROBE": "yes"}, lines.append).match_probe is False
    assert any("IGNORED BAZAAR_BENCH_MATCH_PROBE" in line for line in lines)
