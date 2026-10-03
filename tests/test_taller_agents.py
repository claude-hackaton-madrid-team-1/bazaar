"""The Workshop's hardening (TL1, review of #236): fresh /me and offers, accepts still settling, the shared hourly
cap, the duel guard, a cash drop, commons and uncommons only, and the maker leaving spare commons to it."""

from collections import Counter
from dataclasses import replace

from bazaar_agent import move_impact as mi
from bazaar_agent.agents import taller as tl
from bazaar_agent.agents.maker import Target, taller_stock
from bazaar_agent.agents.market import OpenOffer
from bazaar_agent.guardrails import Guardrails, Ledger, check
from tests.agent_fakes import parts
from tests.test_taller import CATALOG, DEALERS, LEVELS, SPARES, News, Team, card, crafts, me, run_taker

TICK = 100
FREE = [a for a in SPARES["assets"] if a["ref"] != "LAV-07"]  # LAV-01 x3 (#1-3), SAL-01 x2 (#4-5): three spares


class NoLedger:
    """An empty ledger: nothing booked, nothing spent (the guardrail context reads these)."""

    def count_since(self, kind, t_hours, prefix=""):
        return 0

    def spent_since(self, t_hours, prefix=""):
        return 0

    def packs_since(self, t_hours):
        return Counter()

    def accepts_in_tick(self, tick):
        return 0


def ctx_for(ours, rules, busy=(), hold=None, ledger=None):
    ctx = tl.craft_context(ours, busy, hold, TICK, 1.5, ledger or NoLedger(), rules)
    return replace(ctx, breakers=frozenset(), stops=())


# ---------------------------------------------------------------- copies that are not free


def test_settling_names_sold_copies_takes_one_for_a_plain_ref_and_holds_on_an_unnamed_one(tmp_path):
    ledger = Ledger(tmp_path / "l.jsonl")
    ledger.record("accept", TICK - 1, 1.4, 0, "sell:5")  # a bid we sold SAL-01 #5 into
    ledger.record("accept", TICK, 1.5, 9, "LAV-01")  # a dealer sell thread for LAV-01 (the item is the ref)
    ledger.record("accept", TICK, 1.5, 0, "duel:9")
    ledger.record("accept", TICK - 3, 1.3, 0, "team:78")  # older than UNSETTLED_TICKS: settled, /me shows it
    busy, why = tl.settling(ledger, TICK, me(*FREE))
    assert busy == frozenset({5, 1}) and why is None  # LAV-01's cheapest copy (#1 on a value tie)
    ledger.record("accept", TICK, 1.5, 0, "team:77")
    assert "team:77" in (tl.settling(ledger, TICK, me(*FREE))[1] or "")


def test_a_copy_an_accept_is_still_handing_over_is_never_crafted_on_any_path(tmp_path):
    ledger, rules, ours = Ledger(tmp_path / "l.jsonl"), Guardrails(taller_enabled=True), me(*FREE)
    assert tl.rank_triples(ours, CATALOG, DEALERS)[0].asset_ids == [5, 2, 3]
    ledger.record("accept", TICK, 1.5, 0, "sell:5")  # SAL-01 #5 sold into a bid this tick
    busy, hold = tl.busy_copies(ours, [], ledger, TICK)
    assert tl.rank_triples(ours, CATALOG, DEALERS, busy) == []  # SAL-01 #4 is now our last free copy
    verdict = check(tl.craft_action(tl.triple_from_ids(ours, CATALOG, [4, 2, 3])), ctx_for(ours, rules, busy), rules)
    assert not verdict.allowed and "SAL-01" in str(verdict)  # the CLI's path refuses it too
    ledger.record("accept", TICK, 1.5, 0, "team:77")
    busy, hold = tl.busy_copies(ours, [], ledger, TICK)
    held = check(tl.craft_action(tl.triple_from_ids(ours, CATALOG, [1, 2, 4])), ctx_for(ours, rules, busy, hold), rules)
    assert not held.allowed and "team:77" in str(held)


class DeskPostsMidTick(Team):
    """/api/me/offers gains the team desk's swap offer (it gives SAL-01 #4) after the tick's first read."""

    def my_offers(self):
        out = super().my_offers()
        if self.reads.count("my_offers") > 1:
            give = {"cash": 0, "assets": [{"id": 4, "kind": "card", "ref": "SAL-01"}]}
            swap = {"id": 900, "maker": "t01", "to": "t07", "thread": 77, "status": "open", "give": give}
            out["offers"].append({**swap, "want": {"cash": 0, "cards": ["LAV-06"]}})
        return out


def test_the_taker_crafts_on_offers_read_again_so_a_swap_posted_this_tick_keeps_its_copy(tmp_path):
    team, _ = run_taker(tmp_path, news=News(LEVELS), team=DeskPostsMidTick(me=me(*FREE)), taller_enabled=True)
    assert crafts(team) == []  # SAL-01 #4 is promised to t07: #5 is our last free copy, no triple left


# ---------------------------------------------------------------- the step's guards


def test_the_taker_waits_while_a_duel_is_near_its_deadline(tmp_path):
    team = Team(me=me(*FREE))
    team.live_duels = [{"duel": 7, "status": "live", "deadline_tick": TICK + 2}]
    team, lines = run_taker(tmp_path, news=News(LEVELS), team=team, taller_enabled=True)
    assert crafts(team) == []
    assert any("Workshop waits" in line and "duel 7" in line for line in lines)


def test_a_dry_run_waits_ten_ticks_before_it_plans_again(tmp_path):
    team, _ = run_taker(tmp_path, news=News(LEVELS), live=False, ticks=11, taller_enabled=True)
    assert crafts(team) == [] and team.reads.count("duels") == 2  # ticks 100 and 110


class CostsCash(Team):
    def call(self, method, path, body=None):
        self._me["cash"] -= 5
        return super().call(method, path, body)


def test_a_craft_that_takes_cash_stops_the_taker_crafting(tmp_path):
    team = CostsCash(me=me(*FREE))
    team, lines = run_taker(
        tmp_path, news=News(LEVELS), team=team, ticks=2, taller_enabled=True, max_taller_per_game_hour=5
    )
    assert len(crafts(team)) == 1
    assert any("Workshop cash 100 -> 95" in line for line in lines)


class OddAnswer(Team):
    def call(self, method, path, body=None):
        self.sent.append(("call", method, path, body))
        return ["not", "an", "object"]


def test_an_answer_of_any_shape_is_recorded_and_never_raises(tmp_path):
    team, lines = run_taker(tmp_path, news=News(LEVELS), team=OddAnswer(me=me(*FREE)), taller_enabled=True)
    assert len(crafts(team)) == 1 and any("Workshop crafted" in line for line in lines)


def test_rares_never_go_in_and_cards_held_more_than_max_copies_kept_go_first():
    rares = me(card(1, "LAV-09", 5.0), card(2, "LAV-09", 5.0), card(3, "LAV-09", 5.0), card(4, "LAV-09", 5.0))
    assert tl.rank_triples(rares, CATALOG, DEALERS) == []
    rules = Guardrails(taller_enabled=True)
    verdict = check(tl.craft_action(tl.triple_from_ids(rares, CATALOG, [2, 3, 4])), ctx_for(rares, rules), rules)
    assert "commons or uncommons only" in str(verdict)
    assert tl.rank_triples(me(*FREE), CATALOG, DEALERS, crowded=2)[0].asset_ids == [2, 3, 5]  # LAV-01 x3 first


def test_the_score_impact_rule_prices_each_given_copy_at_zero():
    ours, rules = me(*FREE), Guardrails(taller_enabled=True, max_score_loss_per_move=0.2)
    action = tl.craft_action(tl.triple_from_ids(ours, CATALOG, [2, 3, 5]))
    assert check(action, replace(ctx_for(ours, rules), impact=mi.Facts("t01", {})), rules).allowed  # pack copies
    bought = mi.Facts("t01", {a: mi.Origin("team", "t05", 20, 50) for a in (2, 3, 5)})
    assert "max_score_loss_per_move" in str(check(action, replace(ctx_for(ours, rules), impact=bought), rules))


# ---------------------------------------------------------------- one hourly cap for every process


def test_a_craft_row_passes_the_shared_ledgers_kind_check_and_every_process_counts_it(tmp_path):
    from bazaar_agent.ledger_pg import PgLedger
    from tests.pg_fakes import FakePostgres

    pg = FakePostgres(tmp_path / "shared.db")  # the table's check: kind in ('spend', 'accept', 'listing')
    taker_ledger, cli_ledger = PgLedger(pg.connect, "taker"), PgLedger(pg.connect, "taller")
    cli_ledger.record("spend", TICK, 1.5, 0, tl.TALLER_ITEM + "LAV-01,LAV-01,SAL-01")
    assert taker_ledger.count_since("spend", 1.0, tl.TALLER_ITEM) == 1
    assert taker_ledger.spent_since(1.0) == 0 and not taker_ledger.packs_since(1.0)


def test_a_craft_another_process_booked_this_hour_stops_the_taker_before_any_request(tmp_path):
    parts(tmp_path)["ledger"].record("spend", TICK - 5, 1.2, 0, tl.TALLER_ITEM + "LAV-01,LAV-01,SAL-01")
    team, _ = run_taker(tmp_path, news=News(LEVELS), taller_enabled=True, max_taller_per_game_hour=1)
    assert crafts(team) == [] and "duels" not in team.reads


# ---------------------------------------------------------------- the maker leaves spare commons to the Workshop


def target(side, ref, rarity, asset_id):
    return Target(side, ref, rarity, 10, asset_id, 1.0, 1.0, "test")


def test_the_maker_posts_no_new_ask_for_a_spare_common_and_no_uncommon_right_after_a_craft():
    targets = [
        target("ask", "LAT-03", "common", 3),  # new: Workshop stock
        target("ask", "LAT-04", "common", 9),  # already asked: kept, so it is never cancelled
        target("ask", "LAT-09", "uncommon", 5),
        target("ask", "LAT-10", "rare", 6),
        target("bid", "LAV-08", "uncommon", None),
    ]
    standing = [OpenOffer(1, "ask", "LAT-04", 10, "rastro", 9, 140, 90)]
    on = Guardrails(taller_enabled=True)
    assert [t.ref for t in taller_stock(targets, standing, on)] == ["LAT-04", "LAT-09", "LAT-10", "LAV-08"]
    assert [t.ref for t in taller_stock(targets, standing, on, converted=True)] == ["LAT-04", "LAT-10", "LAV-08"]
    assert taller_stock(targets, [], Guardrails(taller_enabled=False)) == targets
