"""Los Pícaros, Sat 3 Oct tick 863: the taker bid 54→55→56 for LAV-10 and took their FINAL 63, their list price, as if
it were their limit; a deal at the list price scores ~0 on the dealer ladder (RULES.md "Dealers"). A forgiving dealer
(published kind `trickster`, or strictness at most `trickster_max_strictness`) is never taken at or above its list
price, only low in its observed fill range (`trickster_accept_fill_share`), and its FINAL is a plain ask: never taken
nor walked from for being final. Fakes only, no network."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from bazaar_agent import intel
from bazaar_agent.agents.accept_gate import dealer_gate
from bazaar_agent.agents.dealer import BidPlan, Move, Negotiation, apply_advice, decide, meet_ask
from bazaar_agent.agents.desk import Conversation, DeskMove, meet_the_ask, plan_conversation
from bazaar_agent.agents.inspector import CardIndex
from bazaar_agent.agents.runtime import JevAdvice, MarketFeed
from bazaar_agent.agents.taker import Taker, TakerConfig, desk_proposal
from bazaar_agent.agents.trickster import accept_cap, class_fills, forgiving_plan, is_forgiving
from bazaar_agent.decisions import PROCESS_STARTED, Decision, DecisionLog
from bazaar_agent.guardrails import Guardrails, Ledger, load_guardrails
from bazaar_agent.persona_model import Persona, parse_persona, parse_personas
from tests.agent_fakes import TICK, FakePublic, FakeTeam, clock, parts, rows
from tests.bites.kit import thread_bid
from tests.test_intel import settle
from tests.test_strategy import CATALOG, ME

RULES = Guardrails()
SOFT = Guardrails(trickster_max_strictness=0.2)  # the strictness bar on (it ships at 0: Abuela's FINAL is real)
# As GET /api/dealers published them on Sat 3 Oct (~18:45): kind trickster, strictness 0.1, rares at list 63.
PICAROS = {
    "id": "picaros",
    "name": "Los Pícaros",
    "kind": "trickster",
    "level": 4,
    "status": "active",
    "traits": {"chattiness": 0.8, "strictness": 0.1, "memory": 0.3, "shrewdness": 0.7},
    "menu": {"sells": [{"rarity": "rare", "sets": "released", "list_price": 63}]},
}


def fill(eid: int, ref: str, price: int, buyer: str = "t01", dealer: str = "picaros") -> dict[str, Any]:
    """A dealer's sale in the feed: a settlement where the dealer gives the card and a team pays."""
    return settle(eid, eid, dealer, buyer, ref, price, tick=700 + eid, kind="card", persona=dealer)


# Our two real Pícaros rare deals before tick 863 (coordinator log): LAV-09 at 58 (tick 775), MAL-10 at 59 (tick 790).
REAL = [fill(1, "LAV-09", 58), fill(2, "MAL-10", 59)]
WIDE = [fill(1, "LAV-09", 52, "t07"), fill(2, "MAL-10", 57, "t03"), fill(3, "SAL-09", 62, "t12")]  # low third ≤ 55


def persona(**over: Any) -> Persona:
    p = parse_persona({**deepcopy(PICAROS), **over})
    assert p is not None
    return p


def plan_for(events: list[dict[str, Any]], p: Persona | None = None, item: str = "LAV-10") -> BidPlan:
    """The taker's ladder at tick 863 (54→67, step 1), as the forgiving builder shapes it for this dealer."""
    return forgiving_plan(BidPlan(54, 1, 67), p or persona(), item, "rare", intel.tape(events), RULES)


def facing(plan: BidPlan, bids: tuple[int, ...] = (54, 55, 56)) -> Negotiation:
    """Our bids so far, after their opening ask 70 came down to 66."""
    n = Negotiation(plan, list(bids))
    n.see_ask(70)
    n.see_ask(66)
    return n


def bids_against(n: Negotiation, ask: int, final: bool) -> tuple[list[int], Move]:
    """Every bid we send while they hold `ask`, and the move that ends the ladder."""
    sent: list[int] = []
    for _ in range(30):
        move = decide(n, ask, 9, final)
        if move.kind != "bid" or move.price is None:
            return sent, move
        sent.append(move.price)
        n.bids.append(move.price)
    raise AssertionError(f"the ladder never ended: {sent}")


# ---------------------------------------------------------------- decide(): the live case and the three rules


def test_the_live_case_their_final_at_list_is_never_taken_we_keep_stepping_by_one():
    today = facing(BidPlan(54, 1, 67))
    assert decide(today, 63, 9, True) == Move("accept", 63, 9, "final within limit")  # tick 863: the bug
    plan = plan_for(REAL)
    assert (plan.forgiving, plan.list_price, plan.accept_max, plan.final_cap) == (True, 63, 58, 67)
    sent, last = bids_against(facing(plan), 63, final=True)
    assert sent == [57, 58, 59, 60, 61, 62]  # never 63: their list price (a bid they take there is that same deal)
    assert last.kind == "walk" and not last.reopen  # nothing left below it that we may pay: no deal at their list


def test_we_never_bid_their_list_price_even_while_they_ask_above_it():
    today, _ = bids_against(facing(BidPlan(54, 1, 67)), 66, final=False)
    assert 63 in today  # today our ladder climbs through their list price toward their ask
    sent, last = bids_against(facing(plan_for(REAL)), 66, final=False)
    assert sent == [57, 58, 59, 60, 61, 62] and last.kind == "walk"  # a bid they take at 63 scores ~0 as well


def test_the_desk_never_passes_their_final_on_as_binding():
    conv = Conversation("picaros", "LAV-10", "rare", 218.0, "r", facing(plan_for(REAL)), 1228, TICK)
    final = {"id": 9, "maker": "picaros", "status": "open", "final": True, "want": {"cash": 63}}
    final["give"] = {"types": ["card:LAV-10"]}
    dm = plan_conversation(conv, {"status": "open", "standing_offers": [final]}, 14, TICK)
    assert (dm.ask, dm.final, dm.move.kind, dm.move.price) == (63, False, "bid", 57)
    assert "its FINAL 63 is not its limit" in dm.move.reason
    accept = desk_proposal(DeskMove(conv, Move("accept", 63, 9), 63, dm.final, offer_id=9))
    assert not accept.final  # never ranked first, nor checked against a lifted final cap


def test_a_final_below_list_but_above_the_low_third_of_its_fills_keeps_us_bidding():
    plan = plan_for(WIDE)
    assert plan.accept_max == 55  # 52 + (62 - 52) / 3, floored
    move = decide(facing(plan), 60, 9, True)
    assert (move.kind, move.price) == ("bid", 57) and "FINAL 60 is not its limit" in move.reason
    met = facing(plan, bids=(54, 55, 56, 57, 58, 59))  # their final meets our next bid: still neither taken nor met
    assert decide(met, 60, 9, True).kind == "walk"
    assert meet_ask(met, 60, final=True).kind == "wait" and meet_ask(met, 60).kind == "wait"


def test_an_ask_in_the_low_third_of_its_fills_is_taken_final_or_not():
    plan = plan_for(WIDE)
    for final in (False, True):
        move = decide(facing(plan, bids=(52, 53, 54)), 55, 9, final)
        assert (move.kind, move.price, move.offer_id) == ("accept", 55, 9)
    assert decide(facing(plan, bids=(53, 54, 55)), 56, 9, False).kind != "accept"  # one above the low third


def test_fills_at_or_above_its_list_price_never_make_its_list_price_acceptable():
    plan = plan_for([fill(1, "LAV-09", 63, "t07"), fill(2, "MAL-10", 66, "t03")])  # teams paid its list, and more
    assert (plan.accept_max, plan.list_price) == (64, 63)
    assert decide(facing(plan, bids=(60, 61, 62)), 63, 9, False).kind == "walk"  # 63 ≤ 64, but it is its list price
    assert not plan.accepts(63) and not plan.accepts(64) and not plan.takes_final(63, 9)


def test_with_no_fills_seen_its_asks_are_never_taken_we_only_bid():
    plan = plan_for([])
    assert plan.forgiving and plan.accept_max is None
    for ask in range(40, 70):
        for final in (False, True):
            move = decide(facing(plan, bids=(50, 51)), ask, 9, final)
            assert move.kind != "accept", (ask, final, move)
    move = decide(facing(plan, bids=(50, 51)), 55, 9, True)
    assert (move.kind, move.price) == ("bid", 52)


def test_only_its_own_sales_in_the_same_price_class_are_fills():
    events = [
        fill(1, "LAV-08", 20),  # an uncommon: another price class
        fill(2, "MAL-09", 50, dealer="chato"),  # another dealer
        settle(3, 3, "t07", "picaros", "SAL-09", 30, tick=703, kind="card", persona="picaros"),  # it bought
        fill(4, "SAL-10", 60, buyer="pilar"),  # not a team's price
        fill(5, "LAT-10", 61),
    ]
    assert class_fills(intel.tape(events), "picaros", "LAV-10") == [61]
    assert accept_cap([], 1 / 3) is None and accept_cap([58, 59], 1 / 3) == 58 and accept_cap([52, 61], 1 / 3) == 55


# ---------------------------------------------------------------- who forgives


@pytest.mark.parametrize(
    ("kind", "traits", "rules", "expected"),
    [
        ("trickster", PICAROS["traits"], RULES, True),
        ("trickster", None, RULES, True),  # its published kind alone
        ("dealer", {"strictness": 0.1}, RULES, False),  # shipped bar 0: the published kind alone decides
        ("dealer", {"strictness": 0.1}, SOFT, True),  # with a bar: at or under it, whatever its kind
        ("dealer", {"strictness": 0.2}, SOFT, True),
        ("dealer", {"strictness": 0.21}, SOFT, False),
        ("dealer", {"strictness": 0.6}, SOFT, False),
        ("dealer", None, Guardrails(trickster_max_strictness=0.9), False),  # no traits: never by neutral defaults
    ],
)
def test_who_forgives(kind: str, traits: dict[str, float] | None, rules: Guardrails, expected: bool):
    assert is_forgiving(persona(kind=kind, traits=traits), rules) is expected
    assert is_forgiving(None, rules) is False  # a dealer /api/dealers does not list: today's behaviour


def test_the_real_abuela_keeps_her_real_final_with_the_shipped_rules():
    # tests/fixtures/api/get_api_dealers.anon.json: Abuela publishes strictness 0.1; her FINAL is real (she walks)
    fixture = Path(__file__).parent / "fixtures" / "api" / "get_api_dealers.anon.json"
    abuela = parse_personas(json.loads(fixture.read_text(encoding="utf-8"))["body"]["personas"])["abuela"]
    assert abuela.kind == "dealer" and abuela.traits.strictness == 0.1
    assert not is_forgiving(abuela, RULES) and not is_forgiving(abuela, load_guardrails().rules)
    assert is_forgiving(abuela, SOFT)  # only a strictness bar set by hand would catch her


def test_a_soft_non_trickster_is_forgiving_and_a_strict_dealer_keeps_todays_final():
    plan = BidPlan(54, 1, 67)
    soft = forgiving_plan(plan, persona(kind="dealer", traits={"strictness": 0.1}), "LAV-10", "rare", (), SOFT)
    assert soft.forgiving and decide(facing(soft), 63, 9, True).kind == "bid"
    strict = persona(kind="dealer", traits={"strictness": 0.6})
    assert forgiving_plan(plan, strict, "LAV-10", "rare", intel.tape(REAL), SOFT) == plan  # unchanged
    assert forgiving_plan(plan, None, "LAV-10", "rare", intel.tape(REAL), RULES) == plan
    assert decide(facing(plan), 63, 9, True) == Move("accept", 63, 9, "final within limit")  # today's final
    lifted = BidPlan(54, 1, 60, final_max=66, lift_after=4)  # a lifted final (N14a) never survives a trickster
    shaped = forgiving_plan(lifted, persona(), "LAV-10", "rare", intel.tape(REAL), RULES)
    assert (shaped.final_max, shaped.final_cap, shaped.takes_final(63, 9)) == (None, 60, False)
    assert not shaped.takes_final(59, 9) and shaped.takes_final(58, 9)  # inside our top, only low in its fills
    assert BidPlan(54, 1, 60, final_max=66, forgiving=True).final_cap == 60  # a lifted cap set by hand is ignored


# ---------------------------------------------------------------- Jev, meeting an ask, the structure


def test_jev_never_takes_a_forgiving_dealer_at_its_list_price_but_may_low_in_its_fills():
    today = facing(BidPlan(54, 1, 67), bids=(54, 55))
    assert apply_advice(Move("bid", 56), "accept", today, 63, 9).kind == "accept"  # today: Jev could take 63
    n = facing(plan_for(WIDE), bids=(50, 51))
    assert apply_advice(Move("bid", 52), "accept", n, 63, 9) == Move("bid", 52)
    assert apply_advice(Move("bid", 52), "accept", n, 56, 9) == Move("bid", 52)  # above the low third
    assert apply_advice(Move("bid", 52), "accept", n, 55, 9).kind == "accept"  # inside it: an early accept is fine
    assert meet_ask(n, 63).kind == "wait"  # our accept slot went elsewhere: never a bid at their list price


def test_a_lav08_offer_in_a_lav09_thread_is_still_refused_for_a_trickster():
    plan = plan_for(WIDE, item="LAV-09")
    conv = Conversation("picaros", "LAV-09", "rare", 120.0, "r", facing(plan, bids=(52, 53)), 1069, TICK)
    bait = {
        "id": 9,
        "maker": "picaros",
        "status": "open",
        "final": True,
        "give": {"types": ["card:LAV-08"]},
        "want": {"cash": 50},  # low in its fills: only the structure refuses it
    }
    thread = {
        "status": "open",
        "standing_offers": [bait],
        "messages": [{"id": 77, "sender": "picaros", "text": "Tu LAV-09, solo hoy.", "offer": bait}],
    }
    dm = plan_conversation(conv, thread, 14, TICK)
    assert dm.ignored and (dm.ask, dm.offer_id, dm.final) == (None, None, False) and dm.move.kind == "bid"
    assert meet_the_ask(dm).move.kind == "wait"
    assert not dealer_gate(thread, "picaros", 9, 50, conv.topic, CardIndex.from_catalog(CATALOG)).allowed


# ---------------------------------------------------------------- the taker, end to end

THREAD = 5000  # the fake server's first thread id
HISTORY = [fill(1, "LAV-09", 58), fill(2, "LAT-09", 63, "t07")]  # its rares: ours at 58, t07's at its list 63
STRICT = {**deepcopy(PICAROS), "kind": "dealer", "traits": {"strictness": 0.9, "shrewdness": 0.7}}  # the control


def offer(oid: int, cash: int, final: bool = False) -> dict[str, Any]:
    return {
        "id": oid,
        "maker": "picaros",
        "status": "open",
        "final": final,
        "give": {"types": ["card:LAV-09"]},
        "want": {"cash": cash},
    }


def their(team: FakeTeam, *offers: dict[str, Any], tid: int = THREAD) -> None:
    """Our thread as the fake server shows it: their offers in its messages, the newest one standing."""
    team.thread_payloads[tid] = {
        "id": tid,
        "status": "open",
        "messages": [{"id": 100 + i, "sender": "picaros", "offer": o} for i, o in enumerate(offers)],
        "standing_offers": [offers[-1]],
    }


def taker(tmp_path: Path, team: FakeTeam, dealer: dict[str, Any], jev: Any = None, lines: Any = None) -> Taker:
    kw = {**parts(tmp_path), "feed": MarketFeed(lambda n: deepcopy(HISTORY))}
    return Taker(
        team,
        FakePublic(dealers=[dealer], events=HISTORY),
        live=True,
        log=(lines if lines is not None else []).append,
        now=lambda: 1000.0,
        sleep=lambda s: None,
        config=TakerConfig(max_dealer_threads=3),
        **({"jev": jev} if jev is not None else {}),
        **kw,
    )


def play(tmp_path: Path, dealer: dict[str, Any], last: dict[str, Any], jev: Any = None, lines: Any = None) -> FakeTeam:
    """Open a thread for LAV-09, see their opening ask 70, then `last` for two ticks."""
    team = FakeTeam(me={**deepcopy(ME), "unlocked": ["picaros"]})
    t = taker(tmp_path, team, dealer, jev, lines)
    t.on_tick(clock())
    assert ("open_thread", "picaros", {"buy": {"card": "LAV-09"}}) in team.sent
    their(team, offer(800, 70))
    team.now = clock(tick=TICK + 1)
    t.on_tick(team.now)
    their(team, offer(800, 70), last)
    for tick in (TICK + 2, TICK + 3):
        team.now = clock(tick=tick)
        t.on_tick(team.now)
    return team


def test_the_taker_never_takes_their_final_at_list_and_keeps_bidding(tmp_path):
    control = play(tmp_path / "strict", STRICT, offer(801, 63, final=True))
    assert ("accept", 801) in control.sent  # a strict dealer's final is its limit: taken, as today
    team = play(tmp_path, PICAROS, offer(801, 63, final=True))
    assert not [s for s in team.sent if s[0] == "accept"]
    assert [s[2] for s in team.sent if s[0] == "say"] == [56, 57, 58, 59]  # by 1, on through their FINAL 63
    (row,) = [r for r in rows(tmp_path) if r.get("kind") == "dealer_open"]
    # The fills in this feed are our own buys (t01): they never set what we accept (#228 security P2), so none is known
    assert (row["inputs"]["forgiving"], row["inputs"]["list_price"], row["inputs"]["accept_max"]) == (True, 63, None)


def test_a_jev_yes_never_takes_their_ask_at_list_through_the_taker(tmp_path):
    asked: list[dict[str, Any]] = []

    def yes(state: dict[str, Any]) -> JevAdvice:
        asked.append(state)
        return JevAdvice("yes", 0.95)

    control = play(tmp_path / "strict", STRICT, offer(801, 63), jev=yes)
    assert ("accept", 801) in control.sent and asked  # today: Jev's yes takes an ask at their list price early
    asked.clear()
    team = play(tmp_path, PICAROS, offer(801, 63), jev=yes)
    assert not [s for s in team.sent if s[0] == "accept"]
    assert asked == []  # Jev is not even asked about an ask the plan would not take


def test_the_takers_accept_refuses_what_the_plan_does_not_accept(tmp_path, monkeypatch):
    # Whatever path proposes the accept (a future rule, an LLM), `_accept_one` checks `BidPlan.accepts` last.
    from bazaar_agent.agents import desk

    monkeypatch.setattr(desk, "decide", lambda neg, ask, oid, final: Move("accept", ask, oid, "forced"))
    lines: list[str] = []
    team = play(tmp_path, PICAROS, offer(801, 63, final=True), lines=lines)
    assert not [s for s in team.sent if s[0] == "accept"]
    skips = [r for r in rows(tmp_path) if r.get("kind") == "dealer_accept" and r.get("status") == "rejected"]
    assert skips and any("skip LAV-09 at 63 from picaros: picaros forgives" in line for line in lines)


def old_taker(root: Path, price: int) -> None:
    """The decisions log of the taker before a restart: it started, opened thread 40 with them and bid `price`."""
    log = DecisionLog(root)
    base: dict[str, Any] = {"agent": "taker", "reason": "r", "guardrail": "allowed", "dry_run": False}
    picaros = {"dealer": "picaros", "thread": 40, "item": "LAV-09"}
    started = {"owner": log.writer()}
    log.decide(Decision(**base, tick=TICK - 3, kind=PROCESS_STARTED, inputs=started, chosen=False, status="done"))
    log.decide(
        Decision(**base, tick=TICK, kind="dealer_opened", inputs=picaros, chosen=False, status="done", thread_id=40)
    )
    bid = {"kind": "bid", "price": price}
    log.decide(
        Decision(
            **base, tick=TICK, kind="dealer_bid", inputs=picaros, chosen=True, status="approved", thread_id=40, move=bid
        )
    )


def test_a_thread_adopted_after_a_restart_is_forgiving_too(tmp_path):
    # Our bid 60 stands in thread 40 when the taker restarts; they answer FINAL 60 (above the low of their fills, 59).
    for dealer, taken in ((STRICT, True), (PICAROS, False)):
        root = tmp_path / str(dealer["kind"])
        old_taker(root, 60)
        team = FakeTeam(me={**deepcopy(ME), "unlocked": ["picaros"]}, threads=[{"id": 40, "with": "picaros"}])
        team.offers = [thread_bid(77, 40, "LAV-09", 60) | {"to": "picaros", "created_tick": TICK}]
        their(team, offer(800, 70), offer(801, 60, final=True), tid=40)
        team.now = clock(tick=TICK + 1)
        taker(root, team, dealer).on_tick(team.now)
        assert (("accept", 801) in team.sent) is taken, (dealer["kind"], team.sent)


# ---------------------------------------------------------------- bazaar dealer buy (the CLI)


class PicarosClient:
    """Los Pícaros on a fake clock: opening ask 70, then 66, then FINAL 63 (their list price) for good."""

    def __init__(self, reads_per_tick: int = 5) -> None:
        self.reads, self.reads_per_tick = 0, reads_per_tick
        self.sent: list[int] = []
        self.accepted: list[int] = []
        self.closed = False

    def clock(self) -> dict[str, Any]:
        self.reads += 1
        return {"tick": 860 + self.reads // self.reads_per_tick, "next_tick_in": 30, "tick_seconds": 30}

    def me(self) -> dict[str, Any]:
        return {"cash": 400, "assets": []}

    def my_offers(self) -> dict[str, Any]:
        return {"offers": []}

    def open_thread(self, dealer: str, topic: dict[str, Any]) -> dict[str, Any]:
        return {"id": 1228}

    def thread(self, tid: int) -> dict[str, Any]:
        asks = [70, 66, 63]
        n = len(self.sent)
        if not n:
            return {"status": "open", "standing_offers": []}
        ask = asks[min(n, len(asks)) - 1]
        o = {"id": 900 + n, "maker": "picaros", "status": "open", "final": ask == 63, "want": {"cash": ask}}
        o["give"] = {"types": ["card:LAV-10"]}
        return {"status": "closed" if self.closed else "open", "standing_offers": [o]}

    def say(self, tid: int, text: str, price: int) -> dict[str, Any]:
        self.sent.append(price)
        return {"id": 5000 + len(self.sent)}

    def accept(self, offer_id: int) -> dict[str, Any]:
        self.accepted.append(offer_id)
        return {"ok": True}

    def close_thread(self, tid: int) -> dict[str, Any]:
        self.closed = True
        return {"id": tid, "status": "closed"}


def test_dealer_buy_never_takes_their_final_at_list(monkeypatch, tmp_path):
    from typer.testing import CliRunner

    from bazaar_agent import cli
    from bazaar_agent.config import Settings

    client = PicarosClient()
    monkeypatch.setattr(cli, "load_settings", lambda: Settings(data_dir=tmp_path))
    monkeypatch.setattr(cli, "team_client", lambda settings: client)
    monkeypatch.setattr(cli, "public_client", lambda settings: FakePublic(dealers=[PICAROS], events=REAL))
    monkeypatch.setattr(cli, "_history", lambda events_file, live: deepcopy(REAL))  # the agents' feed history
    monkeypatch.setattr(cli, "_feed_reader", lambda settings: lambda limit: deepcopy(REAL))
    monkeypatch.setattr(cli, "_rarity_of", lambda item: "rare")
    monkeypatch.setattr(cli, "_ledger", lambda source, live=False: Ledger(tmp_path / "ledger.jsonl"))
    monkeypatch.setattr("time.sleep", lambda seconds: None)
    args = ["dealer", "buy", "LAV-10", "--start", "54", "--max", "67", "--dealer", "picaros", "--live"]
    result = CliRunner().invoke(cli.app, args)
    assert result.exit_code == 0, result.output
    assert client.accepted == [] and client.sent == [54, 55, 56, 57, 58, 59, 60, 61, 62]
    assert client.closed and "picaros forgives" in " ".join(result.output.split())


def test_our_own_buys_and_a_single_fill_never_set_what_we_accept():
    ours = [fill(1, "LAV-10", 63)]  # tick 864: our own buy at its list price
    assert class_fills(intel.tape(ours), "picaros", "LAV-09", us="t01") == []
    assert class_fills(intel.tape(ours), "picaros", "LAV-09") == [63]
    assert accept_cap([63], 1 / 3) is None  # one fill: no range, we only bid
    plan = forgiving_plan(BidPlan(54, 1, 67), persona(), "LAV-09", "rare", intel.tape(ours), RULES, "t01")
    assert plan.accept_max is None and not plan.accepts(62)


def test_a_walk_from_a_trickster_rests_the_item():
    sent, last = bids_against(facing(plan_for(REAL)), 63, final=True)
    assert last.kind == "walk" and last.rest and not last.reopen
