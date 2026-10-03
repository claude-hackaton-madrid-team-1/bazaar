"""Buy targets (`buy_targets.py`): a human's buy approval of an off-page card becomes a card the agents pursue.

The maker's bid ladder and the taker's accepts through the real agents' tick on fakes (no network, no database), the
hard rule that an off-page buy is always strictly below our value (no approval lifts it), the MCP `approve` answer,
targets that end (revoked, expired, held), page cards unchanged, and the bid's exact structure settled in the
simulator. Every value here is synthetic: LAV-11 (the test catalog's epic, book 180) is worth 200 to us.
"""

from copy import deepcopy

import pytest
from pydantic import ValidationError

from bazaar_agent import buy_targets
from bazaar_agent.agents.seller import bid_listing
from bazaar_agent.approvals import Approval, ApprovalBook
from bazaar_agent.buy_targets import BuyTarget, TargetBoard, TargetRow
from bazaar_agent.guardrails import Action, Context, Guardrails, check
from bazaar_agent.official_values import OfficialValues
from bazaar_sim import market, scoring
from tests.agent_fakes import FakePublic, ask, bid, clock, rows
from tests.simkit import manual_world
from tests.test_official_value_agents import ValuedTeam, maker, taker
from tests.test_runtime_human_tools import RULES as HUMAN_RULES
from tests.test_runtime_human_tools import Store, Valued, human_backend, run
from tests.test_strategy import ME

pytestmark = pytest.mark.official_values  # the real official value cap (tests/conftest.py)

EPIC, VALUE = "LAV-11", 200.0
ON = {
    "max_price_epic": 240,
    "off_page_min_surplus": 10,
    "buy_targets_enabled": True,
    "cash_floor": 5,
    "max_spend_per_game_hour": 250,
}
ROOMY = {**ON, "max_spend_per_game_hour": 1000}  # the ladder tests: the hourly spend cap is tested on its own
CEILING, FIRST = 190, 143  # min(approved 230, max_price_epic 240, 200 - 10); ceil(0.75 × 190)
LADDER = [143, 152, 161, 171, 180, 190]  # one step every 6 ticks from the grant at tick 100
TICK = 100


class FakeTargets(TargetBoard):
    """The active buy approvals as the agents would read them this tick (None: the read failed)."""

    def __init__(self, found=()):
        super().__init__(None)
        self.found: tuple[TargetRow, ...] | None = tuple(found)
        self.reads = 0

    def read(self, tick):
        self.reads += 1
        return self.found


@pytest.fixture
def targets():
    fake = FakeTargets()
    old = buy_targets.install(fake)
    yield fake
    if old is None:
        buy_targets._BOARD.pop("board", None)
    else:
        buy_targets.install(old)


def order(card=EPIC, max_price=230, until=TICK + 240, granted=TICK):
    return TargetRow(card, max_price, until, "human:mcp", granted)


def team(**kw):
    return ValuedTeam(values={EPIC: VALUE}, **kw)


def at(t, tick):
    t.now = clock(tick=tick)
    return t.now


def epic_posts(t):
    return [s for s in t.sent if s[0] == "list_offer" and s[2] == {"cards": [EPIC]}]


def ctx(**kw):
    values = OfficialValues(lambda card: {"card": card, "your_value": VALUE})
    base = {"cash": 400, "held": {}, "tick": TICK, "t_hours": 1.5, "values": values, "breakers": frozenset()}
    return Context(**(base | kw))


# ---------------------------------------------------------------- the ladder and its ceiling


def test_the_ceiling_is_always_strictly_below_our_value():
    rules = Guardrails(**ON)
    for official in (1.0, 10.0, 10.5, 11.0, 99.99, 150.0, 200.0, 249.5, 1000.0):
        for surplus in (1, 3, 10):
            r = rules.model_copy(update={"off_page_min_surplus": surplus})
            top = buy_targets.ceiling(BuyTarget(EPIC, "epic", 1000, 999, TICK), official, r)
            assert top is None or top <= official - surplus < official, (official, surplus, top)
    t = BuyTarget(EPIC, "epic", 230, 999, TICK)
    assert buy_targets.ceiling(t, VALUE, rules) == CEILING
    assert buy_targets.ceiling(t, None, rules) is None  # unread value: no ceiling, no buy
    assert buy_targets.ceiling(replace_max(t, 120), VALUE, rules) == 120  # the human's max binds below
    assert buy_targets.ceiling(t, VALUE, rules.model_copy(update={"max_price_epic": 0})) is None
    assert buy_targets.ceiling(BuyTarget("LAV-12", "legendary", 230, 999, TICK), VALUE, rules) is None


def replace_max(t, price):
    return BuyTarget(t.card, t.rarity, price, t.until_tick, t.start_tick, t.by)


def test_the_ladder_climbs_in_even_steps_from_the_grant_and_holds_at_the_ceiling():
    rules = Guardrails(**ON)
    t = BuyTarget(EPIC, "epic", 230, 999, TICK)
    prices = [buy_targets.ladder_price(t, CEILING, TICK + 6 * k, rules) for k in range(8)]
    assert prices == LADDER + [CEILING, CEILING]
    assert buy_targets.ladder_price(t, CEILING, TICK + 5, rules) == FIRST  # a step every 6 ticks, not before
    assert buy_targets.ladder_price(t, CEILING, TICK - 3, rules) == FIRST
    assert all(a < b for a, b in zip(LADDER, LADDER[1:], strict=False))
    assert all(b - a >= round(b * 0.05) for a, b in zip(LADDER, LADDER[1:], strict=False))  # the maker reprices


def test_the_strict_rule_cannot_be_configured_away():
    with pytest.raises(ValidationError):
        Guardrails(off_page_min_surplus=0)
    assert Guardrails().max_price_for("epic") is None  # shipped without the file: no epic is ever bought
    assert Guardrails(max_price_epic=240).max_price_for("legendary") is None


# ---------------------------------------------------------------- guardrails.check: no approval lifts it


@pytest.mark.parametrize("kind", ["bid", "accept_buy", "buy"])
def test_an_epic_buy_at_or_above_our_value_minus_the_surplus_is_refused(kind):
    rules = Guardrails(**ON)
    assert check(Action(kind, EPIC, "epic", CEILING), ctx(), rules).allowed
    for price in (CEILING + 1, 199, 200, 230):
        verdict = check(Action(kind, EPIC, "epic", price), ctx(), rules)
        assert not verdict.allowed and any("off_page_min_surplus 10" in v for v in verdict.violations), verdict


def test_an_approval_never_lifts_the_value_rule():
    rules = Guardrails(**ON, human_approval_above=1)
    book = ApprovalBook({(EPIC, "buy"): Approval(EPIC, "buy", 1000, None, 10_000)})
    assert check(Action("bid", EPIC, "epic", CEILING), ctx(approvals=book), rules).allowed
    for price in (CEILING + 1, 200, 239):
        verdict = check(Action("bid", EPIC, "epic", price), ctx(approvals=book), rules)
        assert not verdict.allowed and "off_page_min_surplus" in str(verdict)
    over_cap = check(Action("bid", EPIC, "epic", 241), ctx(approvals=book, values=None), rules)
    assert "max_price_epic 240" in str(over_cap)


def test_a_page_card_keeps_the_plain_official_value_margin():
    rules = Guardrails(**ON)
    assert check(Action("bid", "LAV-09", "rare", 80), ctx(), Guardrails(**ON, max_price_rare=200)).allowed
    assert rules.value_margin_for("rare") == 0.0 and rules.value_margin_for("epic") == 10.0


# ---------------------------------------------------------------- who is a target


def test_only_an_off_page_card_we_do_not_hold_with_an_active_approval_is_a_target(targets):
    rules, catalog = Guardrails(**ON), FakePublic().catalog()
    targets.found = (order(), order("LAV-09"), order("LAV-08", until=TICK))
    book = buy_targets.TargetBook()
    (t,) = book.active(rules, TICK, catalog, {})
    assert (t.card, t.rarity, t.max_price, t.start_tick) == (EPIC, "epic", 230, TICK)
    assert book.active(rules, TICK, catalog, {EPIC: 1}) == []  # held: done
    assert book.active(rules.model_copy(update={"buy_targets_enabled": False}), TICK, catalog, {}) == []
    targets.found = None  # an unreadable table: fail closed
    assert book.active(rules, TICK, catalog, {}) == []


def test_without_a_grant_row_the_ladder_starts_when_this_process_first_saw_it(targets):
    rules, catalog = Guardrails(**ON), FakePublic().catalog()
    targets.found = (order(granted=None),)
    book = buy_targets.TargetBook()
    assert book.active(rules, 120, catalog, {})[0].start_tick == 120
    assert book.active(rules, 130, catalog, {})[0].start_tick == 120
    targets.found = (order(granted=None, until=TICK + 300),)  # approved again: a new ladder
    assert book.active(rules, 140, catalog, {})[0].start_tick == 140


def test_the_board_reads_the_buy_approvals_and_their_grant_tick():
    class Conn:
        def __init__(self):
            self.args = None

        def execute(self, sql, args):
            self.args = (sql, args)
            return self

        def fetchall(self):
            return [("LAV-11", 230, 340, "human:mcp", 100), ("SAL-11", 150, 300, "", None)]

    conn = Conn()
    found = TargetBoard(None).query(conn, TICK)  # type: ignore[arg-type]
    assert found == (TargetRow("LAV-11", 230, 340, "human:mcp", 100), TargetRow("SAL-11", 150, 300, "", None))
    sql, args = conn.args
    assert args == (TICK, TICK) and "side = 'buy'" in sql and "approval_granted" in sql
    assert TargetBoard(None).read(TICK) is None and TargetBoard(None).no_table() == ()


# ---------------------------------------------------------------- the maker's bid ladder


def test_the_maker_bids_for_a_target_and_steps_it_up_to_the_ceiling(tmp_path, targets):
    targets.found = (order(),)
    t = team()
    m, _ = maker(tmp_path, t, **ROOMY)
    m.on_tick(at(t, TICK))
    assert epic_posts(t) == [("list_offer", {"cash": FIRST}, {"cards": [EPIC]}, "rastro")]
    (row,) = [r for r in rows(tmp_path) if r.get("kind") == "post_bid" and r["inputs"]["ref"] == EPIC]
    assert row["status"] == "approved" and "buy target LAV-11 (epic) approved by human:mcp" in row["reason"]
    assert "ladder step 0/5" in row["reason"] and "public" not in str(row["move"].get("to"))
    price = FIRST
    for k, tick in enumerate(range(TICK + 6, TICK + 31, 6), start=1):
        t.sent.clear()
        t.offers = [bid(4000 + k, EPIC, price, created=tick - 6, expires=tick + 30)]
        m.on_tick(at(t, tick))
        assert ("cancel", 4000 + k) in t.sent, (tick, t.sent)
        assert epic_posts(t) == [("list_offer", {"cash": LADDER[k]}, {"cards": [EPIC]}, "rastro")]
        price = LADDER[k]
    t.sent.clear()
    t.offers = [bid(4100, EPIC, CEILING, created=TICK + 36, expires=TICK + 80)]
    m.on_tick(at(t, TICK + 60))  # at the ceiling: the bid holds
    assert epic_posts(t) == [] and ("cancel", 4100) not in t.sent


@pytest.mark.parametrize(
    "why",
    ["revoked", "expired", "held", "disabled", "unreadable", "value fell"],
)
def test_a_target_that_ends_cancels_its_bid_and_posts_none(tmp_path, targets, why):
    targets.found = (order(),)  # active until tick 340: each case ends it its own way at tick 120
    me = deepcopy(ME)
    rules = dict(ROOMY)
    t = team(me=me)
    if why == "revoked":
        targets.found = ()
    elif why == "expired":
        targets.found = (order(until=TICK + 20),)
    elif why == "held":
        me["assets"] = [*me["assets"], {"id": 77, "kind": "card", "ref": EPIC, "rarity": "epic", "your_value": 50.0}]
        t = team(me=me)
    elif why == "disabled":
        rules["buy_targets_enabled"] = False
    elif why == "unreadable":
        targets.found = None
    else:
        t.values[EPIC] = 195.0  # our standing 190 is now above 195 - 10: re-capped at once
    t.offers = [bid(4200, EPIC, CEILING, created=TICK + 10, expires=TICK + 60)]
    m, lines = maker(tmp_path, t, **rules)
    m.on_tick(at(t, TICK + 20))
    cancel = next(line for line in lines if "cancel bid 4200" in line)
    assert ("official value 195 - margin 10" if why == "value fell" else "no longer a buy target") in cancel, cancel
    assert ("cancel", 4200) in t.sent and epic_posts(t) == [], t.sent  # never re-bid in the tick it was cancelled
    if why == "value fell":  # the next tick bids again, under the new ceiling 195 - 10
        t.sent.clear()
        t.offers = []
        m.on_tick(at(t, TICK + 21))
        ((_, give, _, _),) = epic_posts(t)
        assert give["cash"] <= 185


def test_the_hourly_spend_cap_still_binds_a_target_bid(tmp_path, targets):
    targets.found = (order(),)
    t = team()
    m, lines = maker(tmp_path, t, **{**ON, "max_spend_per_game_hour": 150})
    m.on_tick(at(t, TICK))  # the first bid 143 fits under 150
    assert epic_posts(t) == [("list_offer", {"cash": FIRST}, {"cards": [EPIC]}, "rastro")]
    t.sent.clear()
    t.offers = [bid(4400, EPIC, FIRST, created=TICK, expires=TICK + 60)]
    m.on_tick(at(t, TICK + 6))  # 152 would pass the cap: the bid stays at 143
    assert epic_posts(t) == [] and ("cancel", 4400) not in t.sent
    assert any("keep LAV-11 at 143: denied" in line and "max_spend_per_game_hour 150" in line for line in lines)


def test_the_maker_posts_no_target_bid_without_an_approval_and_page_approvals_change_nothing(tmp_path, targets):
    sent = {}
    for name, found in (("none", ()), ("page", (order("LAV-09"), order("LAV-08"))), ("off", None)):
        targets.found = found
        t = team()
        rules = ON if name != "off" else {**ON, "buy_targets_enabled": False}
        m, _ = maker(tmp_path / name, t, **rules)
        m.on_tick(at(t, TICK))
        sent[name] = t.sent
    assert sent["none"] == sent["page"] == sent["off"] and not any(EPIC in str(s) for s in sent["none"])


# ---------------------------------------------------------------- the taker takes an ask within the ceiling


def test_the_taker_takes_a_target_ask_at_the_ceiling_and_withdraws_our_bid(tmp_path, targets):
    targets.found = (order(),)
    t = team(offers=[bid(4300, EPIC, 161, created=TICK - 2)])
    tk, lines = taker(tmp_path, t, board(ask(1, EPIC, 180, maker="t07")), live=True, **ON)  # 180 + fee 10 = 190
    tk.on_tick(at(t, TICK))
    assert ("accept", 1) in t.sent and ("cancel", 4300) in t.sent
    (row,) = [r for r in rows(tmp_path) if r.get("kind") == "accept_ask" and r.get("chosen")]
    assert row["move"] == {"accept": 1, "price": CEILING} and "buy target LAV-11" in row["reason"]


@pytest.mark.parametrize("price", [181, 199, 230])
def test_the_taker_never_takes_a_target_ask_above_the_ceiling(tmp_path, targets, price):
    targets.found = (order(),)
    t = team()
    taker(tmp_path, t, board(ask(1, EPIC, price, maker="t07")), live=True, **ON)[0].on_tick(at(t, TICK))
    assert ("accept", 1) not in t.sent


def test_without_a_target_the_taker_ignores_an_epic_ask_and_page_cards_are_unchanged(tmp_path, targets):
    offers = (ask(1, EPIC, 100, maker="t07"), ask(2, "LAV-02", 10))
    sent = {}
    for name, found in (("none", ()), ("page", (order("LAV-02", max_price=12),)), ("off", None)):
        targets.found = found
        t = ValuedTeam(values={EPIC: VALUE, "LAV-02": 30.0})
        rules = ON if name != "off" else {**ON, "buy_targets_enabled": False}
        taker(tmp_path / name, t, board(*offers), live=True, **rules)[0].on_tick(at(t, TICK))
        sent[name] = t.sent
    assert sent["none"] == sent["page"] == sent["off"] == [("accept", 2)]


def board(*offers):
    return FakePublic(boards={"rastro": list(offers)})


# ---------------------------------------------------------------- the human's order through the MCP


def human_rules(**kw):
    return HUMAN_RULES.model_copy(update={**ON, "max_spend_per_game_hour": 250, **kw})


def test_an_approved_epic_buy_answers_the_ladder_the_agents_will_run(tmp_path):
    store = Store()
    b = human_backend(tmp_path, values={EPIC: VALUE}, rules=human_rules())
    ok, failed = run("approve", b, store, {"card": EPIC, "side": "buy", "price": CEILING, "ttl_ticks": 120})
    assert not failed and ok["status"] == "approved" and ok["notes"] == []
    target = ok["target"]
    assert (target["pursued"], target["first_bid"], target["ceiling"], target["until_tick"]) == (
        True,
        FIRST,
        CEILING,
        TICK + 120,
    )
    assert store.records[0][0] == "approval_granted"


@pytest.mark.parametrize(
    ("price", "rules", "reason"),
    [
        (191, {}, "official value 200 (off_page_min_surplus 10): an off-page card is bought strictly below"),
        (200, {}, "official value 200 (off_page_min_surplus 10)"),
        (241, {}, "max_price_epic 240: an approval never lifts it"),
        (150, {"max_price_epic": 0}, "no max_price for rarity epic: buying it is not allowed"),
    ],
    ids=["above-ceiling", "at-value", "above-cap", "no-cap"],
)
def test_an_epic_buy_at_or_above_our_value_cannot_be_approved(tmp_path, price, rules, reason):
    store = Store()
    b = human_backend(tmp_path, values={EPIC: VALUE}, rules=human_rules(**rules))
    answer, failed = run("approve", b, store, {"card": EPIC, "side": "buy", "price": price})
    assert not failed and answer["status"] == "refused" and store.approvals == {}
    assert any(r.startswith(reason) for r in answer["reasons"]), answer["reasons"]


def test_an_epic_we_hold_is_not_approved_and_a_disabled_desk_says_nobody_pursues_it(tmp_path):
    me = deepcopy(ME)
    me["assets"] = [*me["assets"], {"id": 77, "kind": "card", "ref": EPIC, "rarity": "epic", "your_value": 50.0}]
    held = human_backend(tmp_path / "held", rules=human_rules(), team=Valued({EPIC: VALUE}, me=me))
    answer, _ = run("approve", held, Store(), {"card": EPIC, "side": "buy", "price": 150})
    assert answer["status"] == "refused" and any("we already hold LAV-11" in r for r in answer["reasons"])
    off = human_backend(tmp_path / "off", values={EPIC: VALUE}, rules=human_rules(buy_targets_enabled=False))
    ok, _ = run("approve", off, Store(), {"card": EPIC, "side": "buy", "price": 150})
    assert ok["status"] == "approved" and ok["target"]["pursued"] is False


# ---------------------------------------------------------------- the bid's structure, settled by the simulator


def test_the_maker_bid_structure_buys_an_epic_from_a_team_below_our_value_in_the_simulator():
    m = manual_world()
    w, us, them = m.world, "t01", "t02"
    w.mint("LAT-11", them, "test: a team holds the epic")
    official = float(scoring.value_view(w, us, "LAT-11")["your_value"])
    rules = Guardrails(**ON)
    t = BuyTarget("LAT-11", "epic", 1000, w.tick + 100, w.tick)
    top = buy_targets.ceiling(t, official, rules)
    assert top is not None and top < official
    price = buy_targets.ladder_price(t, top, w.tick, rules)
    listing = bid_listing("LAT-11", "epic", price)
    offer = market.offer_from_input(w, us, {"give": listing.give, "want": listing.want})
    cash = w.team(us).cash
    market.accept(w, them, offer.id, {})  # the holder takes our bid: it pays the venue fee
    m.step()
    assert w.held_counts(us)["LAT-11"] == 1 and w.held_counts(them)["LAT-11"] == 0
    assert w.team(us).cash == cash - price and price < official
