"""Sales actor: real guard/publication path, fakes only at SDK boundaries."""

from dataclasses import replace

import pytest

from bazaar_agent.agents import publication
from bazaar_agent.agents.runtime import Recorder
from bazaar_agent.agents.sales_outreach import SalesOutreach, sale_lead
from bazaar_agent.agents.seller import open_commitments
from bazaar_agent.decisions import DecisionLog
from bazaar_agent.guardrails import Guardrails, Ledger
from bazaar_agent.sdk import BazaarError
from bazaar_agent.team_matrix import Cell, TeamMatrix
from tests.agent_fakes import our_ask
from tests.test_rivals import bid
from tests.test_team_desk import Team, view


def setup(tmp_path, **overrides):
    rules = Guardrails(
        team_threads_enabled=True, pause_file=str(tmp_path / "PAUSE"), protect_page_sets="LAT,LAV", **overrides
    )
    team = Team()
    ledger = Ledger(tmp_path / "ledger.jsonl")
    rec = Recorder("sales", DecisionLog(tmp_path), True, lambda _: None)
    actor = SalesOutreach(team, rules, ledger, rec, lambda _: None, True)
    v = view()
    matrix = TeamMatrix(v.tick, v.us, (Cell("t05", "LAT-03", 0, 0, True, 9, 10, 0.4),), {})
    return actor, team, ledger, v, matrix


def test_proactive_sale_posts_exact_guarded_terms_and_reserves_copy(tmp_path):
    actor, team, ledger, v, matrix = setup(tmp_path)
    actor.on_tick(v, matrix)
    assert team.sent[0] == ("open_thread", "t05", {"trade": "cards"}, "rastro")
    kind, tid, terms = team.sent[1]
    assert kind == "say" and tid == 42
    assert terms["want"]["cash"] >= 2 and len(terms["give"]["assets"]) == 1
    asset = terms["give"]["assets"][0]
    assert asset in {3, 4}
    assert team.reads == ["me", "my_offers", "my_threads"]
    assert ledger.count_in_tick("operator_say:42", v.tick) == 1
    assert ledger.count_in_tick("listing", v.tick) == 1
    assert ledger.spent_since(0) == 0
    actor.on_tick(v, matrix)
    assert len([x for x in team.sent if x[0] == "say"]) == 1


@pytest.mark.parametrize("reason", ["promised", "sole", "paused", "deadline", "slots", "listing_cap", "blocklist"])
def test_unavailable_sales_send_nothing(tmp_path, reason):
    extra = {"team_desk_never_trade": "t05"} if reason == "blocklist" else {}
    actor, team, ledger, v, matrix = setup(tmp_path, **extra)
    if reason == "promised":
        team.offers = [our_ask(1, 3, "LAT-03", 8), our_ask(2, 4, "LAT-03", 8)]
    elif reason == "sole":
        team._me["assets"] = [a for a in team._me["assets"] if a["id"] != 4]
    elif reason == "paused":
        (tmp_path / "PAUSE").touch()
    elif reason == "deadline":
        v = replace(v, window_open=lambda: False)
    elif reason == "slots":
        team.threads = [{"kind": "team", "with": "t02"}, {"kind": "team", "with": "t03"}]
    elif reason == "listing_cap":
        v = replace(v, listing_cap=0)
    actor.on_tick(v, matrix)
    assert team.sent == []


@pytest.mark.parametrize("status", [0, 408, 503])
def test_unknown_sale_retains_promise_across_restart(tmp_path, status):
    actor, team, ledger, v, matrix = setup(tmp_path)

    def lost(*args, **kwargs):
        raise BazaarError("unknown", "lost response", status)

    team.say = lost
    actor.on_tick(v, matrix)
    pending = publication.with_pending(ledger, team._me, [], v.us, v.tick + 1, v.t_hours)
    assert len(open_commitments(pending, v.us).listed) == 1
    restarted = SalesOutreach(team, actor.rules, ledger, actor.rec, lambda _: None, True)
    restarted.on_tick(replace(v, tick=v.tick + 1), matrix)
    assert len([s for s in team.sent if s[0] == "open_thread"]) == 1


def test_unknown_open_is_not_retried_on_restart(tmp_path):
    actor, team, ledger, v, matrix = setup(tmp_path)
    calls = []

    def lost(*args, **kwargs):
        calls.append(args)
        raise BazaarError("unknown", "lost response", 503)

    team.open_thread = lost
    actor.on_tick(v, matrix)
    SalesOutreach(team, actor.rules, ledger, actor.rec, lambda _: None, True).on_tick(replace(v, tick=101), matrix)
    assert len(calls) == 1


def test_definitive_rejection_releases_asset_promise(tmp_path):
    actor, team, ledger, v, matrix = setup(tmp_path)

    def refused(*args, **kwargs):
        raise BazaarError("refused", "rejected", 403)

    team.say = refused
    actor.on_tick(v, matrix)
    assert publication.with_pending(ledger, team._me, [], v.us, v.tick + 1, v.t_hours) == []


def test_deadline_after_ledger_writes_prevents_say_and_releases_unsent(tmp_path, monkeypatch):
    actor, team, ledger, v, matrix = setup(tmp_path)
    alive = [True]
    record = ledger.record

    def expires(kind, *args, **kwargs):
        record(kind, *args, **kwargs)
        if kind == "listing":
            alive[0] = False

    monkeypatch.setattr(ledger, "record", expires)
    actor.on_tick(replace(v, window_open=lambda: alive[0]), matrix)
    assert [s[0] for s in team.sent] == ["open_thread"]
    assert publication.with_pending(ledger, team._me, [], v.us, 101, v.t_hours) == []


def test_existing_public_bid_supplies_lead_but_own_or_expired_bids_do_not(tmp_path):
    actor, _, _, v, _ = setup(tmp_path)
    event = bid(9, 99, "t05", "LAT-03", 9, expires=110)
    lead = sale_lead(replace(v, events=[event]), actor.rules, None)
    assert lead is not None and lead.price == 9 and lead.to == "t05"
    assert sale_lead(replace(v, tick=111, events=[event]), actor.rules, None) is None
    assert sale_lead(replace(v, events=[bid(9, 99, "t01", "LAT-03", 9)]), actor.rules, None) is None


def test_latest_matrix_is_only_a_hint_and_never_sets_our_private_floor(tmp_path):
    actor, team, _, v, matrix = setup(tmp_path, sell_min_value_ratio=3)
    actor.on_tick(v, matrix)
    offered = team.sent[-1][2]
    aid = offered["give"]["assets"][0]
    value = next(a["your_value"] for a in team._me["assets"] if a["id"] == aid)
    assert offered["want"]["cash"] >= value * 3
    assert sale_lead(replace(v, tick=v.tick + 31), actor.rules, matrix) is None


def test_words_receive_real_budget_and_cannot_change_structured_terms(tmp_path):
    actor, team, ledger, v, matrix = setup(tmp_path)
    requests = []
    actor.words = lambda req: requests.append(req) or "Hola: vendo por 0 primas (palabras no son términos)."
    actor.on_tick(replace(v, budget_s=lambda: 2.5), matrix)
    assert requests[0].budget_s == 2.5
    assert team.sent[-1][2]["want"]["cash"] > 0
    assert ledger.count_in_tick("listing", v.tick) == 1


def test_words_exhausting_tick_prevent_offer_without_releasing_unknown_prior_assets(tmp_path):
    actor, team, ledger, v, matrix = setup(tmp_path)
    alive = [True]

    def words(req):
        alive[0] = False
        return "Hola."

    actor.words = words
    actor.on_tick(replace(v, window_open=lambda: alive[0]), matrix)
    assert [s[0] for s in team.sent] == ["open_thread"]
    assert ledger.count_in_tick("listing", v.tick) == 0


def test_full_protected_page_remains_unavailable_under_current_policy(tmp_path):
    actor, team, _, v, matrix = setup(tmp_path, protect_complete_pages_only=True)
    team._me["assets"] = [a for a in team._me["assets"] if a["id"] != 4]
    team._me["album"] = {"pages": [{"set": "LAT", "have": 10, "of": 10, "complete": True}]}
    # Server album representation is normalized by the existing guard's our_cards helper.
    from bazaar_agent.move_impact import our_cards

    assert "LAT" in our_cards(team._me).complete
    actor.on_tick(v, matrix)
    assert team.sent == []


def test_pause_during_words_blocks_sale_after_opening(tmp_path):
    actor, team, ledger, v, matrix = setup(tmp_path)

    def words(req):
        (tmp_path / "PAUSE").touch()
        return "Hola."

    actor.words = words
    actor.on_tick(v, matrix)
    assert [s[0] for s in team.sent] == ["open_thread"]
    assert ledger.count_in_tick("listing", v.tick) == 0


def test_pause_during_last_ledger_write_releases_only_proven_unsent_promise(tmp_path, monkeypatch):
    actor, team, ledger, v, matrix = setup(tmp_path)
    record = ledger.record

    def pause(kind, *args, **kwargs):
        record(kind, *args, **kwargs)
        if kind == "listing":
            (tmp_path / "PAUSE").touch()

    monkeypatch.setattr(ledger, "record", pause)
    actor.on_tick(v, matrix)
    assert [s[0] for s in team.sent] == ["open_thread"]
    assert publication.with_pending(ledger, team._me, [], v.us, 101, v.t_hours) == []


def test_only_successful_message_ack_is_buffered_for_voice(tmp_path):
    actor, team, _, v, matrix = setup(tmp_path)
    seen = []
    actor.sent_words = lambda *args: seen.append(args)
    actor.on_tick(v, matrix)
    assert len(seen) == 1 and seen[0][:5] == (42, "t05", "t01", 100, 1)
    assert isinstance(seen[0][5], str) and seen[0][6] == team.sent[-1][2]


def test_lost_message_response_never_invents_a_quote(tmp_path):
    actor, team, _, v, matrix = setup(tmp_path)
    seen = []
    actor.sent_words = lambda *args: seen.append(args)

    def unknown(*args, **kwargs):
        raise BazaarError("unknown", "lost", 503)

    team.say = unknown
    actor.on_tick(v, matrix)
    assert seen == []


def test_structured_sale_uses_eligible_alliance_venue_and_records_actual_route(tmp_path):
    from bazaar_agent.agents.market import venues_from

    actor, team, ledger, v, matrix = setup(tmp_path)
    rows = [
        {"venue": "v19", "owner": "t01", "status": "open", "fee_bps": 0, "fee_per_card": 0},
        {"venue": "v05", "owner": "t05", "status": "open", "fee_bps": 0, "fee_per_card": 0},
        {"venue": "v15", "owner": "t15", "status": "open", "fee_bps": 0, "fee_per_card": 0},
    ]
    v = replace(
        v,
        venues=(*v.venues, *venues_from({"venues": rows})),
        params=v.params.model_copy(update={"preferred_sell_venue_owners": "t01,t05,t15"}),
    )
    from bazaar_agent.learn.threads import ThreadStore

    store = ThreadStore(None)
    actor.sent_words = store.sent
    assert actor.on_tick(v, matrix) is True
    assert store.buffer[42].thread["venue"] == "v15"
    assert team.sent[0] == ("open_thread", "t05", {"trade": "cards"}, "v15")
    assert team.sent[1][0] == "say" and team.sent[1][2]["give"]["assets"]
    assert ledger.count_in_tick("listing", v.tick) == 1
    import json

    decisions = [json.loads(line) for line in (actor.rec.decisions.dir / "decisions.jsonl").read_text().splitlines()]
    assert all(row["inputs"]["venue"] == "v15" for row in decisions if "inputs" in row)


def test_alliance_preference_never_increases_buyers_acceptance_fee(tmp_path):
    from bazaar_agent.agents.market import venues_from

    actor, _, _, v, matrix = setup(tmp_path)
    rows = [
        {"venue": "v15", "owner": "t15", "status": "open", "fee_bps": 1000, "fee_per_card": 2},
        {"venue": "v07", "owner": "t10", "status": "open", "fee_bps": 0, "fee_per_card": 0},
    ]
    v = replace(
        v,
        venues=(*v.venues, *venues_from({"venues": rows})),
        params=v.params.model_copy(update={"preferred_sell_venue_owners": "t15"}),
    )
    lead = sale_lead(v, actor.rules, matrix)
    assert lead is not None and lead.venue == "v07"


def test_no_structured_candidate_returns_false_for_promotion_fallback(tmp_path):
    actor, team, _, v, matrix = setup(tmp_path)
    assert actor.on_tick(replace(v, me={**v.me, "assets": []}), matrix) is False
    assert team.sent == []


def test_unknown_structured_open_stops_promotion_in_same_tick(tmp_path):
    actor, team, _, v, matrix = setup(tmp_path)

    def unknown(*args, **kwargs):
        raise BazaarError("network", "lost", 0)

    team.open_thread = unknown
    assert actor.on_tick(v, matrix) is True
