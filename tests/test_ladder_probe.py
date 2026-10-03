"""The ladder probe (SG1): one small negotiated dealer buy per unlocked dealer per game hour, on Jev's yes only."""

from copy import deepcopy

from bazaar_agent.agents.ladder_probe import opening_asks, plan_one, plan_probes, probe_state, range_share
from bazaar_agent.agents.runtime import JevAdvice
from bazaar_agent.agents.strategy_gate import LADDER_PROBE, StrategyGate
from bazaar_agent.agents.taker import Taker, TakerConfig
from bazaar_agent.guardrails import Guardrails
from bazaar_agent.strategy import build_market
from bazaar_agent.ticks import Clock
from tests.agent_fakes import TICK, FakePublic, FakeTeam, clock, parts, rows
from tests.test_intel import msg, opened
from tests.test_strategy import ABUELA, CATALOG, EVENTS, ME, PARAMS

# Abuela opens commons at 14 here (her menu); her lowest common fill in EVENTS is 8 (LAV-02), so a top of 12
# keeps (14 − 12) / (14 − 8) = 0.33 of her range.
ABUELA14 = deepcopy(ABUELA)
ABUELA14["menu"]["sells"][1]["opening_ask"] = 14
DEALERS = [ABUELA14, {"id": "chato", "status": "announced", "level": None}]
RULES = Guardrails()


def market(events=EVENTS, dealers=DEALERS, me=ME):
    return build_market(me, CATALOG, events, dealers)


def opens(m, events=EVENTS, dealers=DEALERS):
    return opening_asks(m, events, dealers)


# ---------------------------------------------------------------- planning (pure)


def test_the_cheapest_missing_card_of_each_dealer_is_probed_below_its_opening_ask():
    m = market()
    (p,) = plan_probes(m, opens(m), RULES, 130, value_of=lambda ref: 12.0)
    # LAV-02 (common, missing) beats LAV-08 (uncommon); LAT-03 and LAV-01 are held, LAV-09/10 no dealer sells.
    assert (p.dealer, p.ref, p.rarity, p.opening, p.lowest_fill) == ("abuela", "LAV-02", "common", 14, 8)
    assert (p.start, p.top, p.share) == (4, 12, 0.333)  # start: 0.6 × lowest fill; top: the official value
    mv = p.move()
    assert mv.ladder == (4, 12, 1) and mv.limit == 12 and mv.source == "abuela" and mv.strategy == "ladder_probe"
    assert mv.command.endswith("--start 4 --max 12 --dealer abuela")


def test_a_top_that_captures_too_little_of_her_range_is_not_probed():
    m, rules = market(), RULES.model_copy(update={"max_price_common": 20})
    assert plan_one(m, "abuela", opens(m), rules, 130, lambda ref: 13.0) is None  # (14 − 13) / 6 = 0.17 < 0.3
    loose = rules.model_copy(update={"ladder_probe_min_share": 0.1})
    assert plan_one(m, "abuela", opens(m), loose, 130, lambda ref: 13.0) is not None


def test_the_top_never_passes_the_official_value_the_rarity_cap_or_the_cash_room():
    m = market()
    by_value = plan_one(m, "abuela", opens(m), RULES, 130, lambda ref: 9.9)
    assert by_value is not None and by_value.top == 9  # floor of the official value
    margin = RULES.model_copy(update={"official_value_margin": 2.0})
    assert plan_one(m, "abuela", opens(m), margin, 130, lambda ref: 12.0).top == 10
    capped = RULES.model_copy(update={"max_price_common": 11})
    assert plan_one(m, "abuela", opens(m), capped, 130, lambda ref: 40.0).top == 11
    assert plan_one(m, "abuela", opens(m), RULES, 9, lambda ref: 40.0).top == 9  # cash above the floor
    # A top below her lowest fill (8) would only walk after holding her thread: no probe.
    assert plan_one(m, "abuela", opens(m), RULES, 7, lambda ref: 40.0) is None
    assert plan_one(m, "abuela", opens(m), RULES, 130, lambda ref: 7.5) is None
    assert plan_one(m, "abuela", opens(m), RULES, 0, lambda ref: 40.0) is None


def test_an_unread_official_value_skips_the_card_and_reads_only_the_chosen_card():
    m = market()
    asked: list[str] = []

    def unread(ref):
        asked.append(ref)
        return None

    assert plan_probes(m, opens(m), RULES, 130, value_of=unread) == []
    assert asked == ["LAV-02"]  # one read, for the single chosen card


def test_unknown_range_or_no_fills_skips_and_skip_drops_a_dealer():
    m = market()
    assert range_share(10, 9, 10) is None and range_share(9, 9, 10) is None
    no_fills = market(events=[e for e in EVENTS if e["type"] != "settlement"])
    assert plan_probes(no_fills, opens(no_fills), RULES, 130, value_of=lambda ref: 12.0) == []
    assert plan_probes(m, opens(m), RULES, 130, skip={"abuela"}, value_of=lambda ref: 12.0) == []


def test_the_observed_opening_ask_wins_over_the_menu():
    events = [
        *EVENTS,
        msg(50, 11, "t03", "abuela", want_cash=16, tick=4),
        opened(51, 13, "t05", {"buy": {"card": "LAV-01"}}),
    ]
    events += [msg(52, 13, "t05", "abuela", want_cash=18, tick=5)]
    m = market(events=events)
    assert opens(m, events)[("abuela", "common")] == 17  # median of 16 and 18


def test_the_jev_state_is_compact_and_json_safe():
    import json

    m = market()
    probes = plan_probes(m, opens(m), RULES, 130, value_of=lambda ref: 12.0)
    state = probe_state(probes, 400, 270, 130, 0, {"abuela": 2})
    assert json.loads(json.dumps(state))["probe"][0]["card"] == "LAV-02"
    assert state["ladder"] == {"our_dealer_deals": {"abuela": 2}} and state["cash_room"] == 130


# ---------------------------------------------------------------- the taker


class ValueTeam(FakeTeam):
    """A team client that answers GET /api/me/value."""

    def __init__(self, value=12.0, **kw):
        super().__init__(**kw)
        self.value_of = value
        self.value_reads: list[str] = []

    def value(self, card):
        self.value_reads.append(card)
        return {"card": card, "your_value": self.value_of}


class Rec:
    def __init__(self):
        self.rows: list[tuple] = []

    def decide(self, tick, kind, line, **kw):
        self.rows.append((tick, kind, kw.get("chosen")))
        return len(self.rows)


def gate(verdict="yes", refresh=120):
    asks: list[dict] = []

    def ask(name, state):
        asks.append({"name": name, **state})
        return JevAdvice(verdict, 0.9 if verdict != "undecided" else 0.5)

    return StrategyGate(ask, Rec(), refresh), asks


def taker(tmp_path, team, strategy_gate, live=False):
    kw = parts(tmp_path)
    kw["params"] = lambda tick: PARAMS.model_copy(update={"min_buy_surplus": 1000})  # the strategy itself buys nothing
    lines: list[str] = []
    t = Taker(
        team,
        FakePublic(dealers=DEALERS),
        live=live,
        log=lines.append,
        now=lambda: 1000.0,
        sleep=lambda s: None,
        config=TakerConfig(max_dealer_threads=1),
        strategy_gate=strategy_gate,
        **kw,
    )
    return t, lines


def probe_rows(tmp_path):
    return [r for r in rows(tmp_path) if r.get("kind") == "ladder_probe"]


def test_no_gate_or_a_gate_that_says_no_or_undecided_opens_no_probe(tmp_path):
    for i, g in enumerate([None, gate("no")[0], gate("undecided")[0]]):
        team = ValueTeam()
        t, _ = taker(tmp_path / str(i), team, g, live=True)
        t.on_tick(clock())
        assert [s for s in team.sent if s[0] == "open_thread"] == []
        assert probe_rows(tmp_path / str(i)) == []
        assert len(team.value_reads) == (0 if g is None else 1)  # only Jev's state reads it, for the one card


def test_a_yes_opens_a_dealer_thread_whose_first_bid_is_below_her_opening_ask(tmp_path):
    team = ValueTeam()
    g, asks = gate("yes")
    t, _ = taker(tmp_path, team, g, live=True)
    t.on_tick(clock())
    assert team.sent[0] == ("open_thread", "abuela", {"buy": {"card": "LAV-02"}})
    bids = [s[2] for s in team.sent if s[0] == "say"]
    assert bids == [4] and bids[0] < 14  # the ladder start, below her opening ask for commons
    (opened,) = [r for r in rows(tmp_path) if r.get("kind") == "dealer_open"]
    assert opened["reason"].startswith("ladder probe") and opened["inputs"]["plan"] == "4→12 step 1"
    assert asks[0]["name"] == LADDER_PROBE and asks[0]["probe"][0]["card"] == "LAV-02"
    (row,) = probe_rows(tmp_path)
    assert row["inputs"]["top"] == 12 and row["inputs"]["dealer"] == "abuela"
    assert team.value_reads.count("LAV-02") >= 1


def test_one_probe_per_dealer_per_game_hour(tmp_path):
    team = ValueTeam()
    t, _ = taker(tmp_path, team, gate("yes")[0])  # dry run: no thread holds the dealer, only the hour does
    t.on_tick(clock())
    t.on_tick(clock(tick=TICK + 1))  # same game hour (t_hours 1.5)
    assert len(probe_rows(tmp_path)) == 1
    later = Clock(tick=TICK + 2, tick_seconds=60.0, next_tick_in=40.0, t_hours=2.1, limits={})
    t.on_tick(later)
    assert len(probe_rows(tmp_path)) == 2


def test_the_gate_is_asked_at_most_once_per_refresh_window(tmp_path):
    team = ValueTeam()
    g, asks = gate("no", refresh=5)
    t, _ = taker(tmp_path, team, g)
    for k in range(5):
        t.on_tick(clock(tick=TICK + k))
    assert len(asks) == 1
    t.on_tick(clock(tick=TICK + 5))
    assert len(asks) == 2
