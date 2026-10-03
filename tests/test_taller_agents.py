"""El Taller in the agents (TL1): the taker converts at most one triple a tick, the maker keeps spare commons."""

from copy import deepcopy
from dataclasses import replace

from bazaar_agent import move_impact as mi
from bazaar_agent.agents.maker import Target, taller_stock
from bazaar_agent.agents.market import OpenOffer
from bazaar_agent.guardrails import Action, Guardrails, Ledger, check
from bazaar_agent.taller import TALLER_ITEM, TALLER_KIND, plan_taller, settling, taller_context
from tests.agent_fakes import TICK, FakePublic, FakeTeam, clock, rows
from tests.test_strategy import ME
from tests.test_taker import taker

SPARES = [{"id": 100 + i, "kind": "card", "ref": "LAT-03", "rarity": "common", "your_value": 1.2} for i in range(5)]


def me_with_spares():
    me = deepcopy(ME)
    me["assets"] = me["assets"] + deepcopy(SPARES)  # LAT-03 x7: six spares
    return me


def tallers(team):
    return [s for s in team.sent if s[0] == "taller"]


def test_the_taker_converts_one_triple_of_free_spares_per_tick(tmp_path):
    team = FakeTeam(me=me_with_spares())
    t, _, ledger = taker(tmp_path, team, FakePublic(), live=True, taller_enabled=True)
    t.on_tick(clock())
    sent = tallers(team)
    assert len(sent) == 1  # six spares, one conversion this tick
    assert len(sent[0][1]) == 3
    assert ledger.count_since("spend", 0.0, TALLER_ITEM) == 1
    kinds = [r.get("kind") for r in rows(tmp_path)]
    assert TALLER_KIND in kinds and "taller_pulled" in kinds
    assert team.reads.count("me") >= 2  # album first: planned on a fresh /me, read again after the pull


def test_the_taker_dry_run_records_the_plan_and_sends_nothing(tmp_path):
    team = FakeTeam(me=me_with_spares())
    t, _, ledger = taker(tmp_path, team, FakePublic(), live=False, taller_enabled=True)
    t.on_tick(clock())
    assert tallers(team) == []
    assert ledger.count_since("spend", 0.0, TALLER_ITEM) == 0
    assert any(r.get("kind") == TALLER_KIND for r in rows(tmp_path))


def test_the_taker_waits_while_a_duel_is_near_its_deadline(tmp_path):
    team = FakeTeam(me=me_with_spares())
    team.live_duels = [{"duel": 7, "status": "live", "deadline_tick": TICK + 2}]
    t, lines, _ = taker(tmp_path, team, FakePublic(), live=True, taller_enabled=True)
    t.on_tick(clock())
    assert tallers(team) == []
    assert any("El Taller waits" in line for line in lines)


def test_the_taker_stops_at_the_hourly_cap(tmp_path):
    team = FakeTeam(me=me_with_spares())
    t, _, ledger = taker(tmp_path, team, FakePublic(), live=True, taller_enabled=True, max_taller_per_game_hour=2)
    for _ in range(2):
        ledger.record("spend", TICK - 1, 1.4, 0, TALLER_ITEM + "LAT-03,LAT-03,LAT-03")
    t.on_tick(clock())
    assert tallers(team) == []
    assert "duels" not in team.reads  # the cap is read before any extra request


def test_the_taker_sends_nothing_with_the_switch_off_or_the_kill_switch_on(tmp_path):
    pause = tmp_path / "PAUSE"
    pause.touch()  # the kill switch: the live file's trading_enabled, or the pause file
    for rules in ({"taller_enabled": False}, {"taller_enabled": True, "pause_file": str(pause)}):
        team = FakeTeam(me=me_with_spares())
        t, _, _ = taker(tmp_path / str(len(rules)), team, FakePublic(), live=True, **rules)
        t.on_tick(clock())
        assert tallers(team) == []


def test_the_taker_never_feeds_a_card_held_once_or_twice(tmp_path):
    team = FakeTeam()  # ME: LAT-03 x2, one spare only
    t, _, _ = taker(tmp_path, team, FakePublic(), live=True, taller_enabled=True)
    t.on_tick(clock())
    assert tallers(team) == []
    assert "duels" not in team.reads  # no plan: no request at all


def target(side, ref, rarity, asset_id):
    return Target(side, ref, rarity, 10, asset_id, 1.0, 1.0, "test")


def offer(asset_id, ref="LAT-03"):
    return OpenOffer(1, "ask", ref, 10, "rastro", asset_id, 140, 90)


def test_the_maker_posts_no_new_ask_for_a_spare_common_while_the_taller_is_on():
    targets = [
        target("ask", "LAT-03", "common", 3),  # new: El Taller stock
        target("ask", "LAT-04", "common", 9),  # already asked: kept, so it is never cancelled
        target("ask", "LAT-09", "uncommon", 5),
        target("bid", "LAV-08", "uncommon", None),
    ]
    kept = taller_stock(targets, [offer(9, "LAT-04")], Guardrails(taller_enabled=True))
    assert [t.ref for t in kept] == ["LAT-04", "LAT-09", "LAV-08"]
    assert taller_stock(targets, [], Guardrails(taller_enabled=False)) == targets


def test_the_taker_waits_while_a_spare_copy_may_be_in_a_settling_accept(tmp_path):
    team = FakeTeam(me=me_with_spares())
    t, lines, ledger = taker(tmp_path, team, FakePublic(), live=True, taller_enabled=True)
    ledger.record("accept", TICK - 1, 1.49, 0, "team:77")  # a swap we accepted last tick: its copy is unnamed
    t.on_tick(clock())
    assert tallers(team) == []
    assert any("still settling" in line for line in lines)


def test_after_a_recent_conversion_the_maker_posts_no_new_uncommon_ask_either():
    targets = [target("ask", "LAT-09", "uncommon", 5), target("ask", "LAT-10", "rare", 6)]
    kept = taller_stock(targets, [], Guardrails(taller_enabled=True), converted=True)
    assert [t.ref for t in kept] == ["LAT-10"]


def me_lat():
    """LAT-03 x2 (ids 3, 4: one spare) and LAT-04 x3 (two spares): one common triple, [201, 202, 4]."""
    me = deepcopy(ME)
    me["assets"] += [
        {"id": 200 + i, "kind": "card", "ref": "LAT-04", "rarity": "common", "your_value": 1.0} for i in range(3)
    ]
    return me


def test_settling_names_sold_copies_and_holds_on_one_it_cannot_name(tmp_path):
    ledger = Ledger(tmp_path / "l.jsonl")
    ledger.record("accept", TICK - 1, 1.4, 0, "sell:4")
    ledger.record("accept", TICK, 1.5, 0, "duel:9")
    ledger.record("accept", TICK - 3, 1.3, 0, "team:78")  # older than UNSETTLED_TICKS: settled, /me shows it
    held, why = settling(ledger, TICK, me_lat())
    assert (held.listed, held.listed_refs, why) == (frozenset({4}), ("LAT-03",), None)
    ledger.record("accept", TICK, 1.5, 0, "team:77")
    assert "team:77" in (settling(ledger, TICK, me_lat())[1] or "")


def test_a_copy_an_accept_is_still_handing_over_is_never_fed_in(tmp_path):
    ledger, me, rules = Ledger(tmp_path / "l.jsonl"), me_lat(), Guardrails(taller_enabled=True)
    assert plan_taller(me, [], rules).assets == [201, 202, 4]
    ledger.record("accept", TICK, 1.5, 0, "sell:4")  # we sold LAT-03 #4 into a bid this tick
    held, _ = settling(ledger, TICK, me)
    assert plan_taller(me, [], rules, None, held) is None
    ctx = replace(taller_context(me, [], TICK, 1.5, ledger, rules), breakers=frozenset(), stops=())
    verdict = check(Action("taller", "LAT-03,LAT-04,LAT-04", "common"), ctx, rules)
    assert not verdict.allowed and "LAT-03" in str(verdict)  # the guardrails refuse it on every path


class DeskPostsMidTick(FakeTeam):
    """/api/me/offers gains the team desk's swap offer (it gives LAT-03 #3) after the tick's first read."""

    def my_offers(self):
        out = super().my_offers()
        if self.reads.count("my_offers") > 1:
            give = {"cash": 0, "assets": [{"id": 3, "kind": "card", "ref": "LAT-03"}]}
            swap = {"id": 900, "maker": "t01", "to": "t07", "thread": 77, "status": "open", "give": give}
            out["offers"].append({**swap, "want": {"cash": 0, "cards": ["LAT-09"]}})
        return out


def test_the_taker_plans_on_offers_read_again_so_a_swap_posted_this_tick_keeps_its_copy(tmp_path):
    control = FakeTeam(me=me_lat())
    t, _, _ = taker(tmp_path / "a", control, FakePublic(), live=True, taller_enabled=True)
    t.on_tick(clock())
    assert tallers(control) == [("taller", [201, 202, 4])]
    team = DeskPostsMidTick(me=me_lat())
    t, _, _ = taker(tmp_path / "b", team, FakePublic(), live=True, taller_enabled=True)
    t.on_tick(clock())
    assert tallers(team) == []  # LAT-03 #3 is promised to t07: #4 is our last free copy


def test_the_score_impact_guard_prices_each_input_at_zero(tmp_path):
    me = deepcopy(ME)
    me["assets"] += [
        {"id": 300 + i, "kind": "card", "ref": "LAV-02", "rarity": "common", "your_value": 16.0} for i in range(4)
    ]
    rules = Guardrails(taller_enabled=True, max_score_loss_per_move=0.2)
    base = replace(
        taller_context(me, [], TICK, 1.5, Ledger(tmp_path / "l.jsonl"), rules), breakers=frozenset(), stops=()
    )
    action = Action("taller", "LAV-02,LAV-02,LAV-02", "common")
    from_packs = mi.Facts("t01", {})
    assert check(action, replace(base, impact=from_packs), rules).allowed  # no team trade: no score moves
    bought = mi.Facts("t01", {300 + i: mi.Origin("team", "t05", 20, 50) for i in range(4)})
    verdict = check(action, replace(base, impact=bought), rules)
    assert not verdict.allowed and "max_score_loss_per_move" in str(verdict)


class CostsCash(FakeTeam):
    def taller(self, assets):
        self._me["cash"] -= 5
        return super().taller(assets)


def test_a_conversion_that_takes_cash_stops_the_taker_converting(tmp_path):
    team = CostsCash(me=me_with_spares())
    t, lines, _ = taker(tmp_path, team, FakePublic(), live=True, taller_enabled=True)
    t.on_tick(clock())
    t.on_tick(clock(tick=TICK + 1))
    assert len(tallers(team)) == 1
    assert any("El Taller cash" in line for line in lines)


def test_a_conversion_row_passes_the_shared_ledgers_kind_check_and_every_process_counts_it(tmp_path):
    from bazaar_agent.ledger_pg import PgLedger
    from tests.pg_fakes import FakePostgres

    pg = FakePostgres(tmp_path / "shared.db")  # the table's check: kind in ('spend', 'accept', 'listing')
    taker_ledger, cli_ledger = PgLedger(pg.connect, "taker"), PgLedger(pg.connect, "taller")
    cli_ledger.record("spend", TICK, 1.5, 0, TALLER_ITEM + "LAT-03,LAT-03,LAT-03")
    assert taker_ledger.count_since("spend", 1.0, TALLER_ITEM) == 1
    assert taker_ledger.spent_since(1.0) == 0 and not taker_ledger.packs_since(1.0)
