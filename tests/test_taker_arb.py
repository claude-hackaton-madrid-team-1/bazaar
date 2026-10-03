"""The taker's two held-card paths, both off by default: duplicate buys kept on their own surplus, and
arbitrage (buy a standing ask, sell into a standing bid on another venue the next tick, with priority)."""

from copy import deepcopy

from bazaar_agent.agents.taker import TakerConfig
from tests.agent_fakes import TICK, FakePublic, FakeTeam, ask, bid, rows
from tests.test_strategy import ME
from tests.test_taker import at, taker

ARB = {"arb_enabled": True, "arb_min_net_spread": 3, "arb_max_inventory_p": 60}
DUP = {"dup_buy_enabled": True, "dup_min_surplus": 3.0, "dup_max_spend_per_hour": 40}


def crossing_boards(bid_price=16):
    # LAV-01 (held, one more copy = 10 × 1.6 × 0.25 = 4): ask 5 + fee 2 on El Rastro, bid 16 on v02 (0 bps)
    return {
        "rastro": [ask(1, "LAV-01", 5, maker="t06")],
        "v02": [bid(2, "LAV-01", bid_price, venue="v02", maker="t17")],
    }


def with_second_copy(me=ME):
    me = deepcopy(me)
    me["assets"].append({"id": 900, "kind": "card", "ref": "LAV-01", "rarity": "common", "your_value": 4.0})
    return me


# ---------------------------------------------------------------- defaults


def test_with_the_defaults_a_held_card_is_never_bought(tmp_path):
    boards = crossing_boards()
    boards["v02"].append(ask(3, "LAV-06", 5, venue="v02", asset=901, maker="t08"))  # one more LAV-06 is worth 10
    team = FakeTeam()
    t, _, _ = taker(tmp_path, team, FakePublic(boards=boards), live=True)
    t.on_tick(at(team, TICK))
    assert team.sent == [] and t.exits == {}
    assert not [r for r in rows(tmp_path) if r.get("kind") == "accept_ask"]


# ---------------------------------------------------------------- duplicates


def test_a_duplicate_worth_its_price_is_bought_and_tagged_in_the_ledger(tmp_path):
    boards = {"v02": [ask(3, "LAV-06", 5, venue="v02", asset=901, maker="t08")]}  # 10 − 5 = 5 ≥ 3
    team = FakeTeam()
    t, _, ledger = taker(tmp_path, team, FakePublic(boards=boards), live=True, **DUP)
    t.on_tick(at(team, TICK))
    assert team.sent == [("accept", 3)]
    assert ledger.spend_rows("dup:", 0) == [("dup:LAV-06", 5, TICK)] and ledger.spent_since(0) == 5
    (row,) = [r for r in rows(tmp_path) if r.get("kind") == "accept_ask" and r.get("chosen")]
    assert row["inputs"]["held_buy"] == "dup" and "duplicate #2" in row["reason"]


def test_a_duplicate_below_the_threshold_or_over_the_hourly_cap_is_not_bought(tmp_path):
    boards = {"v02": [ask(3, "LAV-06", 8, venue="v02", asset=901, maker="t08")]}  # 10 − 8 = 2 < 3
    team = FakeTeam()
    t, _, _ = taker(tmp_path, team, FakePublic(boards=boards), live=True, **DUP)
    t.on_tick(at(team, TICK))
    assert team.sent == []
    boards = {"v02": [ask(3, "LAV-06", 5, venue="v02", asset=901, maker="t08")]}
    team = FakeTeam()
    t, lines, ledger = taker(tmp_path / "cap", team, FakePublic(boards=boards), live=True, **DUP)
    ledger.record("spend", TICK - 10, 1.4, 36, "dup:LAT-09")  # 36 + 5 > 40 this game hour
    t.on_tick(at(team, TICK))
    assert team.sent == []
    assert any("dup_max_spend_per_hour" in line for line in lines)


# ---------------------------------------------------------------- arbitrage


def test_arbitrage_buys_now_and_exits_with_priority_next_tick(tmp_path):
    team, public = FakeTeam(), FakePublic(boards=crossing_boards())
    t, _, ledger = taker(tmp_path, team, public, live=True, **ARB)
    t.on_tick(at(team, TICK))
    assert team.sent == [("accept", 1)]
    assert ledger.spend_rows("arb:", 0) == [("arb:LAV-01:900:2:v02:16:t06:t17", 7, TICK)]  # 5 + fee 2, copy 900
    (row,) = [r for r in rows(tmp_path) if r.get("kind") == "accept_ask" and r.get("chosen")]
    assert row["inputs"]["held_buy"] == "arb" and row["inputs"]["net"] == 9 and row["inputs"]["legs"] == [-3.0, 12.0]
    # next tick the card is ours: the exit takes the accept first, handing over the copy worth least to us
    team._me = with_second_copy()
    public.boards["rastro"] = [ask(4, "LAV-02", 10, asset=902, maker="t05")]  # a normal buy competes for the slot
    t.on_tick(at(team, TICK + 1))
    assert team.sent[1:] == [("accept", 2, [900])]
    assert t.exits == {} and ledger.accept_items(TICK + 1) == ["LAV-01"]
    (exit_row,) = [r for r in rows(tmp_path) if r.get("kind") == "arb_exit"]
    assert exit_row["status"] == "approved" and exit_row["inputs"]["proceeds"] == 16


def test_the_exit_waits_for_the_card_and_expires_to_the_maker(tmp_path):
    team, public = FakeTeam(), FakePublic(boards=crossing_boards())
    t, lines, _ = taker(tmp_path, team, public, live=True, config=TakerConfig(max_dealer_threads=0), **ARB)
    t.on_tick(at(team, TICK))
    for k in (1, 2, 3):  # the settlement never shows up in /me
        t.on_tick(at(team, TICK + k))
        assert "LAV-01" in t.exits
    t.on_tick(at(team, TICK + 4))
    assert t.exits == {} and any("exit for LAV-01 expired" in line for line in lines)
    assert team.sent == [("accept", 1)]  # never a sell without the card


def test_a_vanished_exit_bid_leaves_the_card_to_the_maker(tmp_path):
    team, public = FakeTeam(), FakePublic(boards=crossing_boards())
    t, lines, _ = taker(tmp_path, team, public, live=True, **ARB)
    t.on_tick(at(team, TICK))
    team._me, public.boards["v02"] = with_second_copy(), []
    t.on_tick(at(team, TICK + 1))
    assert team.sent == [("accept", 1)] and t.exits == {}
    assert any("is gone or moved" in line and "maker's sell flow" in line for line in lines)


def test_the_exit_is_re_read_just_before_the_buy(tmp_path):
    class Fickle(FakePublic):
        """The bid is on the board when the taker scans, gone when it re-reads before the accept."""

        reads = 0

        def board(self, venue="rastro"):
            if venue == "v02":
                self.reads += 1
                if self.reads > 1:
                    return {"offers": []}
            return super().board(venue)

    team = FakeTeam()
    t, lines, ledger = taker(tmp_path, team, Fickle(boards=crossing_boards()), live=True, **ARB)
    t.on_tick(at(team, TICK))
    assert team.sent == [] and t.exits == {} and ledger.accepts_in_tick(TICK) == 0
    assert any("arbitrage exit re-read: bid 2 for LAV-01 at 16 on v02 is gone" in line for line in lines)


def test_arbitrage_guards_net_inventory_known_makers_and_the_ring(tmp_path):
    def run(path, boards, **rules):
        team = FakeTeam()
        t, lines, ledger = taker(path, team, FakePublic(boards=boards), live=True, **{**ARB, **rules})
        t.on_tick(at(team, TICK))
        return team, t, lines, ledger

    team, *_ = run(tmp_path / "net", crossing_boards(bid_price=9))  # 9 − 7 = 2 < 3
    assert team.sent == []
    team, _, lines, _ = run(tmp_path / "inv", crossing_boards(), arb_max_inventory_p=6)  # cost 7 > 6
    assert team.sent == [] and any("arb_max_inventory_p" in line for line in lines)
    pseudonyms = crossing_boards()
    pseudonyms["rastro"] = [ask(1, "LAV-01", 5, maker="m3950d43b")]  # the feed never named this maker
    team, *_ = run(tmp_path / "anon", pseudonyms)
    assert team.sent == []
    team, t, _, _ = run(tmp_path / "ring", crossing_boards())
    assert team.sent == [("accept", 1)]
    t.exits.clear()  # the exit is done; the same two makers cross again
    t.on_tick(at(team, TICK + 1))
    assert team.sent == [("accept", 1)]  # no second round trip between t06 and t17 inside the cooldown


def test_a_page_card_worth_more_to_us_than_the_bid_is_not_resold(tmp_path):
    # LAV-02 is missing: our first copy is worth 16 + bonus; selling it at 15 is below the sell floor
    boards = {
        "rastro": [ask(1, "LAV-02", 5, maker="t06")],
        "v02": [bid(2, "LAV-02", 15, venue="v02", maker="t17")],
    }
    team = FakeTeam()
    t, _, _ = taker(tmp_path, team, FakePublic(boards=boards), live=True, **ARB)
    t.on_tick(at(team, TICK))
    assert t.exits == {}  # bought (or not) as a card to keep, never as an arbitrage
    assert not [r for r in rows(tmp_path) if (r.get("inputs") or {}).get("held_buy") == "arb"]


def test_dry_run_arbitrage_sends_nothing(tmp_path):
    team = FakeTeam()
    t, lines, ledger = taker(tmp_path, team, FakePublic(boards=crossing_boards()), **ARB)
    t.on_tick(at(team, TICK))
    assert team.sent == [] and ledger.spend_rows("arb:", 0) == []
    assert any("WOULD accept LAV-01" in line for line in lines) and "LAV-01" in t.exits


def test_the_exit_hands_over_exactly_the_copy_it_bought(tmp_path):
    # our original LAV-01 is worth less to us than the bought copy in /me (as if the page bonus moved):
    # the exit still sells asset 900, never the original
    team, public = FakeTeam(), FakePublic(boards=crossing_boards())
    t, _, _ = taker(tmp_path, team, public, live=True, **ARB)
    t.on_tick(at(team, TICK))
    me = with_second_copy()
    me["assets"][0]["your_value"] = 1.0  # asset 1, the original
    team._me = me
    t.on_tick(at(team, TICK + 1))
    assert team.sent[1:] == [("accept", 2, [900])]


def test_another_copy_arriving_is_not_mistaken_for_the_bought_one(tmp_path):
    # the arbitrage copy has not settled, but a different LAV-01 (a pack) shows up: no exit is sent
    team, public = FakeTeam(), FakePublic(boards=crossing_boards())
    t, _, _ = taker(tmp_path, team, public, live=True, **ARB)
    t.on_tick(at(team, TICK))
    me = deepcopy(ME)
    me["assets"].append({"id": 777, "kind": "card", "ref": "LAV-01", "rarity": "common", "your_value": 4.0})
    team._me = me
    t.on_tick(at(team, TICK + 1))
    assert team.sent == [("accept", 1)] and "LAV-01" in t.exits


def test_a_restarted_taker_rebuilds_the_exit_and_the_ring_guard_from_the_ledger(tmp_path):
    team, public = FakeTeam(), FakePublic(boards=crossing_boards())
    first, _, _ = taker(tmp_path, team, public, live=True, **ARB)
    first.on_tick(at(team, TICK))
    team._me = with_second_copy()
    second, _, _ = taker(tmp_path, team, public, live=True, **ARB)  # a new process, the same ledger
    second.on_tick(at(team, TICK + 1))
    assert team.sent == [("accept", 1), ("accept", 2, [900])]
    # the same two makers cross again: the ledger remembers the pair, so no second round trip
    team._me = deepcopy(ME)
    third, _, _ = taker(tmp_path, team, public, live=True, **ARB)
    third.on_tick(at(team, TICK + 2))
    assert team.sent == [("accept", 1), ("accept", 2, [900])]
