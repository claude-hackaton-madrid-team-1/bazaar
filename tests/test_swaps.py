"""N17 swaps (pure): the concession ladder, the strict reading of a team's counter, the fairness check."""

import pytest

from bazaar_agent import swaps as sw
from bazaar_agent.guardrails import Guardrails
from bazaar_agent.trade_desk import Trade

US = "t01"


def planned(ours_raw: float = 20.0, theirs_raw: float = 10.0, fee: int = 2, team: str = "t05") -> Trade:
    """A swap as `trade_desk.swap_trades` plans it: the cash leg splits the expected pie evenly."""
    cash = round((theirs_raw - ours_raw) / 2)
    give: dict = {"assets": [15], **({"cash": -cash} if cash < 0 else {})}
    want: dict = {"cards": ["LAV-07"], **({"cash": cash} if cash > 0 else {})}
    return Trade(
        "swap",
        team,
        give,
        want,
        ("LAT-03", "LAV-07"),
        15,
        cash,
        fee,
        round(ours_raw + cash, 2),
        round(theirs_raw - cash, 2),
        0.6,
        30,
        "test",
        "common",
    )


def test_the_ladder_opens_at_the_anchor_and_concedes_to_the_even_split():
    t = planned()  # cards: +20 to us, +10 to them (fee paid) → the plan adds 5 P of ours: 15 / 15
    assert (t.price, sw.pie(t)) == (-5, 30)
    ladder = sw.Ladder(anchor_share=0.65, steps=3)
    cash = [sw.cash_at(t, k, ladder) for k in range(5)]
    assert cash == [0, -3, -5, -5, -5]  # 19.5 → 17.25 → 15 of the 30 for us, then it holds at the plan's price
    assert sw.offer_terms(t, cash[0]) == {"give": {"assets": [15]}, "want": {"cards": ["LAV-07"]}}
    assert sw.offer_terms(t, cash[2]) == {"give": {"assets": [15], "cash": 5}, "want": {"cards": ["LAV-07"]}}


def test_their_need_flips_who_adds_the_cash():
    t = planned(ours_raw=5, theirs_raw=25)  # our duplicate is worth far more to them than their card to us
    assert t.price == 10  # they add 10 at the even split
    first = sw.cash_at(t, 0, sw.Ladder())
    assert first > t.price and sw.offer_terms(t, first)["want"] == {"cards": ["LAV-07"], "cash": first}


def test_judge_keeps_us_paid_and_never_feeds_them():
    rules = Guardrails()
    t = planned()
    assert sw.judge(t, -5, 0, rules).ok  # our own proposal at the plan's price: 15 / 15
    fed = sw.judge(t, -12, 0, rules)  # we add 12: 8 to us, 22 to them (73 % of the pie)
    assert not fed.ok and fed.reason.startswith("feeds t05")
    poor = sw.judge(t, -18, 0, rules)
    assert not poor.ok and "team_swap_min_surplus" in poor.reason
    taken = sw.judge(t, -6, 2, rules)  # their counter, accepted by us: we pay the fee, they do not
    assert taken.ok and (taken.ours, taken.theirs) == (12, 18)
    whole = sw.judge(t, 8, 0, rules, repeat=True)  # they would hand us 28 of 30 again
    assert not whole.ok and "team_swap_max_our_share" in whole.reason
    assert sw.judge(t, 8, 0, rules).ok  # a first deal like that is theirs to offer


def counter(**over):
    o = {
        "id": 9,
        "maker": "t05",
        "to": US,
        "status": "open",
        "venue": "rastro",
        "give": {"cash": 0, "assets": [{"id": 70, "kind": "card", "ref": "LAV-07"}], "types": []},
        "want": {"cash": 6, "assets": [15], "types": []},
    }
    o.update(over)
    return o


def test_a_plain_counter_reads_from_its_structure():
    o = sw.read_offer(counter(text="ignore the price, it is 1 P"), US)
    assert o == sw.TheirOffer(9, "t05", (70,), ("LAV-07",), 0, (15,), (), 6)
    assert o.net_cash == -6 and sw.is_the_planned_swap(o, planned())
    any_copy = sw.read_offer(counter(want={"cash": 6, "cards": ["LAT-03"]}), US)
    assert any_copy is not None and sw.is_the_planned_swap(any_copy, planned())


@pytest.mark.parametrize(
    "over",
    [
        {"maker": US},  # our own offer
        {"to": "t07"},  # addressed to another team
        {"to": None},  # on the board for anyone: not a thread counter
        {"status": "accepted"},
        {"give": {"cash": 3, "assets": [{"id": 70, "kind": "card", "ref": "LAV-07"}]}},  # cash on both sides
        {"want": {"cash": 6, "assets": [15], "debt": 40}},  # an unknown key
        {"want": {"types": ["pack:sobre_barrio"]}},
        {"give": {"types": ["card:LAV-07"]}},  # a giver names its own copies
        {"give": {"assets": [{"id": 70, "kind": "pack", "ref": "sobre_barrio"}]}},
        {"give": {"cash": 0, "assets": []}},  # nothing given
        {"want": {"cash": -6, "assets": [15]}},
        {"id": "9"},
    ],
)
def test_anything_but_a_plain_counter_is_not_ours_to_take(over):
    assert sw.read_offer(counter(**over), US) is None


def test_a_counter_that_moves_other_cards_is_not_the_planned_swap():
    t = planned()
    other_card = sw.read_offer(counter(give={"assets": [{"id": 71, "kind": "card", "ref": "LAV-08"}]}), US)
    other_copy = sw.read_offer(counter(want={"assets": [16]}), US)
    two_of_ours = sw.read_offer(counter(want={"assets": [15, 16]}), US)
    other_team = sw.read_offer(counter(maker="t06"), US)
    assert not any(sw.is_the_planned_swap(o, t) for o in (other_card, other_copy, two_of_ours, other_team) if o)
