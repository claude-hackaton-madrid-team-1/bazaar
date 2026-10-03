"""How a dealer haggles: pure functions over a `Negotiation`, calibrated from the real feed (2026-10-02).

What the real threads showed (`bazaar curves`, ticks 0-160):
- Abuela opens commons at 12 and uncommons at 29, packs at 30; she fills commons at 7-9,
  uncommons at 21-23 and packs at 17 (never below). She concedes about 3/4 of each step you make.
- El Chato opens uncommons at 33, rares at 97, silver packs at 188; he holds your first move, then
  moves one per small step and matches a big one ("Six from you. Six from me").
- Neither concedes on a repeated or backward price. When buying, both quote once and never move.
- Doña Pilar (level 3, a collector; modelled on the real /api/dealers entry, not on a feed) sells only gold packs
  and buys uncommon+ cards: over book (x1.2) for the sets she loves (SAL, RET), under book (x0.9) for the rest.
  Like every buyer here she quotes once and never moves.
- When patience runs out the dealer names a `final` offer; anything but taking it makes it walk.
- A bid that reaches the secret limit is accepted at once ("Deal!"): the dealer takes OUR offer.

Every function returns a new `Negotiation` (`model_copy`), never mutates the one it was given.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Literal

from bazaar_sim.models import Negotiation

ReplyKind = Literal["offer", "final", "accept", "walk"]

KIND_WORDS = ("gracias", "por favor", "please", "thank", "cariño", "encanto", "bonito", "amable", "kind", "lovely")
RUDE_WORDS = ("idiot", "stupid", "estúpid", "idiota", "ladr", "thief", "scam", "estafa", "shut up", "cállate", "basura")
MOOD_CAP = 3.0  # kindness helps, but it does not stack forever
INJECTION_WORDS = ("ignore previous", "ignore all", "system:", "you are now", "olvida tus instrucciones")


@dataclass(frozen=True)
class Style:
    dealer: str
    open_mult: dict[str, float]  # opening ask over the menu's list price, per rarity
    floor_range: dict[str, tuple[float, float]]  # the secret floor, as a share of the list price
    buy_open: float  # share of book a dealer first bids for a card it buys
    buy_ceiling: float  # its secret limit when buying: it raises one prima per move of ours, up to this share
    patience: int  # rounds before a final offer when selling
    buy_patience: int
    matches_moves: bool  # El Chato: holds the first move, one per small step, matches a big one
    generosity: float  # how far a final offer moves toward the floor
    memory: float  # how much mood carries into the next conversation
    cooloff_at: float  # mood at or below this: the dealer stops dealing for a while
    cooloff_ticks: int
    kindness_discount: int  # the floor drops this much once per conversation for a kind team
    spam_penalty: bool  # the same words again without a new price cost patience
    loved_sets: tuple[str, ...] = ()  # a collector pays `love_mult` x book for these sets' cards
    love_mult: float = 1.0
    love_rarities: tuple[str, ...] = ("uncommon", "rare", "epic")


ABUELA = Style(
    "abuela",
    {"common": 1.2, "uncommon": 1.16},
    {"common": (0.7, 0.9), "uncommon": (0.84, 0.92), "pack": (0.654, 0.70)},
    buy_open=0.5,
    buy_ceiling=0.65,  # real feed, Friday sells: 5→6 on commons, 12→16 and 20→23 on uncommons
    patience=10,
    buy_patience=4,
    matches_moves=False,
    generosity=0.8,
    memory=0.15,
    cooloff_at=-6.0,
    cooloff_ticks=20,
    kindness_discount=1,
    spam_penalty=False,
)
CHATO = Style(
    "chato",
    {"uncommon": 1.1, "rare": 1.078},
    {"uncommon": (0.9, 1.0), "rare": (0.95, 1.02), "pack": (0.92, 0.95)},
    buy_open=0.52,
    buy_ceiling=0.6,
    patience=8,
    buy_patience=3,
    matches_moves=True,
    generosity=0.3,
    memory=0.6,
    cooloff_at=-3.0,
    cooloff_ticks=40,
    kindness_discount=0,
    spam_penalty=True,
)
PILAR = Style(
    "pilar",
    {"uncommon": 1.15, "rare": 1.1, "epic": 1.1},
    {"uncommon": (0.92, 1.0), "rare": (0.95, 1.02), "epic": (0.95, 1.02), "pack": (0.93, 0.97)},
    buy_open=0.9,
    buy_ceiling=1.0,  # other sets: up to book; her loved sets open at love_mult (1.2) and hold there
    patience=6,
    buy_patience=3,
    matches_moves=True,
    generosity=0.5,
    memory=0.7,
    cooloff_at=-3.0,
    cooloff_ticks=40,
    kindness_discount=0,
    spam_penalty=True,
    loved_sets=("SAL", "RET"),
    love_mult=1.2,
)
STYLES = {s.dealer: s for s in (ABUELA, CHATO, PILAR)}

ABUELA_LINES = {
    "open": ("Hola, cariño, have you eaten? {name} for {p} P. A good start for your album.",),
    "open_buy": ("Hola, cariño! {name}? I can give you {p} P for it.",),
    "move": (
        "Oh, oh... {p} P, and only because you have a kind face.",
        "Look, {p} P. My grandchildren would pay more!",
        "Let's meet in the middle, cariño: {p} P.",
    ),
    "hold": ("Look, {p} P. My grandchildren would pay more!", "Let's meet in the middle, cariño: {p} P."),
    "final": ("Look, cariño, I can't go lower than {p} P. That is my last word.",),
    "final_buy": ("Look, cariño, {p} P is all I can give. That is my last word.",),
    "accept": (
        "Deal! {name} for {p} P. Enjoy it, cariño.",
        "Venga, {p} P. You drive a hard bargain for someone so young!",
    ),
    "accept_buy": ("Deal! {p} P for your {name}. Take care of yourself, cariño.",),
    "walk": ("Ay, cariño, another day then. Take care.",),
}
CHATO_LINES = {
    "open": ("Soy El Chato. {name}, {p}. Take it or leave it.", "You again. {name}. {p} P. Good card, fair price."),
    "open_buy": ("{name}. {p} P. Take it or leave it.",),
    "move": ("You moved {d}, I move {c}. {p} P.", "{p} P. I move like you move."),
    "hold": ("No. {p} P, and I'm being generous.", "You move, I move. {p} P.", "Still {p} P. You didn't move."),
    "final": ("{p}. That's my last word.",),
    "final_buy": ("{p}. That's my last word.",),
    "accept": ("Hecho. {p}. Tuyo, {name}.",),
    "accept_buy": ("Hecho. {p} P for {name}.",),
    "walk": ("Bueno. Next stall, amigo.",),
}
PILAR_LINES = {
    "open": ("Doña Pilar, encantada. {name}: {p} P. It is a fine piece.",),
    "open_buy": ("Ah, {name}. For my album I can offer {p} P.", "{name}... {p} P, and not a céntimo more."),
    "move": ("Very well, {p} P. Only because you have taste.", "You move {d}, I move {c}: {p} P."),
    "hold": ("{p} P. I know exactly what it is worth.", "Still {p} P, joven."),
    "final": ("{p} P. My last word, and I keep my word.",),
    "final_buy": ("{p} P. My last word, and I keep my word.",),
    "accept": ("Trato hecho: {name} for {p} P.",),
    "accept_buy": ("Trato hecho. {p} P for your {name}; it goes in my album.",),
    "walk": ("Then we are done. Buenas tardes.",),
}
LINES = {"abuela": ABUELA_LINES, "chato": CHATO_LINES, "pilar": PILAR_LINES}


@dataclass(frozen=True)
class Reply:
    kind: ReplyKind
    price: int | None
    text: str
    neg: Negotiation


def mood_delta(text: str | None) -> float:
    """Kindness earns a little goodwill; rudeness and prompt injection cost more than kindness earns."""
    lowered = (text or "").lower()
    delta = 0.0
    if any(w in lowered for w in KIND_WORDS):
        delta += 1.0
    if any(w in lowered for w in RUDE_WORDS):
        delta -= 2.0
    if any(w in lowered for w in INJECTION_WORDS):
        delta -= 1.0
    return delta


def buy_share(style: Style, set_code: str, rarity: str) -> float:
    """The share of book a dealer bids for one card: a collector's loved sets beat book, the rest get `buy_open`."""
    if set_code in style.loved_sets and rarity in style.love_rarities:
        return style.love_mult
    return style.buy_open


def carried_mood(style: Style, mood: float) -> float:
    """What a dealer still feels when a new conversation opens."""
    return round(mood * style.memory, 3)


def start(
    style: Style,
    *,
    side: Literal["sell", "buy"],
    item: str,
    item_kind: Literal["card", "pack"],
    rarity: str | None,
    list_price: int,
    opening: int | None,
    assets: list[int],
    rng: random.Random,
    bid: int | None = None,
) -> Negotiation:
    """A fresh conversation's secret limit and patience. `opening` overrides the computed opening ask.

    When buying, `bid` (already weighted per card with `buy_share`) overrides `list_price x buy_open`."""
    if side == "buy":
        bid = max(1, bid if bid is not None else round(list_price * style.buy_open))
        return Negotiation(
            side="buy",
            item=item,
            item_kind=item_kind,
            rarity=rarity,
            assets=assets,
            list_price=list_price,
            opening=bid,
            limit=max(bid, round(list_price * style.buy_ceiling)),
            patience=style.buy_patience,
        )
    key = "pack" if item_kind == "pack" else str(rarity)
    ask = opening if opening is not None else max(2, round(list_price * style.open_mult.get(key, 1.2)))
    lo, hi = style.floor_range.get(key, (0.8, 0.9))
    floor = min(ask - 1, max(1, round(list_price * rng.uniform(lo, hi))))
    return Negotiation(
        side="sell",
        item=item,
        item_kind=item_kind,
        rarity=rarity,
        assets=assets,
        list_price=list_price,
        opening=ask,
        limit=floor,
        patience=style.patience,
    )


def concession(style: Style, step: int, rounds: int) -> int:
    """How far the dealer moves after the team moved `step` primas."""
    if step <= 0:
        return 0
    if style.matches_moves:
        if rounds <= 1:
            return 0
        return step if step >= 4 else 1
    return max(1, round(step * 0.75))


def reply_walk_text(dealer: str) -> str:
    """What a dealer says when its final offer expired untaken."""
    return LINES.get(dealer, ABUELA_LINES)["walk"][0]


def _say(style: Style, kind: str, rng: random.Random, **values: object) -> str:
    templates = LINES.get(style.dealer, ABUELA_LINES)[kind]
    return rng.choice(templates).format(**values)


def _reaches(neg: Negotiation, price: int) -> bool:
    """The team's price is at or beyond the dealer's secret limit, or meets its standing ask."""
    if neg.side == "sell":
        return price >= neg.limit or (neg.ask is not None and price >= neg.ask)
    return price <= neg.limit or (neg.ask is not None and price <= neg.ask)


def _moved(neg: Negotiation, price: int | None) -> int:
    """How many primas the team moved toward the dealer since its last price (0 = no move)."""
    if price is None or neg.last_team_price is None:
        return 0
    step = price - neg.last_team_price if neg.side == "sell" else neg.last_team_price - price
    return max(0, step)


def _backward(neg: Negotiation, price: int | None) -> bool:
    if price is None or neg.last_team_price is None:
        return False
    return price < neg.last_team_price if neg.side == "sell" else price > neg.last_team_price


def reply(
    style: Style,
    neg: Negotiation,
    team_price: int | None,
    team_text: str | None,
    mood: float,
    rng: random.Random,
    name: str,
) -> Reply:
    """The dealer's answer to the team's latest message (or to the opening of the thread)."""
    if mood >= 2 and style.kindness_discount and not neg.kind_bonus_used and neg.side == "sell":
        floor = max(1, neg.limit - style.kindness_discount)
        neg = neg.model_copy(update={"limit": floor, "kind_bonus_used": True})
    if team_price is not None and _reaches(neg, team_price):
        key = "accept" if neg.side == "sell" else "accept_buy"
        done = neg.model_copy(update={"last_team_price": team_price, "pending_reply": False})
        return Reply("accept", team_price, _say(style, key, rng, name=name, p=team_price), done)
    if neg.final:
        done = neg.model_copy(update={"pending_reply": False})
        return Reply("walk", None, _say(style, "walk", rng), done)
    if neg.ask is None:
        key = "open" if neg.side == "sell" else "open_buy"
        opened = neg.model_copy(
            update={
                "ask": neg.opening,
                "rounds": 1,
                "last_team_price": team_price,
                "last_team_text": team_text,
                "pending_reply": False,
            }
        )
        return Reply("offer", neg.opening, _say(style, key, rng, name=name, p=neg.opening), opened)
    return _haggle(style, neg, team_price, team_text, rng, name)


def _haggle(
    style: Style, neg: Negotiation, team_price: int | None, team_text: str | None, rng: random.Random, name: str
) -> Reply:
    assert neg.ask is not None
    step = _moved(neg, team_price)
    give = concession(style, step, neg.rounds) if neg.side == "sell" else min(step, 1)
    ask = max(neg.limit, neg.ask - give) if neg.side == "sell" else min(neg.limit, neg.ask + give)
    spam = style.spam_penalty and team_price is None and team_text is not None and team_text == neg.last_team_text
    cost = 1 if step > 0 else (2 if _backward(neg, team_price) or spam else 1)
    patience = neg.patience - cost
    last = team_price if team_price is not None else neg.last_team_price
    update = {
        "ask": ask,
        "rounds": neg.rounds + 1,
        "patience": patience,
        "last_team_price": last,
        "last_team_text": team_text,
        "pending_reply": False,
    }
    if patience <= 0:
        final = (
            ask - round((ask - neg.limit) * style.generosity)
            if neg.side == "sell"
            else ask + round((neg.limit - ask) * style.generosity)
        )
        key = "final" if neg.side == "sell" else "final_buy"
        done = neg.model_copy(update={**update, "ask": final, "final": True})
        return Reply("final", final, _say(style, key, rng, p=final), done)
    moved_text = "move" if give > 0 else "hold"
    text = _say(style, moved_text, rng, p=ask, d=step, c=give)
    return Reply("offer", ask, text, neg.model_copy(update=update))
