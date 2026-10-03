"""The official value cap (Day-2 hint: "a deal above your own value costs points") through the real agents' tick:
the taker's board accepts and dealer threads, the maker's board bids, the reads per tick and the sell side.
Fakes only, no network: the fake team answers `value(card)` like `GET /api/me/value?card=<ref>`."""

from collections import Counter

import pytest

from bazaar_agent.agents.maker import Maker
from bazaar_agent.agents.taker import Taker, TakerConfig
from bazaar_agent.sdk import BazaarError
from tests.agent_fakes import TICK, FakePublic, FakeTeam, ask, bid, clock, parts, rows

pytestmark = pytest.mark.official_values

HIGH = 500.0  # an official value no test price reaches


class ValuedTeam(FakeTeam):
    """FakeTeam with `value(card)`: the official value per card (`values`, else `default`), every call counted.
    `accept` records the copies handed over, so a sell is told apart from a buy."""

    def __init__(self, values=None, default=HIGH, fail=False, **kw):
        super().__init__(**kw)
        self.values = dict(values or {})
        self.default = default
        self.fail = fail
        self.value_calls: list[str] = []

    def value(self, card):
        self.value_calls.append(card)
        if self.fail:
            raise BazaarError("network", f"GET /api/me/value?card={card}: timed out", 0)
        return {"card": card, "your_value": self.values.get(card, self.default)}

    def accept(self, offer_id, assets=None):
        self.sent.append(("accept", offer_id) if assets is None else ("accept", offer_id, assets))
        return {"ok": True, "settles_tick": self.now.tick + 1}


def taker(tmp_path, team, public, *, live=False, dealers=0, accept_bids=False, **rules):
    lines: list[str] = []
    t = Taker(
        team,
        public,
        live=live,
        log=lines.append,
        now=lambda: 1000.0,
        sleep=lambda s: None,
        config=TakerConfig(max_dealer_threads=dealers, accept_bids=accept_bids),
        **parts(tmp_path, **rules),
    )
    return t, lines


def maker(tmp_path, team, *, live=True, **rules):
    lines: list[str] = []
    m = Maker(team, FakePublic(), live=live, log=lines.append, now=lambda: 1000.0, **parts(tmp_path, **rules))
    return m, lines


def at(team, tick):
    team.now = clock(tick=tick)
    return team.now


def her_ask(team, tid, oid, cash, item="LAV-08"):
    offer = {"id": oid, "maker": "abuela", "status": "open", "final": False}
    offer |= {"give": {"types": [f"card:{item}"]}, "want": {"cash": cash}}
    team.thread_payloads[tid] = {
        "id": tid,
        "status": "open",
        "messages": [{"offer": offer}],
        "standing_offers": [offer],
    }


def board(*offers):
    return FakePublic(boards={"rastro": list(offers)})


# ---------------------------------------------------------------- 1. taker: board accepts


def test_a_board_ask_our_model_likes_is_refused_when_ask_plus_fee_is_above_the_official_value(tmp_path):
    # LAV-02 is worth 20.8 to our model: 10 + fee 2 = 12 is a would-accept with the cap off. Officially it is
    # worth 11: under the total with the fee, over the bare ask, so the fee must be counted.
    team = ValuedTeam(values={"LAV-02": 11.0})
    t, lines = taker(tmp_path, team, board(ask(1, "LAV-02", 10)))
    t.on_tick(clock())
    assert team.sent == [] and team.value_calls == ["LAV-02"]
    assert not any("WOULD accept LAV-02" in line for line in lines)
    assert any("official value 11 of LAV-02" in line for line in lines)
    (row,) = [r for r in rows(tmp_path) if r.get("kind") == "accept_ask"]
    assert row["status"] == "rejected" and not row["chosen"] and "official value" in row["guardrail"]


def test_the_same_board_ask_is_accepted_when_the_official_value_covers_ask_plus_fee(tmp_path):
    team = ValuedTeam(values={"LAV-02": 12.0})  # exactly the total: at the cap is allowed
    t, lines = taker(tmp_path, team, board(ask(1, "LAV-02", 10)))
    t.on_tick(clock())
    assert any(line.startswith(f"tick {TICK} taker: WOULD accept LAV-02 on rastro for 12") for line in lines)
    (row,) = [r for r in rows(tmp_path) if r.get("kind") == "accept_ask" and r.get("chosen")]
    assert (row["status"], row["guardrail"]) == ("approved", "allowed")


def test_a_live_board_accept_is_sent_only_under_the_official_value(tmp_path):
    low = ValuedTeam(values={"LAV-02": 11.0})
    taker(tmp_path / "low", low, board(ask(1, "LAV-02", 10)), live=True)[0].on_tick(clock())
    assert low.sent == []
    ok = ValuedTeam(values={"LAV-02": 30.0})
    taker(tmp_path / "ok", ok, board(ask(1, "LAV-02", 10)), live=True)[0].on_tick(clock())
    assert ok.sent == [("accept", 1)]


def test_the_official_value_margin_tightens_the_cap(tmp_path):
    team = ValuedTeam(values={"LAV-02": 13.0})  # 12 <= 13, but 12 > 13 - margin 2
    t, lines = taker(tmp_path, team, board(ask(1, "LAV-02", 10)), live=True, official_value_margin=2.0)
    t.on_tick(clock())
    assert team.sent == [] and any("official_value_margin 2" in line for line in lines)


# ---------------------------------------------------------------- 2. taker: dealer threads


def test_a_dealer_thread_is_not_opened_when_its_opening_bid_is_above_the_official_value(tmp_path):
    # With the cap off the desk opens Abuela for LAV-08 and bids 18 (tests/test_taker.py).
    team = ValuedTeam(values={"LAV-08": 15.0})
    t, lines = taker(tmp_path, team, FakePublic(), live=True, dealers=3)
    t.on_tick(clock())
    assert not [s for s in team.sent if s[0] in ("open_thread", "say")]
    (row,) = [r for r in rows(tmp_path) if r.get("kind") == "dealer_open"]
    assert row["inputs"]["item"] == "LAV-08" and row["status"] == "rejected"
    assert row["guardrail"] == "denied: price 18 > official value 15 of LAV-08 (GET /api/me/value)"
    assert any("official value" in line for line in lines)


def test_a_later_dealer_bid_above_the_official_value_is_never_planned(tmp_path):
    # 18.5 lets the opening 18 through; her ask 24 would call for 19 next, which is above it. The open lowers the
    # ladder's top to the official value it read (18), so 19 is never bid: we walk when nothing is left inside it.
    team = ValuedTeam(values={"LAV-08": 18.5})
    t, _ = taker(tmp_path, team, FakePublic(), live=True, dealers=3)
    t.on_tick(clock())
    assert ("open_thread", "abuela", {"buy": {"card": "LAV-08"}}) in team.sent
    assert [s for s in team.sent if s[0] == "say"] == [("say", 5000, 18)]
    (opened,) = [r for r in rows(tmp_path) if r.get("kind") == "dealer_open"]
    assert opened["inputs"]["plan"] == "18→18 step 1" and "top 18: official value cap" in opened["reason"]
    her_ask(team, 5000, 800, 24)
    t.on_tick(at(team, TICK + 1))
    assert [s for s in team.sent if s[0] == "say"] == [("say", 5000, 18)]  # 19 never sent
    assert ("close_thread", 5000) in team.sent


def test_a_failed_value_read_holds_the_dealer_thread_for_the_tick_and_never_walks(tmp_path):
    # Review #177 P1-2: value 40, her ask 24, one failed read: hold the tick (no close_thread), bid the next.
    team = ValuedTeam(values={"LAV-08": 40.0})
    t, lines = taker(tmp_path, team, FakePublic(), live=True, dealers=3)
    t.on_tick(clock())
    assert [s for s in team.sent if s[0] == "say"] == [("say", 5000, 18)]
    her_ask(team, 5000, 800, 24)
    team.fail = True
    t.on_tick(at(team, TICK + 1))
    assert not [s for s in team.sent if s[0] == "close_thread"]
    assert [s for s in team.sent if s[0] == "say"] == [("say", 5000, 18)]
    assert any("hold" in line and "could not be read" in line for line in lines)
    team.fail = False
    t.on_tick(at(team, TICK + 2))
    assert not [s for s in team.sent if s[0] == "close_thread"]
    assert [s for s in team.sent if s[0] == "say"][-1] == ("say", 5000, 19)


# ---------------------------------------------------------------- 3. maker: board bids


def test_the_maker_never_posts_a_bid_above_the_official_value(tmp_path):
    # Its strategy target is a 65 bid for LAV-09 (tests/test_maker.py).
    team = ValuedTeam(values={"LAV-09": 50.0})
    m, lines = maker(tmp_path, team)
    m.on_tick(clock())
    assert not [s for s in team.sent if s[0] == "list_offer" and s[1].get("cash")]
    assert any("price 65 > official value 50 of LAV-09" in line for line in lines)
    assert m.ledger.spent_since(0) == 0


def test_the_maker_posts_the_bid_when_the_official_value_covers_it(tmp_path):
    team = ValuedTeam(values={"LAV-09": 100.0})
    maker(tmp_path, team)[0].on_tick(clock())
    bids = [s for s in team.sent if s[0] == "list_offer" and s[1].get("cash")]
    assert bids == [("list_offer", {"cash": 65}, {"cards": ["LAV-09"]}, "rastro")]
    assert "LAV-09" in team.value_calls and not {"LAT-09", "LAT-03"} & set(team.value_calls)  # asks read nothing


def test_a_standing_bid_above_the_official_value_is_cancelled(tmp_path):
    # Review #177 P2: a bid posted before the cap (or the cap moved) never stays open above the official value.
    team = ValuedTeam(values={"LAV-09": 50.0}, offers=[bid(2, "LAV-09", 65)])
    m, lines = maker(tmp_path, team)
    m.on_tick(clock())
    assert ("cancel", 2) in team.sent
    assert not [s for s in team.sent if s[0] == "list_offer" and s[1].get("cash")]
    assert any("official value 50" in line for line in lines)


def test_a_standing_bid_under_the_official_value_stays(tmp_path):
    team = ValuedTeam(values={"LAV-09": 100.0}, offers=[bid(2, "LAV-09", 65)])
    maker(tmp_path, team)[0].on_tick(clock())
    assert ("cancel", 2) not in team.sent


# ---------------------------------------------------------------- 4. reads: once per card per tick, fail closed


def test_the_taker_reads_each_cards_official_value_at_most_once_per_tick(tmp_path):
    public = board(ask(1, "LAV-02", 10), ask(2, "LAV-08", 20, asset=901), ask(3, "LAV-02", 11, asset=902))
    team = ValuedTeam()
    t, _ = taker(tmp_path, team, public, dealers=3)  # board accepts and the dealer desk in the same tick
    t.on_tick(clock())
    t.on_tick(clock())  # the same tick again (a restarted loop): served from the cache
    first = Counter(team.value_calls)
    assert first and max(first.values()) == 1
    assert t.values.reads == len(team.value_calls)
    t.on_tick(at(team, TICK + 1))  # a new tick reads again
    assert max(Counter(team.value_calls).values()) <= 2


def test_a_failed_official_value_read_refuses_the_buy(tmp_path):
    team = ValuedTeam(fail=True)
    t, lines = taker(tmp_path, team, board(ask(1, "LAV-02", 10)), live=True)
    t.on_tick(clock())
    assert team.sent == [] and team.value_calls == ["LAV-02"] and t.values.failures == 1
    assert any("official value of LAV-02 could not be read" in line for line in lines)


def test_a_failed_read_also_keeps_the_dealer_desk_from_opening(tmp_path):
    team = ValuedTeam(fail=True)
    t, _ = taker(tmp_path, team, FakePublic(), live=True, dealers=3)
    t.on_tick(clock())
    assert not [s for s in team.sent if s[0] in ("open_thread", "say")]


def test_a_client_without_value_fails_closed(tmp_path):
    team = FakeTeam()  # the SDK of an older kit: no value() at all
    t, lines = taker(tmp_path, team, board(ask(1, "LAV-02", 10)), live=True)
    t.on_tick(clock())
    assert team.sent == [] and any("could not be read" in line for line in lines)


# ---------------------------------------------------------------- 5. sells never read it


def test_selling_into_a_standing_bid_never_reads_the_official_value(tmp_path):
    team = ValuedTeam(default=0.0)  # a value of 0 would refuse any buy: the sell must not care
    rich = board(bid(77, "LAT-09", 70, maker="m9"))  # 70 - fee 5 - our loss 45 = +20 (tests/test_rivals.py)
    t, _ = taker(tmp_path, team, rich, live=True, accept_bids=True)
    t.on_tick(clock())
    assert team.sent == [("accept", 77, [5])]
    assert team.value_calls == [] and t.values.reads == 0
