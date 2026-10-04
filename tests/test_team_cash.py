"""Private cash offers use the same guarded executor as public asks and bids."""

import json
from copy import deepcopy

import pytest

from bazaar_agent.agents.team_desk import cash_offer
from tests.agent_fakes import FakePublic, ask, bid, clock
from tests.test_taker import taker
from tests.test_team_desk import Team, thread


def setup_cash(tmp_path, offer, **rules):
    payload = thread(offers=[offer], opened_by="t05")
    team = Team(threads=[payload], thread_payloads={42: payload})
    agent, lines, ledger = taker(
        tmp_path,
        team,
        FakePublic(),
        live=True,
        team_threads_enabled=True,
        team_threads_max_open=0,
        **rules,
    )
    return agent, team, lines, ledger


def test_accepts_positive_private_cash_ask(tmp_path):
    agent, team, _, _ = setup_cash(tmp_path, ask(901, "LAV-02", 10, maker="t05", to="t01"))
    agent.on_tick(clock())
    assert ("accept", 901, None) in team.sent
    assert "thread 42" in team.reads
    assert not any(s[0] in {"say", "walk"} for s in team.sent)


def test_accepts_positive_private_cash_bid_for_duplicate(tmp_path):
    agent, team, _, _ = setup_cash(tmp_path, bid(902, "LAT-03", 10, maker="t05"))
    agent.on_tick(clock())
    assert ("accept", 902, [4]) in team.sent


@pytest.mark.parametrize("field,value", [("to", "t09"), ("status", "accepted"), ("expires_tick", 99)])
def test_cash_parser_rejects_unavailable_offers(field, value):
    offer = ask(901, "LAV-02", 10, maker="t05", to="t01")
    offer[field] = value
    assert cash_offer(offer, "t01", "t05", "rastro", 100) is None


def test_rechecks_private_offer_before_accepting(tmp_path):
    agent, team, _, _ = setup_cash(tmp_path, ask(901, "LAV-02", 10, maker="t05", to="t01"))
    team.thread_payloads[42] = deepcopy(team.thread_payloads[42])
    team.thread_payloads[42]["standing_offers"][0]["status"] = "cancelled"
    agent.on_tick(clock())
    assert not any(s[0] == "accept" for s in team.sent)


def test_private_bid_cannot_sell_last_page_copy(tmp_path):
    agent, team, _, _ = setup_cash(
        tmp_path,
        bid(902, "LAV-01", 50, maker="t05"),
        protect_page_sets="LAV,LAT",
    )
    agent.on_tick(clock())
    assert not any(s[0] == "accept" for s in team.sent)


def test_unknown_cash_accept_reserves_cash_past_two_ticks(tmp_path, monkeypatch):
    from bazaar_agent.agents import publication
    from bazaar_agent.agents.seller import open_commitments
    from bazaar_agent.sdk import BazaarError

    agent, team, _, ledger = setup_cash(tmp_path, ask(901, "LAV-02", 10, asset=909, maker="t05", to="t01"))

    def lost(*args, **kwargs):
        raise BazaarError("network", "response lost", 0)

    monkeypatch.setattr(team, "accept", lost)
    agent.on_tick(clock())
    pending = publication.with_pending(ledger, team._me, [], "t01", 110, 2.0)
    assert open_commitments(pending, "t01").cash == 12
    team._me["assets"].append({"id": 909, "kind": "card", "ref": "LAV-02"})
    assert publication.with_pending(ledger, team._me, [], "t01", 111, 2.0) == []


def counter_desk(tmp_path, offer, **rules):
    from dataclasses import replace

    from bazaar_agent.agents.team_desk import _Plan
    from bazaar_agent.guardrails import Ledger
    from bazaar_agent.official_values import OfficialValues
    from tests.test_team_desk import TICK, desk, view

    payload = thread(offers=[offer], opened_by="t05")
    team = Team(threads=[payload], thread_payloads={42: payload})
    d, _ = desk(tmp_path, team, **rules)
    d._plan = _Plan(TICK, (), {})
    d.ledger = Ledger(tmp_path / "cash-ledger.jsonl")
    v = view([payload], in_use=6)
    ctx = replace(v.ctx(None), values=OfficialValues(lambda ref: {"card": ref, "your_value": 10}))
    return d, team, replace(v, ctx=lambda _: ctx)


@pytest.mark.official_values
def test_actor_posts_cash_counter_with_official_ceiling_and_no_fee_to_publisher(tmp_path):
    d, team, v = counter_desk(tmp_path, ask(901, "LAV-02", 50, maker="t05", to="t01"))
    d.proposals(v)
    d.converse(v, set())
    sent = [s for s in team.sent if s[0] == "say"]
    assert len(sent) == 1
    terms = sent[0][2]
    assert terms["give"]["cash"] == int(10 - v.params.min_buy_surplus)
    assert terms["want"] == {"cards": ["LAV-02"]}
    # Counterpublisher pays no fee; accepting the original ask would cost 50 + venue fee.
    assert d.ledger.spent_since(0) == terms["give"]["cash"]
    d.converse(v, set())
    assert len([s for s in team.sent if s[0] == "say"]) == 1
    assert d.ledger.count_in_tick("listing", v.tick) == 1
    rows = [json.loads(line) for line in (tmp_path / "agents" / "decisions.jsonl").read_text().splitlines()]
    context = next(row["inputs"] for row in rows if row.get("kind") == "team_cash_offer")
    assert {key: context[key] for key in ("ref", "side", "price", "venue", "counterparty")} == {
        "ref": "LAV-02",
        "side": "bid",
        "price": terms["give"]["cash"],
        "venue": "rastro",
        "counterparty": "t05",
    }


def test_actor_counters_low_cash_bid_with_only_a_free_duplicate(tmp_path):
    d, team, v = counter_desk(tmp_path, bid(902, "LAT-03", 1, maker="t05"))
    d.proposals(v)
    d.converse(v, set())
    terms = next(s[2] for s in team.sent if s[0] == "say")
    asset = next(a for a in v.me["assets"] if a["id"] == terms["give"]["assets"][0])
    assert terms["want"]["cash"] >= asset["your_value"] + v.params.sell_min_surplus
    assert asset["ref"] == "LAT-03"
    assert d.ledger.spent_since(0) == 0


@pytest.mark.parametrize("ref", ["LAV-01", "NOT-HELD"])
def test_cash_counter_never_promises_a_single_or_absent_copy(tmp_path, ref):
    d, team, v = counter_desk(tmp_path, bid(902, ref, 50, maker="t05"))
    d.proposals(v)
    d.converse(v, set())
    assert not any(s[0] == "say" for s in team.sent)


def test_pending_cash_counter_blocks_second_worker_and_charges_budget_once(tmp_path, monkeypatch):
    from bazaar_agent.agents import publication
    from bazaar_agent.sdk import BazaarError

    d, team, v = counter_desk(tmp_path, ask(901, "LAV-02", 50, maker="t05", to="t01"))
    calls = []

    def lost(*args, **kwargs):
        calls.append(args)
        raise BazaarError("unavailable", "unknown", status=503)

    monkeypatch.setattr(team, "say", lost)
    d.proposals(v)
    d.converse(v, set())
    before = d.ledger.spent_since(0)
    assert before > 0 and len(calls) == 1
    d._cash_sent.clear()  # another/restarted worker has no memory of this send
    d.converse(v, set())
    assert len(calls) == 1 and d.ledger.spent_since(0) == before
    assert len(publication.with_pending(d.ledger, v.me, [], v.us, v.tick, v.t_hours)) == 1


@pytest.mark.official_values
def test_cash_counter_fails_closed_on_missing_value_or_no_cash(tmp_path):
    from dataclasses import replace

    from bazaar_agent.official_values import OfficialValues

    d, team, v = counter_desk(tmp_path, ask(901, "LAV-02", 50, maker="t05", to="t01"))
    ctx = replace(v.ctx(None), values=OfficialValues(lambda ref: {}))
    d.proposals(v)
    d.converse(replace(v, ctx=lambda _: ctx), set())
    assert team.sent == []
    team._me["cash"] = d.rules.cash_floor
    d.converse(v, set())
    assert team.sent == []


@pytest.mark.official_values
def test_taker_tick_can_counter_when_no_swap_exists(tmp_path):
    agent, team, _, ledger = setup_cash(tmp_path, ask(901, "LAV-02", 50, maker="t05", to="t01"))
    team.value = lambda ref: {"card": ref, "your_value": 10}
    agent.on_tick(clock())
    offers = [s[2] for s in team.sent if s[0] == "say" and isinstance(s[2], dict)]
    assert len(offers) == 1 and offers[0]["want"] == {"cards": ["LAV-02"]}
    assert offers[0]["give"]["cash"] < 50
    assert ledger.spent_since(0) >= offers[0]["give"]["cash"]
    assert not any(s[0] == "accept" for s in team.sent)


@pytest.mark.parametrize("block", ["pending_copy", "message_slot", "open_offer_cap"])
def test_cash_counter_respects_shared_capacity(tmp_path, block):
    from dataclasses import replace

    from bazaar_agent.agents import publication

    d, team, v = counter_desk(tmp_path, bid(902, "LAT-03", 1, maker="t05"))
    if block == "pending_copy":
        publication.reserve(d.ledger, v.tick, v.t_hours, v.us, {"assets": [3]}, {"cash": 10})
    elif block == "message_slot":
        d.ledger.record("operator_say:42", v.tick, v.t_hours)
    else:
        v = replace(v, max_open_offers=1)
        publication.reserve(d.ledger, v.tick, v.t_hours, v.us, {"cash": 1}, {"cards": ["LAV-09"]})
    d.proposals(v)
    d.converse(v, set())
    assert not any(s[0] == "say" for s in team.sent)


def test_cash_refund_after_restart_is_once_only_and_not_swap_budget(tmp_path):
    from dataclasses import replace

    from bazaar_agent.agents.team_desk import TeamDesk

    d, team, v = counter_desk(tmp_path, ask(901, "LAV-02", 50, maker="t05", to="t01"))
    d.proposals(v)
    d.converse(v, set())
    amount = d.ledger.spent_since(0)
    assert amount > 0 and d.ledger.spent_since(0, "team:") == 0
    ours = deepcopy(team.offers[0])
    ours["status"] = "expired"
    ended = replace(v, tick=v.tick + 8, threads=[thread(offers=[ours])])
    restarted = TeamDesk(team, d.rules, d.rec, d.log, True, ledger=d.ledger)
    restarted.proposals(ended)
    restarted.converse(ended, set())
    assert d.ledger.spent_since(0) == 0
    restarted.refunded.clear()
    restarted._refund(ended, ours)
    assert d.ledger.spent_since(0) == 0  # persistent dedup, not the process's refunded set
    unknown = {**ours, "id": 999, "thread": 999}
    restarted._refund(ended, unknown)
    assert d.ledger.spent_since(0) == 0  # arbitrary operator offer cannot invent credit


@pytest.mark.parametrize("stop", ["deadline", "pause"])
def test_cash_counter_drops_tick_expiring_during_reservation(tmp_path, monkeypatch, stop):
    from dataclasses import replace

    from bazaar_agent.agents import publication

    d, team, v = counter_desk(tmp_path, ask(901, "LAV-02", 50, maker="t05", to="t01"))
    open_ = True
    record = d.ledger.record

    d.rules = d.rules.model_copy(update={"pause_file": str(tmp_path / "PAUSE")})

    def expire(kind, *args, **kwargs):
        nonlocal open_
        record(kind, *args, **kwargs)
        if kind == "listing":
            if stop == "pause":
                (tmp_path / "PAUSE").touch()
            else:
                open_ = False

    monkeypatch.setattr(d.ledger, "record", expire)
    v = replace(v, window_open=lambda: open_)
    d.proposals(v)
    d.converse(v, set())
    assert not any(s[0] == "say" for s in team.sent)
    assert d.ledger.spent_since(0) == 0
    assert publication.with_pending(d.ledger, v.me, [], v.us, v.tick, v.t_hours) == []
    assert d.ledger.count_in_tick("operator_say:42", v.tick) == 1


@pytest.mark.parametrize("venue_id,owner", [("v15", "t15"), ("v28", "t18"), ("v05", "t04"), ("v07", "t10")])
def test_alliance_cash_counter_uses_actual_venue_and_persists_ack(tmp_path, venue_id, owner):
    from dataclasses import replace

    from bazaar_agent.agents.market import Venue

    offer = {**bid(902, "LAT-03", 1, maker="t05"), "venue": venue_id}
    d, team, v = counter_desk(tmp_path, offer)
    payload = team.thread_payloads[42]
    payload["venue"] = venue_id
    v = replace(
        v,
        threads=[payload],
        venues=[Venue(venue_id, owner, 0, 0, "open", "board", 0, False)],
        params=v.params.model_copy(update={"preferred_sell_venue_owners": owner}),
    )
    acknowledged = []
    d.sent_words = lambda *args: acknowledged.append(args)
    d.proposals(v)
    d.converse(v, set())
    terms = next(s[2] for s in team.sent if s[0] == "say")
    asset = next(a for a in v.me["assets"] if a["id"] == terms["give"]["assets"][0])
    assert terms["want"]["cash"] >= asset["your_value"] + v.params.sell_min_surplus
    assert acknowledged[0][-1] == venue_id
    assert "LAT-03" in acknowledged[0][5] and venue_id in acknowledged[0][5]
    rows = [json.loads(line) for line in (tmp_path / "agents" / "decisions.jsonl").read_text().splitlines()]
    assert next(r["inputs"]["venue"] for r in rows if r.get("kind") == "team_cash_offer") == venue_id


@pytest.mark.parametrize(
    "owner,status,ref",
    [("t01", "open", "LAT-03"), ("t05", "open", "LAT-03"), ("t18", "closed", "LAT-03"), ("t18", "open", "LAV-01")],
)
def test_alliance_counter_retains_venue_and_only_copy_protections(tmp_path, owner, status, ref):
    from dataclasses import replace

    from bazaar_agent.agents.market import Venue

    d, team, v = counter_desk(tmp_path, {**bid(902, ref, 1, maker="t05"), "venue": "v15"})
    payload = team.thread_payloads[42]
    payload["venue"] = "v15"
    v = replace(
        v,
        threads=[payload],
        venues=[Venue("v15", owner, 0, 0, status, "board", 0, False)],
        params=v.params.model_copy(update={"preferred_sell_venue_owners": owner}),
    )
    d.proposals(v)
    d.converse(v, set())
    assert not any(s[0] == "say" for s in team.sent)


@pytest.mark.official_values
@pytest.mark.parametrize("preferred", ["t10", "t18"])
def test_alliance_buy_counter_keeps_official_ceiling_and_preference_boundary(tmp_path, preferred):
    from dataclasses import replace

    from bazaar_agent.agents.market import Venue

    d, team, v = counter_desk(tmp_path, {**ask(901, "LAV-02", 50, maker="t05", to="t01"), "venue": "v07"})
    payload = team.thread_payloads[42]
    payload["venue"] = "v07"
    v = replace(
        v,
        threads=[payload],
        venues=[Venue("v07", "t10", 0, 0, "open", "board", 0, False)],
        params=v.params.model_copy(update={"preferred_sell_venue_owners": preferred}),
    )
    d.proposals(v)
    d.converse(v, set())
    sent = [s for s in team.sent if s[0] == "say"]
    if preferred == "t10":
        assert len(sent) == 1
        assert sent[0][2]["give"]["cash"] == int(10 - v.params.min_buy_surplus)
        assert d.ledger.spent_since(0) == sent[0][2]["give"]["cash"]
    else:
        assert sent == []
