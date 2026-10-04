"""Active offers reserve duplicates; a dormant team desk does not reserve stock."""

from bazaar_agent.agents import publication
from bazaar_agent.agents.maker import Target, _leave_desk_copy
from bazaar_agent.agents.team_desk import spare_copy
from bazaar_agent.guardrails import Guardrails
from tests.agent_fakes import clock, our_ask
from tests.test_maker import NoAccept, maker, posted
from tests.test_strategy import ME


def asked_ids(team):
    return [a for p in posted(team) for a in p[1].get("assets", [])]


def test_two_copies_allow_one_ask_even_when_team_desk_enabled(tmp_path):
    team = NoAccept()
    agent, _ = maker(tmp_path, team, live=True, team_threads_enabled=True, protect_page_sets="LAT,LAV")
    agent.on_tick(clock())
    assert asked_ids(team) == [4]
    assert spare_copy(ME, [our_ask(1, 4, "LAT-03", 10)], "t01", "LAT-03") is None


def test_unknown_swap_promise_blocks_maker_after_process_restart(tmp_path):
    team = NoAccept()
    agent, _ = maker(tmp_path, team, live=True, team_threads_enabled=True, protect_page_sets="LAT,LAV")
    publication.reserve(agent.ledger, 99, 1.4, "t01", {"assets": [3]}, {"cards": ["LAV-02"]}, 42)
    agent.on_tick(clock())
    assert not ({3, 4} & set(asked_ids(team)))


def test_existing_ask_is_not_cancelled_to_reserve_a_dormant_swap(tmp_path):
    team = NoAccept(offers=[our_ask(1, 4, "LAT-03", 10)])
    agent, _ = maker(tmp_path, team, live=True, team_threads_enabled=True, protect_page_sets="LAT,LAV")
    agent.on_tick(clock())
    assert ("cancel", 1) not in team.sent


def test_target_selection_keeps_one_copy_and_does_not_change_asset_identity():
    targets = [Target("ask", "LAT-03", "common", 10, ident, 1.2, 5, "test") for ident in (3, 4)]
    assert _leave_desk_copy(targets, ME, Guardrails(protect_page_sets="LAT")) == targets[:1]


def test_team_desk_uses_another_free_duplicate_when_cheapest_is_reserved():
    me = {**ME, "assets": [*ME["assets"], {"id": 7, "ref": "LAT-03", "your_value": 1.2}]}
    assert spare_copy(me, [our_ask(1, 3, "LAT-03", 10)], "t01", "LAT-03") == 4


def test_http_408_does_not_release_a_publication_promise(tmp_path, monkeypatch):
    from bazaar_agent.sdk import BazaarError

    team = NoAccept()
    agent, _ = maker(tmp_path, team, live=True)

    def timeout(*args, **kwargs):
        raise BazaarError("upstream_timeout", "response uncertain", 408)

    monkeypatch.setattr(team, "list_offer", timeout)
    agent.on_tick(clock())
    assert publication.with_pending(agent.ledger, ME, [], "t01", 110, 2.0)
