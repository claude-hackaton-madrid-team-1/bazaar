"""B16 (bite X18): an accept of the last two ticks that `/api/me` does not show yet counts as held, its cash as gone.

An accept settles on the next tick; a read can land before that. The shared ledger already holds the accept row
(item and price), so the taker (and the maker) count it until `/api/me` shows the card. The flipped bite test lives
in `tests/bites/test_taker_unsettled_duplicate.py`.
"""

import pytest

from bazaar_agent.agents.seller import unsettled_accepts
from bazaar_agent.guardrails import Ledger
from bazaar_agent.ledger_pg import PgLedger
from tests.agent_fakes import TICK, FakePublic, FakeTeam, ask, clock
from tests.test_db import database_url, open_in, schema  # noqa: F401  (pytest fixtures)
from tests.test_maker import maker
from tests.test_strategy import ME
from tests.test_taker import at, taker

TWO_COPIES = {"rastro": [ask(2, "LAV-08", 20, asset=901), ask(3, "LAV-08", 21, asset=902)]}


def _card(ref, asset_id=9001):
    return {"id": asset_id, "kind": "card", "ref": ref, "rarity": "uncommon", "your_value": 30.0}


def test_unsettled_accepts_counts_what_me_does_not_show_yet(tmp_path):
    ledger = Ledger(tmp_path / "ledger.jsonl")
    ledger.reserve_accept(98, 1.4, 22, "LAV-08", 1)  # two ticks ago, still not in /me
    ledger.reserve_accept(99, 1.45, 17, "sobre_barrio", 1)  # a pack, last tick
    ledger.reserve_accept(97, 1.35, 30, "LAV-09", 1)  # three ticks ago: settled or never landed
    me = {"cash": 400, "assets": []}
    c = unsettled_accepts(me, ledger, 100)
    assert (c.cash, c.wanted) == (39, ("LAV-08",))


def test_a_card_me_shows_is_settled_and_counted_once(tmp_path):
    ledger = Ledger(tmp_path / "ledger.jsonl")
    ledger.reserve_accept(99, 1.45, 22, "LAV-08", 1)
    me = {"cash": 378, "assets": [_card("LAV-08")]}
    c = unsettled_accepts(me, ledger, 100)
    assert (c.cash, c.wanted) == (0, ())


def test_a_pack_accept_always_counts_an_older_pack_never_hides_it(tmp_path):
    """Nothing opens our packs: an older unopened pack in /api/me must not pass a new unsettled one as settled
    (review of #143: cash 305, pack bought for 17, then LAV-08 at 22 ended at 266 < floor 270)."""
    ledger = Ledger(tmp_path / "ledger.jsonl")
    ledger.reserve_accept(99, 1.45, 17, "sobre_barrio", 1)
    me = {"cash": 305, "assets": [{"id": 5, "kind": "pack", "ref": "sobre_barrio"}]}
    c = unsettled_accepts(me, ledger, 100)
    assert (c.cash, c.wanted) == (17, ())


def test_an_accept_price_below_zero_never_adds_cash(tmp_path):
    ledger = Ledger(tmp_path / "ledger.jsonl")
    ledger.record("accept", 99, 1.45, -500, "LAV-08")
    ledger.record("accept", 99, 1.45, "12.5", "LAV-09")  # type: ignore[arg-type]  (a hand-edited row)
    c = unsettled_accepts({"cash": 300, "assets": []}, ledger, 100)
    assert c.cash == 0 and ledger.accepts_in_tick(99) == 2


def test_another_process_accept_earlier_this_tick_counts(tmp_path):
    ledger = Ledger(tmp_path / "ledger.jsonl")
    ledger.reserve_accept(100, 1.5, 22, "LAV-08", 1)
    c = unsettled_accepts({"cash": 400, "assets": []}, ledger, 100)
    assert (c.cash, c.wanted) == (22, ("LAV-08",))


def test_duel_rows_and_released_accepts_do_not_count(tmp_path):
    ledger = Ledger(tmp_path / "ledger.jsonl")
    ledger.reserve_accept(99, 1.45, 0, "duel:12", 1)
    ledger.reserve_accept(98, 1.4, 22, "LAV-08", 1)
    ledger.release_accept(98, "LAV-08")  # refused by the game (B18): it cost nothing
    c = unsettled_accepts({"cash": 400, "assets": []}, ledger, 100)
    assert (c.cash, c.wanted) == (0, ())


def test_a_card_another_process_accepted_last_tick_is_not_bought_again(tmp_path):
    """`bazaar dealer buy` on a laptop took LAV-08 last tick (same shared ledger): the taker does not buy it."""
    team = FakeTeam()
    t, lines, ledger = taker(tmp_path, team, FakePublic(boards=TWO_COPIES), live=True)
    ledger.reserve_accept(TICK - 1, 1.49, 18, "LAV-08", 1)
    t.on_tick(clock())
    assert team.sent == []
    assert any("we already hold LAV-08" in line for line in lines)


def test_once_two_ticks_passed_without_the_card_the_accept_no_longer_counts(tmp_path):
    """An accept that may have landed (a network error) and never did: after two ticks it frees the card."""
    team = FakeTeam()
    t, _, ledger = taker(tmp_path, team, FakePublic(boards=TWO_COPIES), live=True)
    ledger.reserve_accept(TICK - 3, 1.45, 18, "LAV-08", 1)
    t.on_tick(clock())
    assert team.sent == [("accept", 2)]


def test_an_unsettled_accepts_cash_counts_against_the_floor(tmp_path):
    """Cash 400, floor 270: last tick's 120 P accept of another card (SAL-07) is not settled; 400 - 120 - 22 < 270."""
    team = FakeTeam()
    t, lines, ledger = taker(tmp_path, team, FakePublic(boards=TWO_COPIES), live=True)
    ledger.reserve_accept(TICK - 1, 1.49, 120, "SAL-07", 1)
    t.on_tick(clock())
    assert team.sent == []
    assert any("cash_floor" in line for line in lines)


def test_a_settled_accepts_cash_is_not_counted_twice(tmp_path):
    """/me already shows SAL-07, so last tick's accept of it settled and /me's cash already paid for it: its
    120 P is not taken off again (400 - 22 >= 270, the buy goes)."""
    me = {**ME, "assets": [*ME["assets"], _card("SAL-07", 9002)]}
    team = FakeTeam(me=me)
    t, _, ledger = taker(tmp_path, team, FakePublic(boards=TWO_COPIES), live=True)
    ledger.reserve_accept(TICK - 1, 1.49, 120, "SAL-07", 1)
    t.on_tick(clock())
    assert team.sent == [("accept", 2)]


def test_the_taker_does_not_buy_the_card_it_accepted_last_tick_twice(tmp_path):
    team = FakeTeam()
    t, _, ledger = taker(tmp_path, team, FakePublic(boards=TWO_COPIES), live=True)
    t.on_tick(clock())
    t.on_tick(at(team, TICK + 1))  # /api/me read before the server settled offer 2
    t.on_tick(at(team, TICK + 2))
    assert team.sent == [("accept", 2)]


def test_the_maker_does_not_bid_for_a_card_the_taker_accepted_last_tick(tmp_path):
    """LAV-09 is the maker's bid target; the taker took an ask for it last tick, not settled yet."""
    team = FakeTeam()
    m, lines = maker(tmp_path, team, live=True)
    m.ledger.reserve_accept(TICK - 1, 1.49, 60, "LAV-09", 1)
    team.now = clock()
    m.on_tick(team.now)
    assert not [s for s in team.sent if s[0] == "list_offer" and s[1].get("cash")]
    assert any("we already hold LAV-09" in line for line in lines)


@pytest.mark.integration
def test_accept_rows_read_postgres(database_url, schema):  # noqa: F811
    from bazaar_agent import db

    conn = open_in(database_url, schema)
    db.init_schema(conn)
    ledger = PgLedger(conn, "taker")
    ledger.reserve_accept(99, 1.45, 22, "LAV-08", 1)
    ledger.reserve_accept(98, 1.4, 0, "duel:3", 1)
    assert ledger.accept_rows(99) == [("LAV-08", 22)]
    ledger.release_accept(99, "LAV-08")
    assert ledger.accept_rows(99) == []
    c = unsettled_accepts({"cash": 400, "assets": []}, ledger, 100)
    assert (c.cash, c.wanted) == (0, ())
    conn.close()
