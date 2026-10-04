"""SA1 part 2, "dealer sells on news": is turning `dealer_sell_enabled` on SAFE with today's GUARDRAILS.md? Tests per
property, on fakes only (no network, no database):

1. every set is protected: never a page card's only copy, nor its last FREE copy (one an open offer of ours gives),
   when the thread opens and on every later send;
2. the floor: never below `sell_min_value_ratio` × your_value, nor your_value + page bonus + `dealer_sell_min_surplus`;
3. a sale at or above `human_approval_above` needs an approval in force; unreadable approvals hold, never walk;
4. never a deal at the dealer's opening bid (also tests/test_dealer_sell.py);
5. a trickster's (Los Pícaros) "final" is no limit: it reads exactly as an ordinary bid;
6. the dealer LEVEL follows the ladder's scoring: the highest level that buys the copy and has bid at or above our
   floor, a level with fewer than three scored deals today first, then the gain;
7. the kill switch (the pause file) holds every send, and never turns into a walk;
8. fevers come only from official news: tests/test_persona_sell.py::test_the_desk_reads_fever_only_from_official_
   signals_in_force."""

from __future__ import annotations

import math
import random
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from bazaar_agent import approvals
from bazaar_agent import guardrails as gr
from bazaar_agent.agents import dealer_sell_data as dd
from bazaar_agent.agents import dealer_sell_desk as desk
from bazaar_agent.agents.dealer import Hold, Move
from bazaar_agent.agents.dealer_sell import AskPlan, SellNegotiation, ask_schedule, decide_sell
from bazaar_agent.agents.runtime import JevAdvice, Recorder
from bazaar_agent.agents.strategy_gate import StrategyGate
from bazaar_agent.decisions import DecisionLog
from bazaar_agent.persona_model import parse_personas
from tests.agent_fakes import FakeTeam, clock
from tests.test_dealer_sell_desk import ME_DUP
from tests.test_strategy import CATALOG, ME, PARAMS, card

REAL = gr.load_guardrails().rules  # the committed GUARDRAILS.md, as every agent loads it
AT60 = REAL.model_copy(update={"human_approval_above": 60})  # the approval gate at Saturday's 60 (250 since ~20:00)
EVERY_SET = ("LAV", "SAL", "MAL", "RET", "LAT", "CHA")


def lines_for(*rarities: str, sets: Any = "released") -> list[dict[str, Any]]:
    return [{"rarity": r, "sets": sets} for r in rarities]


def persona(pid: str, name: str, kind: str, level: int, buys: list[dict[str, Any]]) -> dict[str, Any]:
    menu = {"buys": buys, "deals_per_team_per_hour": 6}
    return {"id": pid, "name": name, "kind": kind, "level": level, "status": "active", "menu": menu, "unlock": {}}


# GET /api/dealers (keyless, Sat 3 Oct 19:10): who buys what, at which level.
API = [
    persona("abuela", "Abuela Carmen", "dealer", 1, lines_for("common", "uncommon")),
    persona("chato", "El Chato", "dealer", 2, lines_for("uncommon", "rare")),
    persona(
        "pilar",
        "Doña Pilar",
        "collector",
        3,
        [*lines_for("uncommon", "rare", "epic", sets=["SAL", "RET"]), *lines_for("uncommon", "rare", "epic")],
    ),
    persona("picaros", "Los Pícaros", "trickster", 4, lines_for("common", "uncommon")),
    persona("banco", "Don Ernesto", "banker", 5, lines_for("epic", "legendary")),
]
ROWS = [{**p, "kind": "dealer"} for p in API]  # the `traders` table stores every persona as kind 'dealer'
FILLS = {
    ("abuela", "common"): dd.Fill(5, 6.0, 7, 4, 6.0),
    ("abuela", "uncommon"): dd.Fill(12, 17.0, 21, 2, 17.0),
    ("chato", "uncommon"): dd.Fill(13, 14.0, 14, 1, 14.0),
    ("chato", "rare"): dd.Fill(39, 46.0, 46, 1, 46.0),
    ("pilar", "uncommon"): dd.Fill(15, 16.0, 18, 3, 16.0),
    ("picaros", "common"): dd.Fill(4, 5.0, 6, 3, 5.0),
    ("picaros", "uncommon"): dd.Fill(10, 12.0, 16, 2, 12.0),
    # Data its menu contradicts (banco buys only epics and legendaries): the highest gain, never a buyer.
    ("banco", "common"): dd.Fill(20, 30.0, 40, 2, 30.0),
    ("banco", "uncommon"): dd.Fill(20, 30.0, 40, 2, 30.0),
    ("banco", "rare"): dd.Fill(60, 90.0, 99, 2, 90.0),
}
MARKET6 = dd.SellMarket(tuple(t for t in map(dd.trader_from, ROWS) if t is not None), FILLS, "test")
PERSONAS = parse_personas(API)
ME5 = {**deepcopy(ME_DUP), "unlocked": [p["id"] for p in API]}  # spares: LAT-03 ×2 (common), LAV-08 ×3 (uncommon)

# Every set released, one card per situation; the low affinities make `strategy._spare` alone offer an only copy.
CAT6 = deepcopy(CATALOG)
CAT6["sets"] = [
    *(s for s in CAT6["sets"] if s["id"] in ("LAV", "LAT")),
    {"id": "SAL", "cards": [card("SAL-01", "common", 12), card("SAL-07", "uncommon", 10)]},
    {"id": "MAL", "cards": [card("MAL-02", "common", 12)]},
    {"id": "RET", "cards": [card("RET-01", "common", 12)]},
    {"id": "CHA", "cards": [card("CHA-01", "common", 12)]},
]
ME6 = {
    **deepcopy(ME),
    "unlocked": ["abuela", "chato"],
    "affinity": {"LAV": 1.6, "SAL": 0.5, "MAL": 0.5, "RET": 0.5, "LAT": 0.5, "CHA": 0.5},
    "album": {"pages": [{"set": s, "have": 1, "of": 6} for s in EVERY_SET]},
    "assets": [
        {"id": 10, "kind": "card", "ref": "LAV-08", "rarity": "uncommon", "your_value": 2.0},  # only copy
        {"id": 20, "kind": "card", "ref": "SAL-07", "rarity": "uncommon", "your_value": 2.0},  # only copy, no boost
        {"id": 30, "kind": "card", "ref": "MAL-02", "rarity": "common", "your_value": 1.0},  # 31 is listed:
        {"id": 31, "kind": "card", "ref": "MAL-02", "rarity": "common", "your_value": 1.0},  # 30 is the last free one
        {"id": 40, "kind": "card", "ref": "RET-01", "rarity": "common", "your_value": 1.0},  # only copy, a new page
        {"id": 50, "kind": "card", "ref": "LAT-03", "rarity": "common", "your_value": 1.2},  # two free copies:
        {"id": 51, "kind": "card", "ref": "LAT-03", "rarity": "common", "your_value": 1.2},  # the one true spare
        {"id": 60, "kind": "card", "ref": "CHA-01", "rarity": "common", "your_value": 1.0},  # only copy, Sunday's page
        {"id": 70, "kind": "card", "ref": "LAT-09", "rarity": "rare", "your_value": 35.0},  # only copy, a rare
    ],
}
MARKET_AB = dd.SellMarket(
    tuple(t for t in map(dd.trader_from, ROWS[:2]) if t is not None),
    {k: dd.Fill(5, 50.0, 100, 3, 50.0) for k in [("abuela", "common"), ("abuela", "uncommon"), ("chato", "rare")]},
    "test",
)


# ---------------------------------------------------------------- fakes: a dealer on our sell thread, the desk


def sell_bid(dealer: str, asset: int, ref: str, cash: int, oid: int, final: bool = False) -> dict[str, Any]:
    """A dealer's structured bid on a sell thread: cash for exactly our copy."""
    want = {"cash": 0, "assets": [{"id": asset, "ref": ref, "kind": "card"}], "types": []}
    give = {"cash": cash, "assets": [], "types": []}
    return {"id": oid, "maker": dealer, "to": "t01", "status": "open", "final": final, "give": give, "want": want}


class Seller(FakeTeam):
    """A dealer on our sell thread: after each priced ask of ours it bids the next of `bids` (offer ids 700, 701,
    ...), FINAL from index `final_from` on. It never takes our ask in words."""

    def __init__(self, dealer: str, bids: list[int], final_from: int | None = None, **kw: Any) -> None:
        super().__init__(**kw)
        self.dealer, self.bids, self.final_from = dealer, list(bids), final_from
        self.asset, self.ref, self.n, self.status = 0, "", 0, "open"
        self.standing: list[dict[str, Any]] = []

    def open_thread(self, with_, topic=None, venue=None):
        self.asset = topic["sell"]["assets"][0]
        self.ref = next(str(a["ref"]) for a in self._me["assets"] if a.get("id") == self.asset)
        return super().open_thread(with_, topic, venue)

    def say(self, tid, text="", price=None, offer=None, topic=None):
        if price is not None and self.n < len(self.bids):
            final = self.final_from is not None and self.n >= self.final_from
            self.standing = [sell_bid(self.dealer, self.asset, self.ref, self.bids[self.n], 700 + self.n, final)]
            self.n += 1
        return super().say(tid, text, price)

    def thread(self, tid):
        self.reads.append(f"thread {tid}")
        return {"id": tid, "status": self.status, "standing_offers": deepcopy(self.standing), "messages": []}

    def accept(self, offer_id, assets=None):
        self.status = "deal"
        return super().accept(offer_id)


def jev_yes(name: str, state: dict[str, Any]) -> JevAdvice:
    return JevAdvice("yes", 0.9)


def live_rules(tmp_path: Path, **update: Any) -> gr.Guardrails:
    """The committed rules with the desk switched on (only here: GUARDRAILS.md keeps it off), a pause file of the
    test's own."""
    return REAL.model_copy(update={"dealer_sell_enabled": True, "pause_file": str(tmp_path / "PAUSE"), **update})


class Desk:
    """A live `SellDesk` as the maker runs it: `standard_hooks` on a context read like the maker's (/me, the shared
    ledger, the kill switch read live), Jev's gate saying yes, and a snapshot per tick."""

    def __init__(self, tmp_path: Path, team: Seller, rules: gr.Guardrails, market: dd.SellMarket = MARKET6) -> None:
        self.team, self.rules, self.lines = team, rules, []
        self.rec = Recorder("maker", DecisionLog(tmp_path), True, self.lines.append)
        self.ledger = gr.Ledger(tmp_path / "ledger.jsonl")
        self.me: dict[str, Any] = deepcopy(team._me)
        self.tick = 100
        gate = StrategyGate(jev_yes, self.rec, rules.strategy_jev_refresh_ticks)
        self.desk = desk.SellDesk(team, rules, self.rec, True, self.lines.append, self.hooks, lambda s: market, gate)

    def context(self) -> gr.Context:
        return gr.context_from(self.me, self.tick, 1.5, self.ledger, self.rules)

    def hooks(self, c: desk.Candidate) -> desk.SellHooks:
        return desk.standard_hooks(
            c, rules=self.rules, rec=self.rec, ledger=self.ledger, context=self.context, catalog=CAT6, log=self.log
        )

    def log(self, line: str) -> None:
        self.lines.append(line)

    def on_tick(self, tick: int, locked: set[int] | None = None, **snap: Any) -> None:
        self.tick, self.me = tick, deepcopy(snap.pop("me", self.me))
        view = SimpleNamespace(
            clock=clock(tick=tick),
            me=deepcopy(self.me),
            catalog=CAT6,
            events=list(snap.pop("events", [])),
            dealers=deepcopy(snap.pop("dealers", API)),
            offers=snap.pop("offers", {"offers": []}),
            us=str(self.me.get("id") or ""),
        )
        self.desk.on_tick(view, PARAMS, set(locked or ()))


def opened(team: FakeTeam) -> list[tuple[Any, ...]]:
    return [s for s in team.sent if s[0] == "open_thread"]


def priced(team: FakeTeam) -> list[int]:
    return [s[2] for s in team.sent if s[0] == "say" and s[2] is not None]


def first_dealer(found: list[desk.Candidate], ref: str) -> str:
    return next(c.dealer for c in found if c.ref == ref)


# ---------------------------------------------------------------- 1. every set protected: no only, no last free copy


def test_the_committed_rules_protect_every_set_and_keep_the_desk_off():
    assert set(gr.set_codes(REAL.protect_page_sets)) >= set(EVERY_SET)
    assert REAL.dealer_sell_enabled is False  # turning it on is the coordinator's one-line change, not ours


def test_with_the_real_rules_no_only_copy_and_no_last_free_copy_of_any_set_is_a_candidate():
    locked = {31}  # MAL-02 #31 is in an open ask of ours
    for rules in (REAL, REAL.model_copy(update={"protect_page_sets": "none"})):  # the last free copy, whatever the list
        found = desk.candidates(ME6, CAT6, MARKET_AB, PARAMS, rules, locked=locked)
        assert {c.ref for c in found} == {"LAT-03"}, found  # two free copies: the one true spare
        assert all(c.asset_id not in locked for c in found)
    unlisted = desk.candidates(ME6, CAT6, MARKET_AB, PARAMS, REAL)
    assert {c.ref for c in unlisted} == {"LAT-03", "MAL-02"}  # the listing was what kept MAL-02 out


def test_an_open_offer_of_ours_inside_a_thread_also_makes_a_copy_not_free(tmp_path):
    """A team-swap offer (the taker's team desk) gives LAT-03 #4 in a team thread: the maker's `locked` lists board
    offers only, so the desk reads `/api/me/offers` itself and LAT-03 #3 is the last free copy."""
    me = {**deepcopy(ME), "unlocked": ["abuela"]}
    swap = {"id": 900, "maker": "t01", "to": "t07", "thread": 77, "status": "open", "venue": "rastro"}
    swap |= {"give": {"assets": [{"id": 4, "ref": "LAT-03"}], "cash": 0}, "want": {"types": ["card:MAL-02"]}}
    d = Desk(tmp_path, Seller("abuela", [5, 6, 7], me=me), live_rules(tmp_path), MARKET_AB)
    d.on_tick(100, offers={"offers": [swap]})
    assert opened(d.team) == []
    d.on_tick(101)  # the swap offer is gone: both copies are free again
    assert [s[1] for s in opened(d.team)] == ["abuela"]


@pytest.mark.parametrize("change", ["listed", "swapped", "gone"])
def test_mid_thread_the_desk_walks_once_its_copy_is_the_last_free_one(tmp_path, change):
    me = {**deepcopy(ME), "unlocked": ["abuela"]}  # LAT-03 #3 and #4: one spare
    d = Desk(tmp_path, Seller("abuela", [5, 6, 7], me=me), live_rules(tmp_path), MARKET_AB)
    d.on_tick(100)
    ((_, _, topic),) = opened(d.team)
    ours = topic["sell"]["assets"][0]
    other = ({3, 4} - {ours}).pop()
    if change == "listed":  # the maker listed the other copy on a board
        d.on_tick(101, locked={other})
    elif change == "swapped":  # the team desk offered it in a team swap
        give = {"assets": [{"id": other, "ref": "LAT-03"}], "cash": 0}
        swap = {"id": 900, "maker": "t01", "to": "t07", "thread": 77, "status": "open", "give": give, "want": {}}
        d.on_tick(101, offers={"offers": [swap]})
    else:  # it left /me (sold or swapped elsewhere)
        d.on_tick(101, me={**me, "assets": [a for a in me["assets"] if a.get("id") != other]})
    assert priced(d.team) == [] and not [s for s in d.team.sent if s[0] == "accept"]
    assert d.team.sent[-1][0] == "close_thread"  # walked: no ask of ours stands for that copy


def test_mid_thread_a_copy_still_spare_gets_its_ask(tmp_path):
    me = {**deepcopy(ME), "unlocked": ["abuela"]}
    d = Desk(tmp_path, Seller("abuela", [5, 6, 7], me=me), live_rules(tmp_path), MARKET_AB)
    d.on_tick(100)
    d.on_tick(101, locked={999})  # some other card is listed: our two copies are untouched
    assert len(priced(d.team)) == 1 and not [s for s in d.team.sent if s[0] == "close_thread"]


# ---------------------------------------------------------------- 2. the floor


def test_every_floor_covers_the_value_ratio_and_what_we_lose_plus_the_surplus():
    for rules in (
        REAL,
        REAL.model_copy(update={"sell_min_value_ratio": 3.0}),  # the ratio decides the floor
        REAL.model_copy(update={"dealer_sell_min_surplus": 0.0}),
    ):
        found = desk.candidates(ME5, CAT6, MARKET6, PARAMS, rules)
        assert found
        for c in found:
            assert c.floor >= math.ceil(c.your_value * rules.sell_min_value_ratio - 1e-9)
            assert c.value >= c.your_value and c.floor >= c.value + rules.dealer_sell_min_surplus
            plan = desk.plan_for(c.fill, c.floor, rules)
            assert plan.floor == c.floor and min(ask_schedule(plan)) >= c.floor
    # the page bonus at stake (an only copy's) is part of what we lose
    assert (
        desk.sell_floor(4.0 + 6.0, 4.0, REAL.dealer_sell_min_surplus, REAL) >= 4.0 + 6.0 + REAL.dealer_sell_min_surplus
    )


def test_the_guard_refuses_an_ask_or_an_accept_below_the_value_ratio(tmp_path):
    cand = desk.Candidate(5, "SAL-09", "rare", 35.0, 35.0, 37, "chato", 46.0, "El Chato")
    ctx = gr.Context(cash=50, held={"SAL-09": 2}, tick=100, t_hours=1.5, stops=(), breakers=frozenset())
    hooks = approval_hooks(tmp_path, cand, ctx)
    for kind in ("dealer_sell", "accept_sell"):
        assert hooks.guard(kind, 34) is not None and "your_value" in str(hooks.guard(kind, 34))
        assert hooks.guard(kind, 35) is None


@pytest.mark.parametrize("kind", ["dealer", "trickster"])
def test_no_ask_and_no_accept_below_the_floor_nor_a_deal_at_her_opening_bid(kind):
    """Properties 2 and 4 on 3,000 random threads: every ask at or above the floor and never the same twice in a row,
    every accept at or above the floor and strictly above her opening bid, finals and tricksters included."""
    rng = random.Random(20261003)
    accepts = 0
    for _ in range(3000):
        floor = rng.randint(1, 30)
        plan = AskPlan(floor + rng.randint(0, 30), rng.randint(1, 5), floor)
        neg, oid = SellNegotiation(plan), 700
        final_min = rng.choice([0, math.ceil(0.5 * plan.start)])
        for _ in range(12):
            bid = None if rng.random() < 0.2 else rng.randint(1, plan.start + 10)
            offer, oid = (None, oid) if bid is None else (oid, oid + 1)
            move = decide_sell(neg, bid, offer, rng.random() < 0.3, final_min, kind=kind)
            if move.kind == "bid":
                assert move.price is not None and move.price >= floor
                assert not neg.asks or move.price != neg.asks[-1]
                neg.asks.append(move.price)
            elif move.kind == "accept":
                assert move.price == bid and move.price >= floor
                assert neg.opening_bid is not None and move.price > neg.opening_bid
                accepts += 1
                break
            elif move.kind == "walk":
                break
    assert accepts > 100  # the walk is not the only way out: the invariants were exercised on real deals


# ---------------------------------------------------------------- 3. human approval


@pytest.fixture
def asked():
    """This process's approval board, with no database (fails closed); the requests kept in a list."""
    writes: list[dict[str, Any]] = []
    old = approvals.install(approvals.ApprovalBoard(None, write=writes.append))
    yield writes
    approvals._BOARD.pop("board", None)
    if old is not None:
        approvals.install(old)


def approval_hooks(tmp_path: Path, cand: desk.Candidate, ctx: gr.Context, rules: gr.Guardrails = REAL):
    rec = Recorder("maker", DecisionLog(tmp_path), True, lambda line: None)
    ledger = gr.Ledger(tmp_path / "ledger.jsonl")
    return desk.standard_hooks(
        cand, rules=rules, rec=rec, ledger=ledger, context=lambda: ctx, catalog=CAT6, log=lambda line: None
    )


# SAL-09: a rare with no protect_page_exceptions MIN (LAT-09 has one since SX1)
RARE = desk.Candidate(5, "SAL-09", "rare", 35.0, 35.0, 37, "chato", 46.0, "El Chato", FILLS[("chato", "rare")])


def book(*rows: approvals.Approval) -> approvals.ApprovalBook:
    return approvals.ApprovalBook({(a.card, a.side): a for a in rows})


def ctx_with(approved: approvals.ApprovalBook | None) -> gr.Context:
    held = {"SAL-09": 2}
    return gr.Context(cash=50, held=held, tick=100, t_hours=1.5, stops=(), breakers=frozenset(), approvals=approved)


@pytest.mark.human_approval
def test_a_dealer_sale_at_or_above_the_threshold_needs_an_approval_in_force(tmp_path, asked):
    top = REAL.human_approval_above
    assert top > 0  # GUARDRAILS.md turns it on (60 on Sat 3 Oct)
    hooks = approval_hooks(tmp_path, RARE, ctx_with(book()))
    for kind in ("dealer_sell", "accept_sell"):  # our ask, and our accept of her bid
        assert hooks.guard(kind, top) == f"needs human approval: SAL-09 sell {top}"
        assert hooks.guard(kind, top + 9) == f"needs human approval: SAL-09 sell {top + 9}"
        assert hooks.guard(kind, top - 1) is None  # under the threshold no human is asked
    assert [(r["card"], r["side"], r["price"]) for r in asked] == [("SAL-09", "sell", top)]  # asked once
    approved = approval_hooks(tmp_path, RARE, ctx_with(book(approvals.Approval("SAL-09", "sell", None, top, 200))))
    for kind in ("dealer_sell", "accept_sell"):
        assert approved.guard(kind, top) is None and approved.guard(kind, top + 9) is None
    for wrong in (
        approvals.Approval("LAT-03", "sell", None, top, 200),  # another card
        approvals.Approval("SAL-09", "buy", top + 50, None, 200),  # the other side
        approvals.Approval("SAL-09", "sell", None, top + 10, 200),  # a minimum above our price
        approvals.Approval("SAL-09", "sell", None, top, 100),  # expired at this tick
    ):
        hooks = approval_hooks(tmp_path, RARE, ctx_with(book(wrong)))
        assert all(hooks.guard(kind, top) is not None for kind in ("dealer_sell", "accept_sell")), wrong


@pytest.mark.human_approval
def test_a_sell_thread_without_an_approval_never_opens_at_a_big_ask(tmp_path, asked):
    team = Seller("chato", [50, 55], me=ME6)
    hooks = approval_hooks(tmp_path, RARE, ctx_with(book()), AT60)
    t = desk.SellTalk(team, RARE, desk.plan_for(RARE.fill, RARE.floor, AT60), hooks)
    assert t.plan.start >= AT60.human_approval_above
    t.step(clock(tick=100))
    assert team.sent == [] and t.status == "refused"


@pytest.mark.human_approval
def test_unreadable_approvals_hold_the_sell_thread_never_walk_it(tmp_path, asked):
    top = AT60.human_approval_above
    hooks = approval_hooks(tmp_path, RARE, ctx_with(None), AT60)  # the board has no database: unreadable
    for kind in ("dealer_sell", "accept_sell"):
        with pytest.raises(Hold, match="approvals unreadable"):
            hooks.guard(kind, top)
    assert hooks.guard("dealer_sell", top - 1) is None  # a small sale needs no approval: nothing to read
    team = Seller("chato", [50, 55], me=ME6)
    t = desk.SellTalk(team, RARE, desk.plan_for(RARE.fill, RARE.floor, AT60), hooks)
    t.step(clock(tick=100))
    assert team.sent == [] and t.status == "refused"  # no thread yet: the hold ends the talk unopened (#227)


# ---------------------------------------------------------------- 4. never a deal at her opening bid
# tests/test_dealer_sell.py::test_never_closes_at_her_opening_bid_and_counters_above_it and
# ::test_a_final_at_her_opening_bid_is_walked, plus the 3,000 random threads above.


def test_a_trickster_final_at_her_opening_bid_is_never_taken_either():
    neg = SellNegotiation(AskPlan(20, 2, 8), asks=[20, 18])
    move = decide_sell(neg, 16, 701, True, kind="trickster")  # her first bid, FINAL, meets our next ask (16)
    assert move.kind != "accept"


# ---------------------------------------------------------------- 5. a trickster's final is no limit


def test_a_trickster_final_reads_exactly_as_an_ordinary_bid():
    rng = random.Random(5)
    honest_differs = 0
    for _ in range(2000):
        floor = rng.randint(1, 20)
        plan = AskPlan(floor + rng.randint(0, 20), rng.randint(1, 4), floor)
        neg = SellNegotiation(plan, asks=ask_schedule(plan)[: rng.randint(0, 4)])
        for oid in range(600, 600 + rng.randint(0, 3)):
            neg.see_bid(rng.randint(1, plan.start), oid)
        bid, final_min = rng.randint(1, plan.start + 5), rng.choice([0, math.ceil(0.5 * plan.start)])
        trick, plain, honest = deepcopy(neg), deepcopy(neg), deepcopy(neg)
        assert decide_sell(trick, bid, 650, True, final_min, kind="trickster") == decide_sell(
            plain, bid, 650, False, final_min
        )
        assert trick == plain
        honest_differs += decide_sell(honest, bid, 650, True, final_min) != decide_sell(
            deepcopy(neg), bid, 650, False, final_min
        )
    assert honest_differs > 100  # a real dealer's final does change the move: the comparison can tell


def test_a_trickster_final_neither_walks_us_nor_closes_the_deal_below_our_next_ask():
    plan = AskPlan(20, 2, 10)
    walked = SellNegotiation(plan, asks=[20])
    assert decide_sell(walked, 6, 700, True).kind == "walk"  # Abuela's final under our floor: she walks
    assert decide_sell(SellNegotiation(plan, asks=[20]), 6, 700, True, kind="trickster") == Move(
        "bid", 18, reason="small distinct step down"
    )
    taken = SellNegotiation(plan, asks=[20])
    taken.see_bid(8, 699)
    assert decide_sell(taken, 12, 700, True).kind == "accept"  # Abuela's final above our floor: taken
    stepped = SellNegotiation(plan, asks=[20])
    stepped.see_bid(8, 699)
    assert decide_sell(stepped, 12, 700, True, 10, kind="trickster").price == 18  # Los Pícaros: we step down


def test_the_desk_tells_the_thread_its_dealer_is_a_trickster_and_plays_through_its_finals(tmp_path):
    me = {**deepcopy(ME_DUP), "unlocked": ["picaros"]}
    team = Seller("picaros", [5, 6, 7, 19], final_from=0, me=me)  # every bid says FINAL
    d = Desk(tmp_path, team, live_rules(tmp_path))
    for tick in range(100, 112):
        d.on_tick(tick)
        if d.desk.talk is None:
            break
        assert d.desk.talk.dealer_kind == "trickster"
    assert priced(team) == [24, 22, 20, 18]  # her "finals" 5, 6, 7 never stopped the ladder
    assert [s for s in team.sent if s[0] == "accept"] == [("accept", 703)]  # her 19 met our next ask: a deal
    assert not [s for s in team.sent if s[0] == "close_thread"]


def test_an_honest_dealer_keeps_its_final_and_an_unknown_kind_is_a_dealer():
    assert desk.dealer_kind(API, "abuela") == "dealer" and desk.dealer_kind(API, "pilar") == "collector"
    assert desk.dealer_kind(API, "picaros") == "trickster"
    assert desk.dealer_kind([], "picaros") == "dealer" and desk.dealer_kind(None, "x") == "dealer"


# ---------------------------------------------------------------- 6. the level follows the ladder's scoring


def test_a_copy_goes_to_the_highest_level_dealer_that_buys_it():
    for personas in (None, PERSONAS):
        found = desk.candidates(ME5, CAT6, MARKET6, PARAMS, REAL, personas=personas)
        assert first_dealer(found, "LAT-03") == "picaros"  # a common: L4 over Abuela (L1), though she pays more
        assert first_dealer(found, "LAV-08") == "picaros"  # an uncommon: L4 over Pilar, Chato and Abuela
        assert found[0].dealer == "picaros" and found[0].level == 4
        assert all(c.dealer != "banco" for c in found)  # L5 buys only epics and legendaries
        assert all(c.dealer != "chato" for c in found if c.rarity == "common")  # its menu buys no common


def test_a_level_that_never_bid_our_floor_is_skipped_for_the_next_one_down():
    fills = {**FILLS, ("picaros", "uncommon"): dd.Fill(2, 3.0, 3, 2, 3.0)}  # never above 3: our floor is 4
    market = dd.SellMarket(MARKET6.traders, fills, "test")
    found = desk.candidates(ME5, CAT6, market, PARAMS, REAL, personas=PERSONAS)
    assert first_dealer(found, "LAV-08") == "pilar" and first_dealer(found, "LAT-03") == "picaros"


def test_banco_is_never_picked_for_a_page_rarity_even_when_its_data_row_says_so():
    stale = [
        {**r, "menu": {"buys": lines_for("common", "uncommon", "rare")}} if r["id"] == "banco" else r for r in ROWS
    ]
    market = dd.SellMarket(tuple(t for t in map(dd.trader_from, stale) if t is not None), FILLS, "test")
    found = desk.candidates(ME5, CAT6, market, PARAMS, REAL, personas=PERSONAS)  # its published menu wins
    assert found and all(c.dealer != "banco" for c in found)


def test_a_full_level_today_yields_to_one_with_fewer_than_three_deals():
    found = desk.candidates(ME5, CAT6, MARKET6, PARAMS, REAL, personas=PERSONAS, deals={"picaros": 3})
    assert first_dealer(found, "LAV-08") == "pilar" and first_dealer(found, "LAT-03") == "abuela"
    two = desk.candidates(ME5, CAT6, MARKET6, PARAMS, REAL, personas=PERSONAS, deals={"picaros": 2})
    assert first_dealer(two, "LAV-08") == "picaros"  # two deals: a slot is still empty


def sell_thread(eid: int, thread: int, dealer: str, asset: int, opening: int, fill: int, tick: int, team="t01"):
    """Our sell thread in the feed: opened, the dealer's opening bid, the settlement at `fill`."""
    offer = {"id": eid + 1, "maker": dealer, "give": {"cash": opening}, "want": {"assets": [{"id": asset}]}}
    items = [{"id": asset, "kind": "card", "ref": "LAT-03", "frm": team, "to": dealer}]
    start = {"thread": thread, "kind": "persona", "team": team, "with": dealer, "topic": sell_topic(asset)}
    said = {"thread": thread, "kind": "persona", "sender": dealer, "team": team, "offer": offer}
    settled = {"settlement": eid + 2, "parties": [team, dealer], "persona": dealer, "price": fill, "items": items}
    return [
        {"id": eid, "tick": tick, "type": "thread.opened", "payload": start},
        {"id": eid + 1, "tick": tick, "type": "thread.message", "payload": said},
        {"id": eid + 2, "tick": tick + 2, "type": "settlement", "payload": settled},
    ]


def sell_topic(asset: int) -> dict[str, Any]:
    return {"sell": {"assets": [asset]}}


def scored_today(dealer: str, n: int, since_tick: int = 200) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = [{"id": 1, "tick": since_tick, "type": "day.opened", "payload": {"day": "sat"}}]
    for k in range(n):
        out += sell_thread(10 + 3 * k, 50 + k, dealer, 300 + k, 5, 7, since_tick + 5 + k)
    return out


def test_ladder_deals_counts_our_scored_dealer_deals_of_today_only():
    events = [
        *sell_thread(2, 40, "picaros", 101, 5, 7, 10),  # Friday: before the day opened
        {"id": 50, "tick": 40, "type": "day.opened", "payload": {"day": "sat"}},
        *sell_thread(60, 41, "picaros", 102, 5, 7, 60),  # a sale today
        *sell_thread(70, 42, "picaros", 103, 5, 5, 70),  # at her opening bid: it scores nothing
        *sell_thread(80, 43, "abuela", 104, 5, 6, 80),
        *sell_thread(90, 44, "chato", 105, 5, 8, 90, team="t07"),  # another team's
    ]
    assert desk.ladder_deals(events, "t01") == {"picaros": 1, "abuela": 1}
    assert desk.ladder_deals(events, "") == {}


def test_ladder_deals_restart_when_a_round_starts_mid_day():
    # The ladder restarts every round, and a round may start in the middle of a day (Sunday's schedule).
    events = [
        {"id": 50, "tick": 40, "type": "day.opened", "payload": {"day": "sun"}},
        *sell_thread(60, 41, "picaros", 102, 5, 7, 60),  # round 2, this morning
        {"id": 75, "tick": 75, "type": "round.started", "payload": {"round": 3}},
        *sell_thread(80, 43, "abuela", 104, 5, 6, 80),  # round 3
    ]
    assert desk.ladder_deals(events, "t01") == {"abuela": 1}


def test_the_desk_opens_with_the_highest_level_not_yet_full_today(tmp_path):
    first = Desk(tmp_path / "a", Seller("picaros", [5], me=ME5), live_rules(tmp_path / "a"))
    first.on_tick(100)
    assert [s[1] for s in opened(first.team)] == ["picaros"]
    full = Desk(tmp_path / "b", Seller("pilar", [5], me=ME5), live_rules(tmp_path / "b"))
    full.on_tick(300, events=scored_today("picaros", 3))
    assert [s[1] for s in opened(full.team)] == ["pilar"]


def test_without_the_database_collector_trickster_and_banker_dealers_are_still_known():
    market = dd.market_from_feed(API, [], {})
    assert {t.id: t.level for t in market.traders} == {"abuela": 1, "chato": 2, "pilar": 3, "picaros": 4, "banco": 5}
    assert (
        dd.trader_from({"id": "t07", "kind": "team"}) is None and dd.trader_from({"id": "b1", "kind": "bench"}) is None
    )


def test_a_dealer_never_seen_bidding_for_the_rarity_is_never_a_candidate():
    """Report only: `fill is None` skips a dealer no sell thread has shown a bid from (a new L5 banco), whatever its
    level; it becomes a candidate once one opening bid or fill is in the data."""
    fills = {k: v for k, v in FILLS.items() if k[0] != "picaros"}
    market = dd.SellMarket(MARKET6.traders, fills, "test")
    found = desk.candidates(ME5, CAT6, market, PARAMS, REAL, personas=PERSONAS)
    assert all(c.dealer != "picaros" for c in found)


# ---------------------------------------------------------------- 7. the kill switch holds every send


def test_the_pause_file_holds_every_send_of_the_desk_and_it_resumes_where_it_was(tmp_path):
    pause = tmp_path / "PAUSE"
    me = {**deepcopy(ME), "unlocked": ["abuela"]}
    d = Desk(tmp_path, Seller("abuela", [5, 8, 10], me=me), live_rules(tmp_path), MARKET6)
    kinds = lambda: [s[0] for s in d.team.sent]  # noqa: E731
    pause.touch()
    d.on_tick(100)
    assert kinds() == []  # no thread opened
    pause.unlink()
    d.on_tick(101)
    assert kinds() == ["open_thread"]
    pause.touch()
    d.on_tick(102)
    assert kinds() == ["open_thread"]  # no ask
    pause.unlink()
    for tick in (103, 104, 105):
        d.on_tick(tick)
    assert priced(d.team) == [12, 11, 10]  # her 10 now meets our next ask: the accept is due
    pause.touch()
    d.on_tick(106)
    assert not [s for s in d.team.sent if s[0] in ("accept", "close_thread")]  # no accept, no walk
    pause.unlink()
    d.on_tick(107)
    assert [s for s in d.team.sent if s[0] == "accept"] == [("accept", 702)]


def test_a_kill_switch_seen_only_by_the_guard_holds_and_never_walks(tmp_path):
    """The step read the switch off, then it went on before the send: `guardrails.check` says halted. The thread
    stays as it is: no ask, no accept, and no walk (a walk is two writes)."""
    rules = live_rules(tmp_path)
    team = Seller("abuela", [5, 8, 10], me={**deepcopy(ME), "unlocked": ["abuela"]})
    cand = desk.Candidate(
        3, "LAT-03", "common", 1.2, 1.2, 4, "abuela", 6.0, "Abuela Carmen", FILLS[("abuela", "common")]
    )
    ctx = {"now": gr.Context(cash=50, held={"LAT-03": 2}, tick=100, t_hours=1.5, stops=(), breakers=frozenset())}
    rec = Recorder("maker", DecisionLog(tmp_path), True, lambda line: None)
    ledger = gr.Ledger(tmp_path / "ledger.jsonl")
    hooks = desk.standard_hooks(
        cand, rules=rules, rec=rec, ledger=ledger, context=lambda: ctx["now"], catalog=CAT6, log=lambda line: None
    )
    hooks.kill_switch = lambda: ()  # the step's own read: off
    t = desk.SellTalk(team, cand, desk.plan_for(cand.fill, cand.floor, rules), hooks)
    t.step(clock(tick=100))
    assert [s[0] for s in team.sent] == ["open_thread"]
    ctx["now"] = gr.Context(cash=50, held={"LAT-03": 2}, tick=101, t_hours=1.5, stops=("pause file exists",))
    t.step(clock(tick=101))  # the ask is due: the guard sees the switch on
    assert [s[0] for s in team.sent] == ["open_thread"] and t.status == "open"
    ctx["now"] = gr.Context(cash=50, held={"LAT-03": 2}, tick=102, t_hours=1.5, stops=(), breakers=frozenset())
    t.step(clock(tick=102))
    assert priced(team) == [12]  # resumed where it was
