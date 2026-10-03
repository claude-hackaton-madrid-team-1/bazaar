"""El Taller (TL1): the pure planner, hand-picked plans, the guardrails, the ledger count and `convert`.

No network and no Postgres: every `Context` passes `stops=()` and `breakers=frozenset()`, so `check` never reads the
pause file or the breaker board, and the ledger is a JSONL file in `tmp_path`."""

from __future__ import annotations

from typing import Any

import pytest

from bazaar_agent.agents.seller import open_commitments
from bazaar_agent.guardrails import Action, Context, Guardrails, Ledger, breaker_scope, check, last_copy_refusals
from bazaar_agent.taller import (
    TALLER_ITEM,
    TALLER_KIND,
    TallerError,
    TallerResult,
    convert,
    free_spares,
    plan_from_ids,
    plan_taller,
    taller_action,
    taller_context,
)
from tests.agent_fakes import FakeTeam

ON = Guardrails(taller_enabled=True)
US = "t01"


def card(aid: int, ref: str, rarity: str | None = "common", value: float | None = 5.0) -> dict[str, Any]:
    a: dict[str, Any] = {"id": aid, "kind": "card", "ref": ref}
    if rarity is not None:
        a["rarity"] = rarity
    if value is not None:
        a["your_value"] = value
    return a


def copies(ref: str, first_id: int, n: int, rarity: str = "common", value: float = 5.0) -> list[dict[str, Any]]:
    return [card(first_id + i, ref, rarity, value) for i in range(n)]


def me_of(*assets: dict[str, Any], affinity: dict[str, float] | None = None) -> dict[str, Any]:
    return {
        "id": US,
        "cash": 400,
        "affinity": affinity if affinity is not None else {"LAV": 1.0, "LAT": 1.0, "MAL": 1.0, "SAL": 1.0},
        "assets": [*assets, {"id": 999, "kind": "pack", "ref": "sobre_barrio"}],
    }


def our_ask(oid: int, *assets: Any, status: str = "open") -> dict[str, Any]:
    return {"id": oid, "maker": US, "status": status, "give": {"assets": list(assets)}, "want": {"cash": 10}}


def ctx_of(held: dict[str, int], **kw: Any) -> Context:
    base: dict[str, Any] = {"stops": (), "breakers": frozenset(), "tallers_last_hour": 0}
    base.update(kw)
    return Context(cash=400, held=held, tick=10, t_hours=2.0, **base)


def taller(item: str, rarity: str | None = "common") -> Action:
    return Action("taller", item, rarity)


# ---------------------------------------------------------------- plan_taller


def test_plan_is_none_while_the_switch_is_off() -> None:
    me = me_of(*copies("LAV-01", 1, 5))
    assert plan_taller(me, [], Guardrails()) is None
    assert plan_taller(me, [], ON) is not None


def test_a_card_held_once_is_never_an_input_and_four_copies_give_at_most_three() -> None:
    me = me_of(*copies("LAV-01", 1, 4), card(10, "LAT-03"), card(11, "MAL-01"), card(12, "SAL-01"))
    plan = plan_taller(me, [], ON)
    assert plan is not None
    assert plan.rarity == "common" and plan.pulls == "uncommon"
    assert plan.refs == ["LAV-01", "LAV-01", "LAV-01"]
    assert sorted(plan.assets) == [2, 3, 4]  # the lowest id is the copy we keep
    assert not {10, 11, 12} & set(plan.assets)


def test_three_copies_of_one_card_and_singles_make_no_triple() -> None:
    me = me_of(*copies("LAV-01", 1, 3), card(10, "LAT-03"), card(11, "MAL-01"))
    assert plan_taller(me, [], ON) is None  # 2 spares of LAV-01, 0 of the singles


def test_copies_in_our_open_asks_are_excluded_and_reduce_the_spare_count() -> None:
    me = me_of(*copies("LAV-01", 1, 4))
    offers = [our_ask(70, {"id": 4, "ref": "LAV-01"})]
    spares = free_spares(me, open_commitments(offers, US))
    assert [s.asset_id for s in spares] == [3, 2]  # 4 held - 1 listed - 1 kept; the listed copy is never one
    assert plan_taller(me, offers, ON) is None

    me2 = me_of(*copies("LAV-01", 1, 4), *copies("LAT-03", 10, 2))
    plan = plan_taller(me2, offers, ON)
    assert plan is not None
    assert 4 not in plan.assets
    assert sorted(plan.refs) == ["LAT-03", "LAV-01", "LAV-01"]


def test_an_accepted_ask_still_settling_keeps_its_copy_out() -> None:
    me = me_of(*copies("LAV-01", 1, 4))
    offers = [our_ask(70, {"id": 4, "ref": "LAV-01"}, status="accepted")]
    assert plan_taller(me, offers, ON) is None


@pytest.mark.parametrize("asset", [{"id": 4}, 4, {"id": 555}])
def test_an_ask_that_does_not_name_its_card_counts_against_every_card(asset: Any) -> None:
    me = me_of(*copies("LAV-01", 1, 4))
    assert plan_taller(me, [], ON) is not None
    offers = [our_ask(70, asset)]
    spares = free_spares(me, open_commitments(offers, US))
    assert len(spares) == 2
    assert plan_taller(me, offers, ON) is None


def test_commons_go_before_uncommons() -> None:
    me = me_of(*copies("LAV-01", 1, 4), *copies("LAV-06", 10, 4, rarity="uncommon"))
    plan = plan_taller(me, [], ON)
    assert plan is not None and plan.rarity == "common" and plan.pulls == "uncommon"


def test_an_uncommon_triple_only_when_no_common_triple_exists() -> None:
    me = me_of(*copies("LAV-01", 1, 3), *copies("LAV-06", 10, 4, rarity="uncommon"))
    plan = plan_taller(me, [], ON)
    assert plan is not None
    assert plan.rarity == "uncommon" and plan.pulls == "rare"
    assert plan.refs == ["LAV-06"] * 3


def test_rares_and_above_are_never_fed_in() -> None:
    me = me_of(*copies("LAV-09", 1, 5, rarity="rare"), *copies("LAV-11", 10, 5, rarity="epic"))
    assert plan_taller(me, [], ON) is None


def test_cards_held_more_than_max_copies_kept_go_first() -> None:
    affinity = {"LAT": 0.5, "LAV": 1.6, "SAL": 1.2}
    me = me_of(
        *copies("LAT-03", 1, 2, value=1.0),  # cheapest set, 1 spare
        *copies("LAV-01", 10, 3, value=16.0),  # 3 > max_copies_kept 2: both spares first
        *copies("SAL-01", 20, 2, value=5.0),
        affinity=affinity,
    )
    plan = plan_taller(me, [], ON)
    assert plan is not None
    assert plan.refs == ["LAV-01", "LAV-01", "LAT-03"]
    assert ON.max_copies_kept == 2


def test_then_the_lowest_set_multiplier() -> None:
    affinity = {"LAT": 0.5, "LAV": 1.6, "SAL": 1.2, "MAL": 1.0}
    me = me_of(
        *copies("LAV-01", 1, 2, value=1.0),  # cheapest value, but the dearest set
        *copies("LAT-03", 10, 2, value=20.0),
        *copies("SAL-01", 20, 2, value=3.0),
        *copies("MAL-01", 30, 2, value=4.0),
        affinity=affinity,
    )
    plan = plan_taller(me, [], ON)
    assert plan is not None
    assert plan.refs == ["LAT-03", "MAL-01", "SAL-01"]  # three different cards of one rarity


def test_then_the_lowest_your_value() -> None:
    me = me_of(
        *copies("LAV-01", 1, 2, value=9.0),
        *copies("LAT-03", 10, 2, value=3.0),
        *copies("SAL-01", 20, 2, value=5.0),
        *copies("MAL-01", 30, 2, value=7.0),
        affinity={},  # no multiplier read: 1.0 for every set
    )
    plan = plan_taller(me, [], ON)
    assert plan is not None
    assert plan.refs == ["LAT-03", "SAL-01", "MAL-01"]
    assert plan.your_value == 15.0


def test_unknown_rarity_or_missing_your_value_is_skipped() -> None:
    me = me_of(
        *copies("LAV-01", 1, 2),
        *copies("LAT-03", 10, 2),
        card(20, "MAL-01", rarity=None),
        card(21, "MAL-01", rarity=None),
        card(30, "SAL-01", value=None),
        card(31, "SAL-01", value=None),
        {"id": 40, "kind": "card", "ref": "SAL-02", "rarity": "common", "your_value": True},
        {"id": 41, "kind": "card", "ref": "SAL-02", "rarity": "common", "your_value": True},
    )
    assert plan_taller(me, [], ON) is None  # LAV-01 + LAT-03: two spares only
    catalog = {"sets": [{"id": "MAL", "cards": [{"id": "MAL-01", "rarity": "common"}]}]}
    plan = plan_taller(me, [], ON, catalog)
    assert plan is not None
    assert sorted(plan.refs) == ["LAT-03", "LAV-01", "MAL-01"]


def test_a_copy_with_no_integer_id_is_never_a_spare() -> None:
    me = me_of(*copies("LAV-01", 1, 3), {"id": "x", "kind": "card", "ref": "LAV-01", "rarity": "common"})
    spares = free_spares(me, open_commitments([], US))
    assert all(isinstance(s.asset_id, int) for s in spares)
    assert len(spares) <= 3


def test_every_plan_passes_the_last_copy_rule_of_its_own_context() -> None:
    me = me_of(*copies("LAV-01", 1, 2), *copies("LAT-03", 10, 2), *copies("SAL-01", 20, 2), card(30, "MAL-01"))
    offers = [our_ask(70, {"id": 20, "ref": "SAL-01"})]
    plan = plan_taller(me, offers, ON)
    assert plan is None  # SAL-01: 2 held, 1 listed → no spare; LAV-01 + LAT-03 give only two
    me2 = me_of(*me["assets"][:-1], card(31, "MAL-01"))
    plan2 = plan_taller(me2, offers, ON)
    assert plan2 is not None
    held = {"LAV-01": 2, "LAT-03": 2, "SAL-01": 2, "MAL-01": 2}
    sellable = {"LAV-01": 2, "LAT-03": 2, "SAL-01": 1, "MAL-01": 2}
    assert last_copy_refusals(plan2.refs, sellable) == []
    assert check(taller_action(plan2), ctx_of(held, sellable=sellable), ON).allowed


# ---------------------------------------------------------------- plan_from_ids


HAND = me_of(*copies("LAV-01", 1, 3), *copies("LAT-03", 10, 2), card(20, "LAV-06", rarity="uncommon"))


@pytest.mark.parametrize(
    ("ids", "offers", "why"),
    [
        ([1, 2, 777], [], "not a card we hold"),
        ([1, 2, 999], [], "not a card we hold"),  # a pack we hold is not a card
        ([1, 2, 3], [our_ask(70, {"id": 3, "ref": "LAV-01"})], "open offers"),
        ([1, 2], [], "3 different asset ids"),
        ([1, 2, 3, 10], [], "3 different asset ids"),
        ([1, 1, 2], [], "3 different asset ids"),
    ],
)
def test_plan_from_ids_refuses_bad_picks(ids: list[int], offers: list[dict[str, Any]], why: str) -> None:
    with pytest.raises(TallerError, match=why):
        plan_from_ids(HAND, ids, offers)


def test_plan_from_ids_of_one_rarity() -> None:
    plan = plan_from_ids(HAND, [2, 3, 11], [])
    assert plan.rarity == "common" and plan.pulls == "uncommon"
    assert plan.refs == ["LAV-01", "LAV-01", "LAT-03"]
    assert [s.copies for s in plan.spares] == [3, 3, 2]


def test_mixed_rarities_are_planned_as_mixed_and_refused_by_check() -> None:
    plan = plan_from_ids(HAND, [2, 3, 20], [])
    assert plan.rarity == "mixed" and plan.pulls == "?"
    held = {"LAV-01": 3, "LAT-03": 2, "LAV-06": 2}
    verdict = check(taller_action(plan), ctx_of(held), ON)
    assert not verdict.allowed
    assert any("'mixed'" in v for v in verdict.violations)


# ---------------------------------------------------------------- guardrails.check


def test_check_allows_a_conversion_that_keeps_a_copy_of_each_card() -> None:
    assert check(taller("LAV-01,LAV-01,LAV-01"), ctx_of({"LAV-01": 4}), ON).allowed
    held = {"LAT-03": 2, "LAV-01": 2, "SAL-01": 2}
    verdict = check(taller("LAT-03,LAV-01,SAL-01"), ctx_of(held), ON)
    assert verdict.allowed and str(verdict) == "allowed"
    assert check(taller("LAV-06,LAV-06,LAV-06", "uncommon"), ctx_of({"LAV-06": 4}), ON).allowed


def test_check_refuses_a_last_copy() -> None:
    verdict = check(taller("LAT-03,LAT-03,LAV-01"), ctx_of({"LAT-03": 2, "LAV-01": 2}), ON)
    assert not verdict.allowed and not verdict.halted
    assert len(verdict.violations) == 1
    assert "LAT-03" in verdict.violations[0] and "LAV-01" not in verdict.violations[0]


def test_check_refuses_a_card_we_do_not_hold() -> None:
    verdict = check(taller("LAV-01,LAV-01,MAL-01"), ctx_of({"LAV-01": 3}), ON)
    assert not verdict.allowed
    assert any("MAL-01" in v for v in verdict.violations)


def test_check_counts_the_free_copies_when_sellable_is_set() -> None:
    held = {"LAV-01": 4}
    assert check(taller("LAV-01,LAV-01,LAV-01"), ctx_of(held), ON).allowed
    verdict = check(taller("LAV-01,LAV-01,LAV-01"), ctx_of(held, sellable={"LAV-01": 3}), ON)
    assert not verdict.allowed
    assert any("LAV-01" in v and "3 free copies" in v for v in verdict.violations)
    # A card missing from `sellable` (never read, or all of it listed) has no free copy.
    assert not check(taller("LAV-01,LAV-01,LAV-01"), ctx_of(held, sellable={}), ON).allowed


def test_check_refuses_while_the_switch_is_off() -> None:
    verdict = check(taller("LAV-01,LAV-01,LAV-01"), ctx_of({"LAV-01": 4}), Guardrails())
    assert not verdict.allowed
    assert "taller_enabled = false" in verdict.violations


def test_check_refuses_when_the_hourly_count_was_not_read() -> None:
    verdict = check(taller("LAV-01,LAV-01,LAV-01"), ctx_of({"LAV-01": 4}, tallers_last_hour=None), ON)
    assert not verdict.allowed
    assert any("were not read" in v for v in verdict.violations)


def test_check_refuses_at_the_hourly_cap() -> None:
    cap = ON.max_taller_per_game_hour
    held = {"LAV-01": 4}
    assert check(taller("LAV-01,LAV-01,LAV-01"), ctx_of(held, tallers_last_hour=cap - 1), ON).allowed
    for n in (cap, cap + 3):
        verdict = check(taller("LAV-01,LAV-01,LAV-01"), ctx_of(held, tallers_last_hour=n), ON)
        assert not verdict.allowed
        assert any("max_taller_per_game_hour" in v for v in verdict.violations)
    zero = Guardrails(taller_enabled=True, max_taller_per_game_hour=0)
    assert not check(taller("LAV-01,LAV-01,LAV-01"), ctx_of(held), zero).allowed


def test_the_kill_switch_halts_a_conversion() -> None:
    verdict = check(taller("LAV-01,LAV-01,LAV-01"), ctx_of({"LAV-01": 4}, stops=("pause file x exists",)), ON)
    assert not verdict.allowed and verdict.halted
    assert "pause file x exists" in verdict.violations


@pytest.mark.parametrize("rarity", ["rare", "epic", "mixed", None, "pack"])
def test_check_refuses_any_rarity_but_common_or_uncommon(rarity: str | None) -> None:
    verdict = check(taller("LAV-09,LAV-09,LAV-09", rarity), ctx_of({"LAV-09": 5}), ON)
    assert not verdict.allowed
    assert any("only commons or uncommons" in v for v in verdict.violations)


@pytest.mark.parametrize("item", ["LAV-01,LAV-01", "LAV-01,LAV-01,LAV-01,LAV-01", "", " , , "])
def test_check_refuses_anything_but_three_copies(item: str) -> None:
    verdict = check(taller(item), ctx_of({"LAV-01": 9}), ON)
    assert not verdict.allowed
    assert any("takes 3 copies" in v for v in verdict.violations)


def test_a_tripped_taller_breaker_refuses_a_conversion() -> None:
    assert breaker_scope(Action("taller")) == "taller"
    held = {"LAV-01": 4}
    verdict = check(taller("LAV-01,LAV-01,LAV-01"), ctx_of(held, breakers=frozenset({"taller"})), ON)
    assert not verdict.allowed
    assert any("circuit breaker taller" in v for v in verdict.violations)
    other = check(taller("LAV-01,LAV-01,LAV-01"), ctx_of(held, breakers=frozenset({"board_accept"})), ON)
    assert other.allowed


def test_taller_action_carries_the_refs_and_rarity() -> None:
    plan = plan_from_ids(HAND, [2, 3, 11], [])
    action = taller_action(plan)
    assert action.kind == "taller" and action.item == "LAV-01,LAV-01,LAT-03" and action.rarity == "common"


# ---------------------------------------------------------------- ledger


def test_count_since_counts_rows_of_that_kind_and_prefix_after_t(tmp_path) -> None:
    ledger = Ledger(tmp_path / "l.jsonl")
    assert ledger.count_since("spend", 0.0, TALLER_ITEM) == 0  # no file yet
    ledger.record("spend", 1, 1.0, 0, TALLER_ITEM + "LAV-01,LAV-01,LAV-01")
    ledger.record("spend", 2, 2.5, 0, TALLER_ITEM + "LAT-03,LAT-03,LAT-03")
    ledger.record("spend", 3, 2.6, 10, "LAV-01")
    ledger.record("accept", 3, 2.6, 10, "LAV-01")
    ledger.record("spend", 4, 3.0, 0, TALLER_ITEM + "SAL-01,SAL-01,SAL-01")
    assert ledger.count_since("spend", 2.0, TALLER_ITEM) == 2
    assert ledger.count_since("spend", 2.5, TALLER_ITEM) == 1  # strictly after
    assert ledger.count_since("spend", 0.0, TALLER_ITEM) == 3
    assert ledger.count_since("spend", 3.0, TALLER_ITEM) == 0
    assert ledger.count_since("spend", 2.0) == 3
    assert ledger.spent_since(0.0) == 10 and not ledger.packs_since(0.0)  # a conversion row adds no spend, no pack


def test_taller_context_reads_the_hourly_count_and_the_free_copies(tmp_path) -> None:
    ledger = Ledger(tmp_path / "l.jsonl")
    ledger.record("spend", 1, 0.5, 0, TALLER_ITEM + "x")  # older than the hour
    ledger.record("spend", 2, 1.5, 0, TALLER_ITEM + "y")
    ledger.record("spend", 3, 2.0, 0, TALLER_ITEM + "z")
    me = me_of(*copies("LAV-01", 1, 4))
    ctx = taller_context(me, [our_ask(70, {"id": 4, "ref": "LAV-01"})], 10, 2.2, ledger, ON)
    assert ctx.tallers_last_hour == 2
    assert ctx.held["LAV-01"] == 4
    assert ctx.sellable == {"LAV-01": 3}


# ---------------------------------------------------------------- TallerResult


@pytest.mark.parametrize(
    ("body", "pulled"),
    [
        ({"ok": True, "card": {"id": 1, "kind": "card", "ref": "RET-06"}, "luck": "shown"}, ["RET-06"]),
        ({"cards": [{"ref": "MAL-02"}, {"ref": "LAV-01"}]}, ["LAV-01", "MAL-02"]),
        ({"result": {"asset": {"id": 7, "kind": "card", "ref": "SAL-03"}}}, ["SAL-03"]),
        ({"pulled": [{"ref": "LAT-04"}], "cooldown_ticks": 5, "unknown": {"x": [1, 2]}}, ["LAT-04"]),
        ({"asset": {"kind": "pack", "ref": "sobre_barrio"}}, []),
        ({"card": {"ref": 12}}, []),
        ({"ok": True, "message": "done"}, []),
        ({}, []),
    ],
)
def test_taller_result_finds_the_pulled_refs_wherever_they_are(body: dict[str, Any], pulled: list[str]) -> None:
    result = TallerResult.model_validate(body)
    assert result.pulled() == pulled
    assert result.model_dump() == body  # every unknown field is kept


# ---------------------------------------------------------------- convert


class FakeRec:
    """A decisions/executions recorder: `decide` returns a row id, `send` runs the call and returns its answer."""

    def __init__(self) -> None:
        self.decisions: list[dict[str, Any]] = []
        self.sends: list[tuple[int, int, str, dict[str, Any]]] = []

    def decide(self, tick: int, kind: str, line: str, **kw: Any) -> int:
        self.decisions.append({"tick": tick, "kind": kind, "line": line, **kw})
        return len(self.decisions)

    def send(self, did: int, tick: int, method: str, request: dict[str, Any], call: Any) -> Any:
        self.sends.append((did, tick, method, request))
        return call()


def _setup(tmp_path, me: dict[str, Any]) -> tuple[FakeTeam, FakeRec, Ledger]:
    return FakeTeam(me=me), FakeRec(), Ledger(tmp_path / "l.jsonl")


SPARE_ME = me_of(*copies("LAV-01", 1, 4))


def test_convert_dry_run_sends_nothing_and_books_nothing(tmp_path) -> None:
    team, rec, ledger = _setup(tmp_path, SPARE_ME)
    plan = plan_taller(SPARE_ME, [], ON)
    assert plan is not None
    out = convert(team, plan, ctx_of({"LAV-01": 4}), ON, rec, ledger, tick=10, t_hours=2.0, live=False)
    assert out.verdict.allowed and not out.sent and out.result is None
    assert team.sent == [] and rec.sends == []
    assert ledger.entries() == []
    assert len(rec.decisions) == 1
    row = rec.decisions[0]
    assert row["kind"] == TALLER_KIND and row["status"] == "approved" and row["chosen"] is True
    assert row["move"] == {"taller": plan.assets}
    assert out.decision_id == 1


def test_convert_live_sends_once_books_once_and_records_the_pull(tmp_path) -> None:
    team, rec, ledger = _setup(tmp_path, SPARE_ME)
    plan = plan_taller(SPARE_ME, [], ON)
    assert plan is not None
    out = convert(team, plan, ctx_of({"LAV-01": 4}), ON, rec, ledger, tick=10, t_hours=2.0, live=True)
    assert out.sent and out.verdict.allowed
    assert team.sent == [("taller", plan.assets)]
    assert rec.sends == [(1, 10, TALLER_KIND, {"assets": plan.assets})]
    rows = ledger.entries()
    assert len(rows) == 1
    assert (rows[0]["kind"], rows[0]["item"], rows[0]["price"]) == ("spend", TALLER_ITEM + "LAV-01,LAV-01,LAV-01", 0)
    assert ledger.count_since("spend", 1.0, TALLER_ITEM) == 1
    assert out.result is not None and out.result.pulled() == ["RET-06"]
    assert [d["kind"] for d in rec.decisions] == [TALLER_KIND, "taller_pulled"]
    assert rec.decisions[1]["inputs"]["pulled"] == ["RET-06"]
    assert rec.decisions[1]["chosen"] is False


def test_convert_live_refused_for_a_last_copy_sends_nothing(tmp_path) -> None:
    me = me_of(*copies("LAT-03", 1, 2), *copies("LAV-01", 10, 2))
    team, rec, ledger = _setup(tmp_path, me)
    plan = plan_from_ids(me, [1, 2, 11], [])
    ctx = ctx_of({"LAT-03": 2, "LAV-01": 2})
    out = convert(team, plan, ctx, ON, rec, ledger, tick=10, t_hours=2.0, live=True)
    assert not out.verdict.allowed and not out.sent and out.result is None
    assert any("LAT-03" in v for v in out.verdict.violations)
    assert team.sent == [] and rec.sends == []
    assert ledger.entries() == []
    assert len(rec.decisions) == 1
    assert rec.decisions[0]["status"] == "rejected" and rec.decisions[0]["chosen"] is False


def test_convert_live_under_the_kill_switch_sends_nothing(tmp_path) -> None:
    team, rec, ledger = _setup(tmp_path, SPARE_ME)
    plan = plan_taller(SPARE_ME, [], ON)
    assert plan is not None
    ctx = ctx_of({"LAV-01": 4}, stops=("pause file x exists",))
    out = convert(team, plan, ctx, ON, rec, ledger, tick=10, t_hours=2.0, live=True)
    assert out.verdict.halted and not out.sent
    assert team.sent == [] and ledger.entries() == []


def test_convert_books_the_ledger_row_before_the_send(tmp_path) -> None:
    class Boom(FakeTeam):
        def taller(self, assets):  # type: ignore[no-untyped-def]
            raise RuntimeError("network down")

    team, rec, ledger = Boom(me=SPARE_ME), FakeRec(), Ledger(tmp_path / "l.jsonl")
    plan = plan_taller(SPARE_ME, [], ON)
    assert plan is not None
    with pytest.raises(RuntimeError):
        convert(team, plan, ctx_of({"LAV-01": 4}), ON, rec, ledger, tick=10, t_hours=2.0, live=True)
    assert ledger.count_since("spend", 1.0, TALLER_ITEM) == 1  # over-counts the hourly cap, never under-counts it


def test_convert_with_no_answer_records_no_pull(tmp_path) -> None:
    class Silent(FakeRec):
        def send(self, did, tick, method, request, call):  # type: ignore[no-untyped-def]
            self.sends.append((did, tick, method, request))
            return None  # the recorder's view of a refused request

    team, rec, ledger = FakeTeam(me=SPARE_ME), Silent(), Ledger(tmp_path / "l.jsonl")
    plan = plan_taller(SPARE_ME, [], ON)
    assert plan is not None
    out = convert(team, plan, ctx_of({"LAV-01": 4}), ON, rec, ledger, tick=10, t_hours=2.0, live=True)
    assert out.sent and out.result is None
    assert [d["kind"] for d in rec.decisions] == [TALLER_KIND]
    assert len(ledger.entries()) == 1
