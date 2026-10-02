from collections import Counter, deque
from dataclasses import dataclass, field
from typing import Optional

KNOWN_TYPES = frozenset({
    "agent.hello", "clock", "agent.phase", "agent.thought", "agent.action", "agent.me",
    "thread.message", "thread.closed", "settlement", "duel.message", "duel.result",
})

LOG_SIZE = 300
TAPE_SIZE = 200
PRICE_HISTORY = 24


@dataclass
class LogLine:
    tick: int
    kind: str
    text: str


@dataclass
class Thread:
    id: int
    with_: str
    topic: str = "?"
    side: str = "buy"
    our_price: Optional[int] = None
    their_price: Optional[int] = None
    rounds: int = 0
    final: bool = False
    expires_tick: Optional[int] = None
    status: str = "open"
    last_text: Optional[str] = None
    history: list = field(default_factory=list)


@dataclass
class Duel:
    id: int
    role: str
    our_price: Optional[int] = None
    their_price: Optional[int] = None
    our_days: Optional[int] = None
    their_days: Optional[int] = None
    rounds: int = 0
    status: str = "open"
    deal_price: Optional[int] = None
    points: Optional[float] = None


@dataclass
class Trade:
    tick: int
    venue: str
    seller: str
    buyer: str
    ref: str
    name: str
    price: int
    ours: bool
    gain: Optional[float]


@dataclass
class State:
    team: str = ""
    name: str = ""
    tick: int = 0
    day: str = ""
    tick_seconds: float = 60.0
    phase: str = "observe"
    goal: str = ""
    cash: int = 0
    score: dict = field(default_factory=dict)
    pages: list = field(default_factory=list)
    owned: Counter = field(default_factory=Counter)
    values: dict = field(default_factory=dict)
    log: deque = field(default_factory=lambda: deque(maxlen=LOG_SIZE))
    threads: dict = field(default_factory=dict)
    duels: dict = field(default_factory=dict)
    tape: deque = field(default_factory=lambda: deque(maxlen=TAPE_SIZE))
    prices: dict = field(default_factory=dict)


def _topic(offer: dict) -> str:
    for side in ("give", "want"):
        for t in offer.get(side, {}).get("types") or []:
            return t.split(":", 1)[-1]
        for a in offer.get(side, {}).get("assets") or []:
            return a.get("ref", "?")
    return "?"


def _price(offer: dict) -> Optional[int]:
    return (offer.get("want", {}).get("cash") or offer.get("give", {}).get("cash")) or None


def _thread_message(s: State, p: dict) -> None:
    if p.get("team") != s.team:
        return
    offer = p.get("offer") or {}
    th = s.threads.get(p["thread"])
    if th is None:
        th = s.threads[p["thread"]] = Thread(id=p["thread"], with_=p.get("with", "?"))
    ours = p.get("sender") == s.team
    if offer:
        th.topic = _topic(offer)
        price = _price(offer)
        if ours:
            th.side = "buy" if offer.get("give", {}).get("cash") else "sell"
            th.our_price = price
        else:
            th.side = "buy" if offer.get("want", {}).get("cash") else "sell"
            th.their_price = price
            th.final = bool(offer.get("final"))
        th.expires_tick = offer.get("expires_tick")
        th.history.append(("us" if ours else "them", price))
    if not ours and p.get("text"):
        th.last_text = p["text"]
    th.rounds += 1


def _settlement(s: State, tick: int, p: dict) -> None:
    parties = p.get("parties") or []
    venue = p.get("venue") or p.get("persona") or "direct"
    price = p.get("price") or 0
    for item in p.get("items") or []:
        ref = item.get("ref", "?")
        seller, buyer = item.get("frm", "?"), item.get("to", "?")
        ours = s.team in parties
        value = p.get("your_value", s.values.get(ref))
        gain = None
        if ours and value is not None:
            gain = value - price if buyer == s.team else price - value
        s.tape.appendleft(Trade(tick, venue, seller, buyer, ref, item.get("name", ref), price, ours, gain))
        s.prices.setdefault(ref, deque(maxlen=PRICE_HISTORY)).append(price)


def _duel_message(s: State, p: dict) -> None:
    d = s.duels.get(p["duel"])
    if d is None:
        d = s.duels[p["duel"]] = Duel(id=p["duel"], role=p.get("role", "?"))
    if p.get("sender") == s.team:
        d.our_price, d.our_days = p.get("price"), p.get("days")
    else:
        d.their_price, d.their_days = p.get("price"), p.get("days")
    d.rounds += 1


def _duel_result(s: State, p: dict) -> None:
    d = s.duels.get(p.get("duel"))
    if d is None:
        return
    d.status = "deal" if p.get("deal") else "no deal"
    d.deal_price, d.points = p.get("price"), p.get("points")


def _me(s: State, p: dict) -> None:
    s.cash = p.get("cash", s.cash)
    s.score = p.get("score") or s.score
    s.pages = (p.get("album") or {}).get("pages") or s.pages
    cards = [a for a in p.get("assets") or [] if a.get("kind") == "card"]
    if cards:
        s.owned = Counter(a["ref"] for a in cards)
        s.values = {a["ref"]: a.get("your_value", 0.0) for a in cards}


def apply(s: State, e: dict) -> State:
    kind, p, tick = e.get("type"), e.get("payload") or {}, e.get("tick", s.tick)
    if kind == "agent.hello":
        s.team, s.name = p.get("team", s.team), p.get("name", s.name)
    elif kind == "clock":
        s.tick, s.day = tick, p.get("day", s.day)
        s.tick_seconds = p.get("tick_seconds", s.tick_seconds)
    elif kind == "agent.phase":
        s.phase, s.goal = p.get("phase", s.phase), p.get("goal", s.goal)
    elif kind == "agent.thought":
        s.log.append(LogLine(tick, "thought", p.get("text", "")))
    elif kind == "agent.action":
        s.log.append(LogLine(tick, p.get("kind", "act"), p.get("summary", "")))
    elif kind == "agent.me":
        _me(s, p)
    elif kind == "thread.message":
        _thread_message(s, p)
    elif kind == "thread.closed" and p.get("thread") in s.threads:
        s.threads[p["thread"]].status = "closed"
    elif kind == "settlement":
        _settlement(s, tick, p)
    elif kind == "duel.message":
        _duel_message(s, p)
    elif kind == "duel.result":
        _duel_result(s, p)
    return s
