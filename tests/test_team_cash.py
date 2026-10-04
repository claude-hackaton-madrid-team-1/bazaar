"""Private cash offers use the same guarded executor as public asks and bids."""

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
