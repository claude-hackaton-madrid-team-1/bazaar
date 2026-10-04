"""Operator confirmations cannot mutate terms, bypass floors or redispatch uncertain requests."""

from copy import deepcopy

import pytest
from pydantic import ValidationError

from bazaar_agent.ledger_pg import LedgerUnavailable, trade_lock
from bazaar_agent.runtime import operator as op
from tests.runtime_fakes import Team, backend, with_spare


class Store(op.Proposals):
    def __init__(self):
        self.rows = {}

    def unresolved(self, target):
        return any(r["status"] in ("unknown", "executing") for r in self.rows.values())

    def create(self, payload):
        self.rows[payload["proposal_id"]] = deepcopy(payload)

    def get(self, ident):
        return deepcopy(self.rows[ident])

    def transition(self, ident, before, after, result=None):
        if self.rows[ident]["status"] != before:
            return False
        self.rows[ident].update(status=after, result=deepcopy(result))
        return True


def proposal(b, store, action=None):
    row = op.propose(b, op.Propose(action=action or {"kind": "sell_list", "target": "LAT-03", "price": 10}), store)
    return row, op.ProposalId(proposal_id=row["proposal_id"])


def test_boundaries_reject_arbitrary_commands_and_fields():
    for action in (
        {"kind": "shell", "text": "curl"},
        {"kind": "team_say", "thread_id": 2, "text": "hello", "price": 8},
        {"kind": "team_open", "team_id": "abuela"},
    ):
        with pytest.raises(ValidationError):
            op.Propose(action=action)


def test_proposal_and_approval_never_send_and_execution_is_once(tmp_path):
    b, store = backend(tmp_path, live=True, team=with_spare(Team())), Store()
    row, ident = proposal(b, store)
    assert row["allowed"] and row["action"]["target"].isdigit()
    assert op.execute(b, ident, store)["status"] == "proposed"
    assert not b.team.sent
    assert op.approve(b, ident, store)["status"] == "approved"
    assert not b.team.sent
    result = op.execute(b, ident, store)
    assert result["status"] == "submitted" and result["sent"]
    sent = list(b.team.sent)
    again = op.execute(b, ident, store)
    assert not again["sent"] and b.team.sent == sent


def test_dry_run_records_terminal_result_without_sending(tmp_path):
    b, store = backend(tmp_path, team=with_spare(Team())), Store()
    _, ident = proposal(b, store)
    op.approve(b, ident, store)
    assert op.execute(b, ident, store)["status"] == "dry_run"
    assert b.team.sent == []


def test_holdings_change_after_approval_cannot_sell_last_copy(tmp_path):
    b, store = backend(tmp_path, live=True, team=with_spare(Team())), Store()
    _, ident = proposal(b, store)
    op.approve(b, ident, store)
    b.rules = b.rules.model_copy(update={"protect_page_sets": "LAT"})
    chosen = int(store.get(ident.proposal_id)["action"]["target"])
    b.team._me["assets"] = [a for a in b.team._me["assets"] if a.get("ref") != "LAT-03" or a["id"] == chosen]
    result = op.execute(b, ident, store)
    assert result["status"] == "denied" and not b.team.sent


def test_floor_change_requires_new_proposal(tmp_path):
    b, store = backend(tmp_path, live=True, team=with_spare(Team())), Store()
    _, ident = proposal(b, store)
    op.approve(b, ident, store)
    chosen = int(store.get(ident.proposal_id)["action"]["target"])
    next(a for a in b.team._me["assets"] if a["id"] == chosen)["your_value"] = 100
    assert op.execute(b, ident, store)["status"] == "denied"
    assert not b.team.sent


@pytest.mark.human_approval
def test_exact_human_confirmation_can_cover_60p_but_not_floor(tmp_path):
    b, store = backend(tmp_path, live=True, team=with_spare(Team())), Store()
    b.rules = b.rules.model_copy(update={"human_approval_above": 60})
    row, ident = proposal(b, store, {"kind": "sell_list", "target": "LAT-03", "price": 60})
    assert row["allowed"]
    op.approve(b, ident, store)
    assert op.execute(b, ident, store)["status"] == "submitted"


def test_unknown_send_is_never_repeated(tmp_path, monkeypatch):
    b, store = backend(tmp_path, live=True, team=with_spare(Team())), Store()
    _, ident = proposal(b, store)
    op.approve(b, ident, store)
    calls = []

    def lost(*args):
        calls.append(args)
        raise TimeoutError("reply lost after the server applied the write")

    monkeypatch.setattr(op, "_send", lost)
    assert op.execute(b, ident, store)["status"] == "unknown"
    assert op.execute(b, ident, store)["status"] == "unknown"
    assert len(calls) == 1


def test_expired_or_changed_round_never_dispatches(tmp_path):
    b, store = backend(tmp_path, live=True, team=with_spare(Team())), Store()
    _, ident = proposal(b, store)
    op.approve(b, ident, store)
    b.public.now = b.public.now.model_copy(update={"tick": b.public.now.tick + 9})
    assert op.execute(b, ident, store)["status"] == "expired"
    assert not b.team.sent


def test_publication_mutex_fails_closed_and_releases(tmp_path):
    b = backend(tmp_path)
    with trade_lock(b.ledger), pytest.raises(LedgerUnavailable), trade_lock(b.ledger):
        pytest.fail("concurrent publication permitted")
    with trade_lock(b.ledger):
        pass


def test_unknown_blocks_a_new_proposal_too(tmp_path, monkeypatch):
    b, store = backend(tmp_path, live=True, team=with_spare(Team())), Store()
    _, first = proposal(b, store)
    op.approve(b, first, store)
    monkeypatch.setattr(op, "_send", lambda *a: {"status": "unknown", "sent": None})
    op.execute(b, first, store)
    _, second = proposal(b, store)
    op.approve(b, second, store)
    assert op.execute(b, second, store)["status"] == "denied"


def test_exact_new_offer_reconciles_but_absence_does_not(tmp_path):
    from tests.agent_fakes import ask

    b, store = backend(tmp_path, live=True, team=with_spare(Team())), Store()
    row, ident = proposal(b, store)
    store.transition(ident.proposal_id, "proposed", "unknown")
    assert op.review(b, ident, store)["status"] == "unknown"
    b.team.offers.append(ask(9001, "LAT-03", 10, asset=int(row["action"]["target"]), maker="t01"))
    result = op.review(b, ident, store)
    assert result["status"] == "reconciled"
    assert result["reconciliation"]["matching_offer_ids"] == [9001]


def test_typed_team_cash_offer_and_message_share_tick_limit(tmp_path):
    team = with_spare(Team(thread_payloads={5: {"id": 5, "with": "t09", "status": "open", "messages": []}}))
    b, store = backend(tmp_path, live=True, team=team), Store()
    _, ident = proposal(
        b,
        store,
        {
            "kind": "team_offer",
            "side": "sell",
            "thread_id": 5,
            "target": "LAT-03",
            "price": 10,
            "text": "Can this help your album?",
        },
    )
    op.approve(b, ident, store)
    assert op.execute(b, ident, store)["status"] == "submitted"
    row, _ = proposal(b, store, {"kind": "team_say", "thread_id": 5, "text": "Any thoughts?"})
    assert not row["allowed"] and "message slot" in row["reason"]


def test_accept_requires_unchanged_price_and_fee(tmp_path):
    from tests.agent_fakes import ask

    incoming = ask(55, "LAV-09", 20, maker="t09")
    b, store = backend(tmp_path, live=True, team=Team(offers=[incoming])), Store()
    row, ident = proposal(b, store, {"kind": "offer_accept", "offer_id": 55})
    assert row["allowed"]
    op.approve(b, ident, store)
    b.team.offers[0]["want"]["cash"] = 21
    assert op.execute(b, ident, store)["status"] == "denied"
    assert not b.team.sent


def test_accept_bid_with_no_copy_fails_closed(tmp_path):
    from bazaar_agent.agents.seller import OfferError
    from tests.agent_fakes import bid

    b = backend(tmp_path, live=True, team=Team(offers=[bid(55, "LAV-09", 30, maker="t09")]))
    with pytest.raises(OfferError):
        proposal(b, Store(), {"kind": "offer_accept", "offer_id": 55})
    assert not b.team.sent


def test_snapshot_marks_missing_history_and_does_not_invent_candidates(tmp_path):
    result = op.snapshot(backend(tmp_path))
    assert result["history_available"] is False and result["candidates"] == []
    assert result["clock"]["tick"] == 100 and "score" in result and "limits" in result["budget"]


def test_snapshot_exposes_unobserved_publication_without_releasing_or_sending(tmp_path):
    from bazaar_agent.agents import publication

    b = backend(tmp_path, team=with_spare(Team()))
    with trade_lock(b.ledger):
        publication.reserve(b.ledger, 99, 1.0, "t01", {"assets": [41]}, {"cash": 10}, to="t09")
    before = b.ledger.publication_rows()
    result = op.snapshot(b)
    pending = [o for o in result["pending"] if o["publication_pending"]]
    assert len(pending) == 1 and pending[0]["created_tick"] == 99
    assert pending[0]["give"]["assets"] == [{"id": 41, "ref": "LAT-03"}]
    assert pending[0]["want"] == {"cash": 10}
    assert b.ledger.publication_rows() == before and b.team.sent == []


def test_proposal_cannot_be_reused_after_team_key_changes(tmp_path):
    b, store = backend(tmp_path, live=True, team=with_spare(Team())), Store()
    row, ident = proposal(b, store)
    assert row["team"] == "t01"
    op.approve(b, ident, store)
    b.team._me["id"] = "t02"
    with pytest.raises(ValueError, match="another team"):
        op.execute(b, ident, store)
    assert not b.team.sent


def test_activity_labels_evidence_without_inventing_idle_work():
    from tests.agent_fakes import clock

    assert op._activity([], True, clock())["state"] == "unknown"
    assert op._activity([{"kind": "no_work", "status": "approved"}], True, clock())["state"] == "no_work"
    assert op._activity([{"kind": "trade", "status": "rejected", "reason": "floor"}], True, clock()) == {
        "state": "blocked",
        "reason": "floor",
    }
    assert op._activity([{"kind": "duel_hold", "status": "approved"}], True, clock())["state"] == "active"


def test_new_decision_evidence_has_explicit_target_and_round(tmp_path, monkeypatch):
    import json

    from bazaar_agent.decisions import DecisionLog
    from tests.test_ledger import decision

    monkeypatch.setenv("RAILWAY_SERVICE_NAME", "maker")
    monkeypatch.setenv("RAILWAY_DEPLOYMENT_ID", "deploy-test")
    log = DecisionLog(tmp_path, game_url="http://localhost:9999/")
    log.begin_tick(123, 3)
    log.decide(decision(tick=123))
    row = json.loads((tmp_path / "agents" / "decisions.jsonl").read_text())
    assert row["inputs"]["evidence_context"]["world"] == "simulator"
    assert row["inputs"]["evidence_context"]["round"] == 3
    assert row["inputs"]["evidence_context"]["deploy"] == "deploy-test"
    assert row["inputs"]["evidence_context"]["service"] == "maker"
    assert "http://" not in json.dumps(row["inputs"]["evidence_context"])


def test_accept_never_promises_an_asset_already_committed(tmp_path):
    from bazaar_agent.agents import publication
    from tests.agent_fakes import bid

    team = with_spare(Team(offers=[bid(55, "LAT-03", 30, maker="t09")]))
    b = backend(tmp_path, live=True, team=team)
    publication.reserve(b.ledger, 100, 1.0, "t01", {"assets": [41]}, {"cash": 10})
    row, _ = proposal(b, Store(), {"kind": "offer_accept", "offer_id": 55})
    assert not row["allowed"] and "already promised" in row["reason"]
    assert not team.sent


def test_accept_sale_reserves_until_the_asset_leaves(tmp_path):
    from bazaar_agent.agents import publication
    from tests.agent_fakes import bid

    team = with_spare(Team(offers=[bid(55, "LAT-03", 30, maker="t09")]))
    b, store = backend(tmp_path, live=True, team=team), Store()
    row, ident = proposal(b, store, {"kind": "offer_accept", "offer_id": 55})
    assert row["allowed"]
    asset = row["terms"]["assets"][0]
    op.approve(b, ident, store)
    assert op.execute(b, ident, store)["status"] == "submitted"
    pending = publication.with_pending(b.ledger, team._me, [], "t01", 101, 1.0)
    assert pending[0]["give"]["assets"][0]["id"] == asset
    team._me["assets"] = [a for a in team._me["assets"] if a["id"] != asset]
    assert publication.with_pending(b.ledger, team._me, [], "t01", 102, 1.0) == []


def test_listing_rejects_our_venue(tmp_path):
    from tests.runtime_fakes import Public

    public = Public()
    public.venues = lambda: {
        "venues": [{"id": "own", "name": "Own", "owner": "t01", "status": "open", "fee": {"rate": 0, "flat": 0}}]
    }
    b = backend(tmp_path, live=True, team=with_spare(Team()), public=public)
    row, _ = proposal(b, Store(), {"kind": "sell_list", "target": "LAT-03", "price": 10, "venue": "own"})
    assert not row["allowed"] and "owned by us" in row["reason"]
    assert not b.team.sent


def test_definitive_rejection_does_not_block_later_operator_work(tmp_path, monkeypatch):
    from bazaar_agent.sdk import BazaarError

    b, store = backend(tmp_path, live=True, team=with_spare(Team())), Store()
    original = b.team.list_offer

    def reject(*args, **kwargs):
        raise BazaarError("offer_limit", "refused", 400)

    monkeypatch.setattr(b.team, "list_offer", reject)
    _, first = proposal(b, store)
    op.approve(b, first, store)
    assert op.execute(b, first, store)["status"] == "denied"
    monkeypatch.setattr(b.team, "list_offer", original)
    _, second = proposal(b, store)
    op.approve(b, second, store)
    assert op.execute(b, second, store)["status"] == "submitted"


def test_accept_purchase_keeps_cash_until_exact_asset_arrives(tmp_path):
    from bazaar_agent.agents import publication
    from tests.agent_fakes import ask

    team = Team(offers=[ask(55, "LAV-09", 20, asset=909, maker="t09")])
    b, store = backend(tmp_path, live=True, team=team), Store()
    _, ident = proposal(b, store, {"kind": "offer_accept", "offer_id": 55})
    op.approve(b, ident, store)
    assert op.execute(b, ident, store)["status"] == "submitted"
    pending = publication.with_pending(b.ledger, team._me, [], "t01", 101, 1.0)
    assert pending[0]["give"]["cash"] > 20
    team._me["assets"].append({"id": 909, "ref": "LAV-09", "kind": "card"})
    assert publication.with_pending(b.ledger, team._me, [], "t01", 102, 1.0) == []
