"""The maker's dealer sell desk (on #179's `decide_sell`): the opening plan from data, the structure checks on
a real sell-thread offer, the candidates, the maker's switch, one thread end to end (finals, walks, a deal in
words only), and the dealer data from `traders` / `dealer_curves` or the feed."""

from copy import deepcopy

from bazaar_agent.agents import dealer_sell_data as dd
from bazaar_agent.agents import dealer_sell_desk as desk
from bazaar_agent.agents.accept_gate import dealer_gate
from bazaar_agent.agents.dealer_sell import ask_schedule, latest_dealer_bid, sell_topic
from bazaar_agent.agents.inspector import CardIndex
from bazaar_agent.agents.maker import Maker
from bazaar_agent.agents.runtime import JevAdvice, Recorder
from bazaar_agent.decisions import DecisionLog
from bazaar_agent.guardrails import Action, Context, Guardrails, Ledger, check
from tests.agent_fakes import FakePublic, FakeTeam, clock, parts, rows
from tests.test_strategy import ABUELA, CATALOG, ME, PARAMS

# A real dealer bid on a sell thread (Abuela, thread 32, Sat 3 Oct; the copy re-numbered to our fixture's).
REAL_BID = {
    "id": 140,
    "to": "t01",
    "give": {"cash": 5, "types": [], "assets": []},
    "want": {
        "cash": 0,
        "types": [],
        "assets": [{"id": 8, "ref": "LAV-08", "set": "LAV", "kind": "card", "rarity": "uncommon", "serial": 7}],
    },
    "final": False,
    "maker": "abuela",
    "venue": None,
    "status": "open",
    "thread": 32,
    "created_tick": 13,
    "expires_tick": 15,
}

ABUELA_BUYS = {**ABUELA, "menu": {**ABUELA["menu"], "buys": [{"rarity": "common"}, {"rarity": "uncommon"}]}}
CHATO_BUYS = {
    "id": "chato",
    "status": "active",
    "level": 2,
    "menu": {"buys": [{"rarity": "uncommon"}, {"rarity": "rare"}]},
}
DEALERS = [ABUELA_BUYS, CHATO_BUYS]

# Fake `traders` rows and sell rows of `dealer_curves` (dealer, item, rarity, opening bid, fill, outcome).
TRADER_ROWS = [
    {
        "id": "abuela",
        "kind": "dealer",
        "name": "Abuela Carmen",
        "level": 1,
        "status": "active",
        "menu": {
            "buys": [{"rarity": "common", "sets": "released"}, {"rarity": "uncommon", "sets": "released"}],
            "deals_per_team_per_hour": 8,
        },
        "unlock": {"always": True},
    },
    {
        "id": "chato",
        "kind": "dealer",
        "name": "El Chato",
        "level": 2,
        "status": "active",
        "menu": {
            "buys": [{"rarity": "uncommon", "sets": "released"}, {"rarity": "rare", "sets": "released"}],
            "deals_per_team_per_hour": 6,
        },
        "unlock": {"always": False},
    },
    {
        "id": "pilar",
        "kind": "dealer",
        "name": "Doña Pilar",
        "level": 3,
        "status": "active",
        "menu": {"buys": [{"rarity": "rare", "sets": ["LAT"]}], "deals_per_team_per_hour": 6},
        "unlock": {"always": False},
    },
]
CURVE_ROWS = [
    ("abuela", "assets:175", "common", 5, 5, "deal"),
    ("abuela", "assets:176", "common", 5, 6, "deal"),
    ("abuela", "assets:300", "uncommon", 12, 13, "deal"),
    ("abuela", "assets:301", "uncommon", 12, 21, "deal"),
    ("abuela", "assets:302", "uncommon", 13, None, "walked"),
    ("chato", "assets:448", "rare", 39, 46, "deal"),
    ("chato", "assets:449", "uncommon", 13, 14, "deal"),
    ("pilar", "assets:680", "rare", 16, None, "walked"),  # no fill: her opening bid stands in
    ("abuela", "assets:1,2", "common", 9, 9, "deal"),  # several assets: not a single-card sell row
]


class FakeCursor:
    def __init__(self):
        self.description = None
        self._rows = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        if "from traders" in sql:
            cols = ["id", "kind", "name", "level", "menu", "unlock", "status"]
            self.description = [(c,) for c in cols]
            self._rows = [tuple(r.get(c) for c in cols) for r in TRADER_ROWS]
        else:
            self._rows = [r for r in CURVE_ROWS if "," not in r[1]]  # the SQL keeps one asset per item

    def fetchall(self):
        return list(self._rows)


class FakeConn:
    closed = False

    def cursor(self):
        return FakeCursor()

    def close(self):
        self.closed = True


MARKET = dd.market_from_db(FakeConn())

# Three copies of LAV-08 (an uncommon): the maker may list one, and one more is a spare.
ME_DUP = deepcopy(ME)
ME_DUP["assets"] += [
    {"id": 7, "kind": "card", "ref": "LAV-08", "rarity": "uncommon", "your_value": 6.0},
    {"id": 8, "kind": "card", "ref": "LAV-08", "rarity": "uncommon", "your_value": 2.0},
    {"id": 9, "kind": "card", "ref": "LAV-08", "rarity": "uncommon", "your_value": 2.0},
]


def bid_offer(cash, oid=140, final=False, assets=(8,)):
    o = deepcopy(REAL_BID)
    o.update(id=oid, final=final)
    o["give"]["cash"] = cash
    o["want"]["assets"] = [{"id": a, "ref": "LAV-08", "kind": "card"} for a in assets]
    return o


# ---------------------------------------------------------------- the opening plan (from data)


def test_plan_opens_above_every_fill_seen_and_steps_down_to_the_floor():
    fill = MARKET.fills[("abuela", "uncommon")]  # fills 13 and 21: the median (17) × 2 does not cap 1.6 × 21
    plan = desk.plan_for(fill, 11, Guardrails())
    asks = ask_schedule(plan)
    assert asks[0] > fill.top and asks[-1] == 11
    assert all(a > b for a, b in zip(asks, asks[1:], strict=False))
    assert desk.plan_for(None, 10, Guardrails()).start == 20  # nothing seen: twice the floor


# ---------------------------------------------------------------- structure: what we accept


def test_a_real_bid_reads_and_another_copy_is_ignored():
    thread = {"standing_offers": [bid_offer(14)]}
    assert latest_dealer_bid(thread, "abuela", 8) == (14, 140, False)
    assert latest_dealer_bid(thread, "abuela", 7) == (None, None, False)


def test_inspector_gate_reads_the_cash_a_sell_offer_gives():
    cards = CardIndex.from_catalog(CATALOG)
    topic = sell_topic(8)
    good = {"standing_offers": [bid_offer(14)], "messages": []}
    assert dealer_gate(good, "abuela", 140, 14, topic, cards).allowed
    assert not dealer_gate(good, "abuela", 140, 15, topic, cards).allowed  # not the price we decided on
    other = {"standing_offers": [bid_offer(14, assets=(7,))], "messages": []}
    assert not dealer_gate(other, "abuela", 140, 14, topic, cards).allowed  # wants another copy of ours


def test_guardrails_refuse_a_dealer_sale_below_our_value_and_on_the_kill_switch():
    ctx = Context(cash=50, held={"LAV-08": 2}, tick=1, t_hours=1.0, stops=())
    rules = Guardrails()
    assert check(Action("dealer_sell", "LAV-08", "uncommon", 12, your_value=10.0), ctx, rules).allowed
    assert not check(Action("dealer_sell", "LAV-08", "uncommon", 9, your_value=10.0), ctx, rules).allowed
    halted = check(
        Action("dealer_sell", "LAV-08", "uncommon", 12, your_value=1.0),
        Context(**{**ctx.__dict__, "stops": ("x",)}),
        rules,
    )
    assert halted.halted


# ---------------------------------------------------------------- candidates


def test_candidates_are_unlisted_spares_a_free_dealer_buys():
    found = desk.candidates(ME_DUP, CATALOG, MARKET, PARAMS, Guardrails())
    assert found and found[0].ref == "LAV-08" and found[0].dealer == "abuela"
    assert found[0].floor >= found[0].value + Guardrails().dealer_sell_min_surplus
    assert all(c.ref != "LAV-01" for c in found)  # our only copy of a boosted set: not a spare
    assert not [c for c in desk.candidates(ME_DUP, CATALOG, MARKET, PARAMS, Guardrails(), busy={"abuela", "chato"})]
    unlocked = desk.candidates(ME_DUP, CATALOG, MARKET, PARAMS, Guardrails(), locked={8})
    assert all(c.asset_id != 8 for c in unlocked)


def test_a_protected_only_copy_is_never_a_candidate():
    found = desk.candidates(ME_DUP, CATALOG, MARKET, PARAMS, Guardrails(protect_page_sets="LAT"))
    assert all(c.ref not in ("LAT-03", "LAT-09") or c.asset_id in (3, 4) for c in found)
    assert all(c.ref != "LAT-09" for c in found)  # LAT-09: our only copy of a protected page


# ---------------------------------------------------------------- the maker's switch


def jev_yes(name, state):
    return JevAdvice("yes", 0.9)


def maker(tmp_path, team, *, live, strategy_jev=jev_yes, **rules):
    lines: list[str] = []
    public = FakePublic(dealers=DEALERS)
    m = Maker(
        team,
        public,
        live=live,
        log=lines.append,
        now=lambda: 1000.0,
        sell_market=lambda snap: MARKET,
        strategy_jev=strategy_jev,
        **parts(tmp_path, **rules),
    )
    return m, lines


def opened(team):
    return [s for s in team.sent if s[0] == "open_thread"]


def test_switch_off_by_default_never_reads_threads_nor_opens_one(tmp_path):
    assert Guardrails().dealer_sell_enabled is False
    team = FakeTeam(me=ME_DUP)
    maker(tmp_path, team, live=True)[0].on_tick(clock())
    assert opened(team) == [] and "my_threads" not in team.reads


def test_switch_on_opens_one_sell_thread_for_an_unlisted_spare(tmp_path):
    team = FakeTeam(me=ME_DUP)
    m, _ = maker(tmp_path, team, live=True, dealer_sell_enabled=True)
    m.on_tick(clock())
    [(_, dealer, topic)] = opened(team)
    assert dealer in ("abuela", "chato") and list(topic) == ["sell"]
    [aid] = topic["sell"]["assets"]
    listed = {s[1]["assets"][0] for s in team.sent if s[0] == "list_offer" and s[1].get("assets")}
    assert aid not in listed
    assert not [s for s in team.sent if s[0] == "say"]  # one write per tick: the opening ask comes next tick
    m.on_tick(clock(tick=101))
    assert [s for s in team.sent if s[0] == "say"]
    assert len(opened(team)) == 1  # one sell thread at a time
    kinds = [r.get("kind") for r in rows(tmp_path)]
    assert "dealer_sell" in kinds


def test_switch_on_but_every_dealer_busy_opens_nothing(tmp_path):
    team = FakeTeam(me=ME_DUP, threads=[{"id": 1, "with": "abuela"}, {"id": 2, "with": "chato"}])
    maker(tmp_path, team, live=True, dealer_sell_enabled=True)[0].on_tick(clock())
    assert opened(team) == []


def test_switch_on_in_dry_run_records_a_would_open_and_sends_nothing_to_dealers(tmp_path):
    team = FakeTeam(me=ME_DUP)
    maker(tmp_path, team, live=False, dealer_sell_enabled=True)[0].on_tick(clock())
    assert opened(team) == []
    assert any(r.get("kind") == "dealer_sell" and r.get("dry_run") for r in rows(tmp_path))


def test_kill_switch_holds_the_sell_desk(tmp_path, monkeypatch):
    import bazaar_agent.agents.maker as maker_mod
    import bazaar_agent.guardrails as gr

    monkeypatch.setattr(maker_mod, "kill_switch", lambda rules, path=None: ("test switch",))
    monkeypatch.setattr(gr, "kill_switch", lambda rules, path=None: ("test switch",))
    team = FakeTeam(me=ME_DUP)
    maker(tmp_path, team, live=True, dealer_sell_enabled=True)[0].on_tick(clock())
    assert opened(team) == [] and team.sent == []


def test_kill_switch_mid_thread_sends_nothing(tmp_path, monkeypatch):
    import bazaar_agent.guardrails as gr

    team = Dealer([12, 13, 14])
    t, _ = talk(tmp_path, team, floor=14)
    t.step(clock(tick=100))
    sent = list(team.sent)
    monkeypatch.setattr(gr, "kill_switch", lambda rules, path=None: ("test switch",))
    t.hooks.kill_switch = lambda: ("test switch",)
    t.step(clock(tick=101))
    assert team.sent == sent and t.status == "open"


# ---------------------------------------------------------------- one thread, end to end


class Dealer(FakeTeam):
    """Abuela on a sell thread: answers each of our asks with a bid, and a final on her third."""

    def __init__(self, bids, **kw):
        super().__init__(**kw)
        self.bids = list(bids)
        self.standing = []
        self.status = "open"

    def say(self, tid, text="", price=None, offer=None, topic=None):
        out = super().say(tid, text, price)
        if price is not None and self.bids:
            cash = self.bids.pop(0)
            self.standing = [bid_offer(cash, oid=200 + len(self.bids), final=not self.bids)]
        return out

    def thread(self, tid):
        return {"id": tid, "status": self.status, "standing_offers": deepcopy(self.standing), "messages": []}

    def accept(self, offer_id, assets=None):
        self.status = "deal"
        self.standing[0]["status"] = "accepted"
        return super().accept(offer_id)


def talk(tmp_path, team, floor, rules=None):
    rules = rules or Guardrails()
    fill = MARKET.fills[("abuela", "uncommon")]
    cand = desk.Candidate(8, "LAV-08", "uncommon", 2.0, 2.0, floor, "abuela", fill.expected, "Abuela Carmen", fill)
    decisions = DecisionLog(tmp_path)
    rec = Recorder("maker", decisions, True, lambda line: None)
    ledger = Ledger(tmp_path / "ledger.jsonl")
    ctx = Context(cash=50, held={"LAV-08": 2}, tick=100, t_hours=1.0, stops=())
    hooks = desk.standard_hooks(
        cand, rules=rules, rec=rec, ledger=ledger, context=lambda: ctx, catalog=CATALOG, log=lambda line: None
    )
    return desk.SellTalk(team, cand, desk.plan_for(fill, floor, rules), hooks), ledger


def test_end_to_end_takes_her_final_at_the_floor_and_records_every_step(tmp_path):
    team = Dealer([12, 13, 14])
    t, ledger = talk(tmp_path, team, floor=14)
    for tick in range(100, 110):
        t.step(clock(tick=tick))
        if t.done:
            break
    assert t.status == "deal" and t.price == 14
    assert ("accept", 200) in team.sent
    assert ledger.accepts_in_tick(103) == 1 or any(ledger.accepts_in_tick(k) for k in range(100, 110))
    made = rows(tmp_path)
    assert any((r.get("move") or {}).get("deal", {}).get("price") == 14 for r in made)  # the sale is recorded
    assert any(r.get("kind") == "dealer_sell" and "open_thread" in (r.get("move") or {}) for r in made)
    assert any(r["sdk_method"] == "accept" for r in rows(tmp_path, "executions.jsonl"))  # and its execution row


def test_end_to_end_walks_when_her_final_is_below_the_floor(tmp_path):
    team = Dealer([10, 11, 12])
    t, _ = talk(tmp_path, team, floor=14)
    for tick in range(100, 110):
        t.step(clock(tick=tick))
        if t.done:
            break
    assert t.status == "walked"
    assert not [s for s in team.sent if s[0] == "accept"]
    assert all(p is None or p >= 14 for (k, _, p) in (s for s in team.sent if s[0] == "say"))


# ---------------------------------------------------------------- dealer data from the database and the feed


def test_market_from_db_reads_traders_and_sell_fills():
    assert {t.id: t.greeting for t in MARKET.traders} == {
        "abuela": "Abuela Carmen",
        "chato": "El Chato",
        "pilar": "Doña Pilar",
    }
    assert MARKET.trader("chato").deals_per_hour == 6
    assert MARKET.fills[("abuela", "uncommon")] == dd.Fill(12, 17.0, 21, 2, 17.0)
    assert MARKET.fills[("abuela", "common")].top == 6  # the several-asset row is not a single sell
    assert MARKET.fills[("pilar", "rare")] == dd.Fill(16, 16.0, 16, 0, 16.0)  # no fill: her opening bid


def test_only_unlocked_dealers_buy_and_an_unlock_adds_one():
    me = {"unlocked": ["abuela"]}
    assert [t.id for t in MARKET.buyers(me, "uncommon", "LAV", ("LAV", "LAT"))] == ["abuela"]
    assert MARKET.buyers(me, "rare", "LAT", ("LAV", "LAT")) == []
    me = {"unlocked": ["abuela", "chato", "pilar"]}
    assert [t.id for t in MARKET.buyers(me, "rare", "LAT", ("LAV", "LAT"))] == ["chato", "pilar"]
    assert [t.id for t in MARKET.buyers(me, "rare", "LAV", ("LAV", "LAT"))] == ["chato"]  # pilar: LAT only


def test_a_dealer_unlocked_later_appears_as_a_buyer_of_our_spare():
    me = deepcopy(ME_DUP)
    assert all(c.dealer != "chato" for c in desk.candidates(me, CATALOG, MARKET, PARAMS, Guardrails()))
    me["unlocked"] = ["abuela", "chato"]
    found = desk.candidates(me, CATALOG, MARKET, PARAMS, Guardrails())
    assert any(c.dealer == "chato" and c.ref == "LAV-08" and c.name == "El Chato" for c in found)


def test_the_desk_reads_the_dealer_data_again_every_refresh(tmp_path):
    calls = []

    def load(snap):
        calls.append(snap.clock.tick)
        return MARKET

    sell_desk = desk.SellDesk(
        FakeTeam(me=ME_DUP), Guardrails(dealer_sell_enabled=True), None, False, lambda line: None, lambda c: None, load
    )

    class Snap:
        def __init__(self, tick):
            self.clock = clock(tick=tick)

    for tick in (100, 101, 100 + dd.REFRESH_TICKS):
        sell_desk.market_for(Snap(tick))
    assert calls == [100, 100 + dd.REFRESH_TICKS]


def test_without_the_database_the_feed_window_gives_the_fills():
    events = [
        {
            "id": 1,
            "tick": 10,
            "type": "thread.opened",
            "payload": {
                "kind": "persona",
                "team": "t12",
                "with": "abuela",
                "topic": {"sell": {"assets": [175]}},
                "thread": 32,
            },
        },
        {
            "id": 2,
            "tick": 11,
            "type": "thread.message",
            "payload": {
                "kind": "persona",
                "thread": 32,
                "sender": "abuela",
                "offer": {"give": {"cash": 5}, "want": {"assets": [{"id": 175}]}},
            },
        },
        {
            "id": 3,
            "tick": 14,
            "type": "settlement",
            "payload": {
                "kind": "trade",
                "persona": "abuela",
                "parties": ["abuela", "t12"],
                "price": 6,
                "settlement": 9,
                "items": [
                    {"id": 175, "frm": "t12", "to": "abuela", "ref": "LAT-03", "rarity": "common", "kind": "card"}
                ],
            },
        },
    ]
    market = dd.market_from_feed(TRADER_ROWS, events, {})
    assert market.source == "api+feed"
    fill = market.fills.get(("abuela", "common"))
    assert fill is not None and fill.opening == 5


def test_a_dealer_taking_our_ask_in_words_only_is_a_deal_when_our_copy_leaves_me(tmp_path):
    team = Dealer([12, 12, 12, 12])
    t, _ = talk(tmp_path, team, floor=14)
    t.step(clock(tick=100))  # open + the opening ask
    t.step(clock(tick=101), {"assets": [{"id": 8}]})
    assert t.status == "open"
    t.step(clock(tick=102), {"assets": []})  # no deal status on the thread, but the copy is gone
    assert t.status == "deal" and t.price == t.neg.asks[-1]
    assert any((r.get("move") or {}).get("deal") for r in rows(tmp_path))


def test_the_last_free_copy_of_a_page_card_is_never_a_candidate():
    found = desk.candidates(ME_DUP, CATALOG, MARKET, PARAMS, Guardrails(), locked={7, 9})
    assert all(c.ref != "LAV-08" for c in found)  # copies 7 and 9 are listed: copy 8 is the last free one


# ---------------------------------------------------------------- Sat 3 Oct: a common to Abuela walked at 6


def commons(*fills):
    return dd.fills_from(dd.SellCurve("abuela", "common", 5, f) for f in fills)[("abuela", "common")]


def test_one_outlier_fill_no_longer_opens_the_ladder_far_above_what_she_pays():
    fill = commons(5, 5, 6, 6, 13)  # one team once got 13 for a common: 1.6 × 13 opened us at 21
    assert (fill.median, fill.top) == (6.0, 13)
    floor = desk.sell_floor(1.3, 1.3, Guardrails().dealer_sell_min_surplus, Guardrails())
    plan = desk.plan_for(fill, floor, Guardrails())
    assert plan.start == 12  # 2 × her median fill, not ceil(1.6 × 13) = 21
    asks = ask_schedule(plan)
    assert all(a > b for a, b in zip(asks, asks[1:], strict=False)) and asks[-1] == floor  # never the same ask
    # the cap never opens below our floor
    assert desk.plan_for(fill, 20, Guardrails()).start == 20
    # a hand-built Fill without a median falls back to the mean
    assert dd.Fill(5, 6.0, 13, 5).typical == 6.0


def test_the_dealer_sell_floor_uses_its_own_surplus_and_takes_her_final_6_for_a_common_worth_1_3():
    from bazaar_agent.agents.dealer_sell import AskPlan, SellNegotiation, decide_sell

    rules = Guardrails()
    assert rules.dealer_sell_min_surplus == 2
    floor = desk.sell_floor(1.3, 1.3, rules.dealer_sell_min_surplus, rules)
    assert floor == 4 and floor >= 1.3  # never below what we lose
    assert desk.sell_floor(1.3, 1.3, PARAMS.sell_min_surplus, rules) == 7  # the old floor: her final 6 walked
    for floor_, kind in ((floor, "accept"), (7, "walk")):
        neg = SellNegotiation(AskPlan(12, 1, floor_), asks=[12, 11, 10, 9, 8])
        decide_sell(neg, 5, 301, False)  # her opening bid
        decide_sell(neg, 6, 302, False)  # she came up
        move = decide_sell(neg, 6, 303, True)  # her FINAL
        assert move.kind == kind
    assert move.kind == "walk"
    neg = SellNegotiation(AskPlan(12, 1, floor), asks=[12, 11, 10, 9, 8])
    decide_sell(neg, 5, 301, False)
    assert decide_sell(neg, 6, 303, True) == decide_sell(neg, 6, 303, True)
    assert decide_sell(neg, 6, 303, True).price == 6


def test_the_candidate_floor_is_the_dealer_sell_surplus_and_the_venue_path_keeps_its_own():
    lax = desk.candidates(ME_DUP, CATALOG, MARKET, PARAMS, Guardrails(dealer_sell_min_surplus=0))
    strict = desk.candidates(ME_DUP, CATALOG, MARKET, PARAMS, Guardrails(dealer_sell_min_surplus=5))
    assert lax and strict and lax[0].floor == strict[0].floor - 5 and lax[0].floor >= lax[0].value


# ---------------------------------------------------------------- Sat 3 Oct: four sell threads for one copy


class WalkingDealer(FakeTeam):
    """Every sell thread we open ends without a deal the next tick (she walks or the thread closes)."""

    def thread(self, tid):
        self.reads.append(f"thread {tid}")
        return {"id": tid, "status": "closed", "closed_reason": "walked", "messages": [], "standing_offers": []}


def test_a_card_that_walked_is_not_reopened_with_that_dealer_and_the_dealer_gets_gaps(tmp_path):
    team = WalkingDealer(me=ME_DUP)
    m, _ = maker(tmp_path, team, live=True, dealer_sell_enabled=True)
    when: list[tuple[int, str, int]] = []
    for tick in range(100, 140):  # 40 ticks inside one game hour (the fake clock stays at hour 1.5)
        before = len(opened(team))
        m.on_tick(clock(tick=tick))
        for _, dealer, topic in opened(team)[before:]:
            when.append((tick, dealer, topic["sell"]["assets"][0]))
    refs = {8: "LAV-08", 9: "LAV-08", 7: "LAV-08"}
    pairs = [(d, refs.get(a, a)) for _, d, a in when]
    assert when and len(pairs) == len(set(pairs))  # never the same card twice with the same dealer this hour
    abuela = [t for t, d, _ in when if d == "abuela"]
    gap = Guardrails().dealer_sell_dealer_gap_ticks
    assert all(b - a > gap for a, b in zip(abuela, abuela[1:], strict=False))


def test_a_walked_card_comes_back_after_the_retry_window_or_when_our_floor_drops():
    sell_desk = desk.SellDesk(FakeTeam(), Guardrails(dealer_sell_enabled=True), None, True, lambda line: None, None)
    cand = desk.Candidate(8, "LAV-08", "uncommon", 2.0, 2.0, 7, "abuela", 13.0)
    sell_desk.walked[("abuela", "LAV-08")] = (1.5, 7)
    assert not sell_desk._retry_ok(cand, clock())  # same game hour (1.5), same floor
    assert sell_desk._retry_ok(desk.Candidate(**{**cand.__dict__, "floor": 4}), clock())  # our floor dropped
    late = clock().model_copy(update={"t_hours": 2.5})
    assert sell_desk._retry_ok(cand, late)
    assert sell_desk._retry_ok(desk.Candidate(**{**cand.__dict__, "dealer": "chato"}), clock())
