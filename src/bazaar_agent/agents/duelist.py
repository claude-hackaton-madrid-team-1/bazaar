"""Duels: log every raw /api/duels response each tick, and (with --play) play inside our limit.

The duel shape in docs/api/openapi.json is unverified (x-verified: false), so the logger stores raw
responses first and the policy reads fields defensively. Rules (RULES.md): we see only our own limit
(a seller's cost or a buyer's value); a deal outside it loses points; no deal scores zero; the pie
shrinks with every round of talk; a priced message in a two-issue session must also carry `days`.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from bazaar_agent.agents.bluff import Choice, Counterparty, TacticBook
from bazaar_agent.agents.tactics import Side, private_numbers
from bazaar_agent.agents.words import WordsRequest
from bazaar_agent.guardrails import Action, duel_days_ok
from bazaar_agent.sdk import BazaarError

ANCHOR = 0.6  # open this far beyond our limit (fraction of the limit)
FLOOR_MARGIN = 0.05  # never settle closer than this to our limit (fraction), until the last ticks
ENDGAME_TICKS = 2  # in the last ticks, any deal strictly inside our limit beats no deal
DUEL_WORDS = "Propongo este precio, creo que es justo para los dos."
# RULES.md: two-issue duels trade price and delivery days, 0 to 10. The sign of `your_days_weight` is not
# verified, so we value every day at |weight| against us; 0 days is then the only free choice for our offers.
OUR_DAYS = 0
ROUNDING_SLACK = 1e-9  # 100 × 1.05 is 105.00000000000001 in floats: never let that round a price up


@dataclass(frozen=True)
class DuelMove:
    kind: Literal["accept", "offer", "hold"]
    price: int | None = None
    days: int | None = None
    reason: str = ""


def _rival_price(duel: Mapping[str, Any]) -> int | None:
    """The rival's standing price, only when it is a real integer (True is an int, int(101.7) is 101: never)."""
    offer = duel.get("rival_offer")
    price = offer.get("price") if isinstance(offer, dict) else None
    if isinstance(price, int) and not isinstance(price, bool):
        return price
    if isinstance(price, float) and price.is_integer():  # False for NaN and infinities
        return int(price)
    return None


def _two_issue(duel: Mapping[str, Any]) -> bool:
    """Days are negotiated when `issues` says so or the rival's offer carries non-zero days; without an `issues` list,
    also whenever our day weight is a number: a payload that drops `issues` must not let days slip by unvalued (r2
    bite B2a), and an explicit price-only list is trusted (r1)."""
    issues = duel.get("issues")
    offer = duel.get("rival_offer")
    rival_days = offer.get("days") if isinstance(offer, dict) else None
    if isinstance(issues, list | tuple):  # explicit: trust it, unless the rival's offer carries days anyway
        return "days" in issues or rival_days not in (None, 0)
    return _number(duel.get("your_days_weight")) is not None or rival_days not in (None, 0)


def _number(value: object) -> float | None:
    """A finite number from the payload, else None (bools, NaN, infinities and ints too large for a float)."""
    if not isinstance(value, int | float) or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except OverflowError:  # a 400-digit int: math.isfinite raised on it and stopped the caller (#165 P3-3)
        return None
    return number if math.isfinite(number) else None


def worth(duel: Mapping[str, Any], price: int, days: object, zero_days_free: bool = False) -> float | None:
    """A price with its delivery days, valued in the worst case. None = cannot value it safely.

    The sign of `your_days_weight` is not verified yet, so we assume the worst: days always cost us.
    """
    if not _two_issue(duel):
        return float(price)
    n_days, weight = _number(days), _number(duel.get("your_days_weight"))
    if zero_days_free and n_days == 0:  # v2: 0 days cost nothing whatever the weight, even a missing one (B2c)
        return float(price)
    if n_days is None or weight is None or not duel_days_ok(n_days):  # days outside 0 to 10: never valued
        return None
    penalty = abs(weight) * n_days
    return price - penalty if duel.get("role") == "seller" else price + penalty


def effective_price(duel: dict[str, Any], price: int) -> float | None:
    """The rival's price, adjusted for the days of its offer in two-issue duels. None = cannot value it."""
    return worth(duel, price, (duel.get("rival_offer") or {}).get("days"))


def duel_id(duel: Mapping[str, Any]) -> int | None:
    """The live API names it `duel` (verified 2026-10-02, practice session); `id` kept for older shapes."""
    value = duel.get("duel", duel.get("id"))
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def duel_deadline(duel: Mapping[str, Any]) -> int | None:
    value = duel.get("deadline_tick", duel.get("deadline"))
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def duel_done(duel: Mapping[str, Any]) -> bool:
    status = duel.get("status")
    return bool(duel.get("done")) or (status is not None and status != "live") or duel.get("result") is not None


def our_target(limit: int, role: str, progress: float, anchor: float = ANCHOR, floor: float = FLOOR_MARGIN) -> int:
    """Our ask (seller) or bid (buyer) at `progress` 0..1 of the duel: anchor → limit ± margin.
    Rounded away from the limit (a seller up, a buyer down), so the margin never rounds to zero."""
    progress = min(1.0, max(0.0, progress))
    reach = anchor - (anchor - floor) * progress
    if role == "seller":
        return max(1, math.ceil(limit * (1 + reach) - ROUNDING_SLACK))
    return max(1, math.floor(limit * (1 - reach) + ROUNDING_SLACK))


def our_price(target: float, role: str, days: int, weight: float) -> int:
    """The price that keeps our target after the worst-case cost of `days`, rounded to our side."""
    penalty = abs(weight) * days
    if role == "seller":
        return math.ceil(target + penalty - ROUNDING_SLACK)
    return math.floor(target - penalty + ROUNDING_SLACK)


def inside_limit(price: float, limit: int, role: str) -> bool:
    """Strictly better than our limit: a seller above its cost, a buyer below its value."""
    return price > limit if role == "seller" else price < limit


def duel_move(
    duel: dict[str, Any],
    tick: int,
    started_tick: int,
    *,
    anchor: float = ANCHOR,
    floor: float = FLOOR_MARGIN,
    endgame_ticks: int = ENDGAME_TICKS,
) -> DuelMove:
    limit, role = duel.get("your_limit"), duel.get("role")
    deadline = duel_deadline(duel)
    if duel_done(duel) or not isinstance(limit, int) or role not in ("seller", "buyer"):
        return DuelMove("hold", reason="done or unreadable duel")
    total = max(1, (deadline - started_tick) if isinstance(deadline, int) else 12)
    left = (deadline - tick) if isinstance(deadline, int) else total
    target = our_target(limit, role, (tick - started_tick) / total, anchor, floor)
    weight = _number(duel.get("your_days_weight"))
    if _two_issue(duel) and weight is None:
        return DuelMove("hold", reason="two-issue duel without your_days_weight: cannot value days")
    days = OUR_DAYS if _two_issue(duel) else None
    price = our_price(target, role, days, weight or 0.0) if days is not None else target
    rival = _rival_price(duel)
    theirs = effective_price(duel, rival) if rival is not None else None
    if rival is not None and theirs is not None and inside_limit(theirs, limit, role):
        good_enough = theirs >= target if role == "seller" else theirs <= target
        if good_enough or left <= endgame_ticks:
            return DuelMove(
                "accept", rival, reason="rival meets our target" if good_enough else "endgame, inside limit"
            )
    ours = worth(duel, price, days)
    if price < 1 or ours is None or not inside_limit(ours, limit, role):
        return DuelMove("hold", reason=f"no offer strictly inside our limit {limit}")
    return DuelMove("offer", price, days, reason=f"concede toward limit ({left} ticks left)")


RETRY_MIN_LEFT_S = 1.5  # a retry needs this much of the tick left (a send is ~0.1-0.3 s, the SDK timeout is longer)


def send_with_one_retry(call: Callable[[], Any], time_left: Callable[[], float]) -> tuple[Any, bool]:
    """Send a duel message or accept; after a `network` error (no answer at all) send it once more in the same
    tick, only while `time_left()` allows. Returns (answer, retried). Never retries a 4xx or a 5xx (the game may
    have applied those). The game takes one message per side per tick, so a retry cannot double-send: if it is
    answered `wait_for_tick` the first one landed and the send counts as made (answer None, no third try). Any
    other failure of the retry raises the FIRST error, so the caller books it as a maybe-landed send."""
    try:
        return call(), False
    except BazaarError as first:
        if first.code != "network" or time_left() < RETRY_MIN_LEFT_S:
            raise
        try:
            return call(), True
        except BazaarError as second:
            if second.code == "wait_for_tick":
                return None, True
            raise first from second


def duel_action(duel: Mapping[str, Any], move: DuelMove) -> Action:
    """The guardrail's view of a duel move: the price and days we would agree to, with our limit and role.
    An accept takes the rival's standing price and days: an unreadable price, or one that is not the move's, goes out
    without a price (denied). Two-issue terms we cannot read go out without a price too."""
    accept = move.kind == "accept"
    days = (duel.get("rival_offer") or {}).get("days") if accept else move.days
    n_days = _number(days) if _two_issue(duel) else None
    terms = _rival_price(duel) if accept else move.price
    if accept and terms != move.price:
        terms = None
    price = terms if not _two_issue(duel) or n_days is not None else None
    limit, role = duel.get("your_limit"), duel.get("role")
    return Action(
        "duel_accept" if move.kind == "accept" else "duel_offer",
        str(duel_id(duel)),
        price=price,
        limit=limit if isinstance(limit, int) and not isinstance(limit, bool) else None,
        role=role if isinstance(role, str) else None,
        days=n_days,
        days_weight=_number(duel.get("your_days_weight")),
    )


def template_duel_words(request: WordsRequest) -> str:
    """The default duel `WordsFn`: one fixed polite line (the price travels as the structured field)."""
    return DUEL_WORDS


def rival_text(duel: dict[str, Any]) -> str | None:
    """The rival's latest words, read defensively (the duel shape is unverified). Untrusted input."""
    offer = duel.get("rival_offer")
    for text in (offer.get("text") if isinstance(offer, dict) else None, duel.get("rival_text")):
        if isinstance(text, str) and text.strip():
            return text
    return None


def rival_offer(duel: dict[str, Any]) -> tuple[int | None, int | None]:
    """(price, offer id) of the rival's standing offer, read defensively."""
    offer = duel.get("rival_offer")
    if not isinstance(offer, dict):
        return None, None
    oid = offer.get("id")
    return _rival_price(duel), oid if isinstance(oid, int) and not isinstance(oid, bool) else None


def our_duel_messages(duel: dict[str, Any]) -> int:
    """How many messages we already sent in this duel, from the duel's own list (`from: "you"`)."""
    messages = duel.get("messages")
    return (
        sum(1 for m in messages if isinstance(m, dict) and m.get("from") == "you") if isinstance(messages, list) else 0
    )


def spoke_this_tick(duel: Mapping[str, Any], tick: int) -> bool:
    """True when the duel already shows a message of ours at `tick`. The game takes one message per side per tick,
    so a second one is refused (`wait_for_tick`): what a runner restarted mid-tick (every merge to main redeploys
    `duel run`) would send right after the process it replaced."""
    messages = duel.get("messages")
    said = isinstance(messages, list) and any(
        isinstance(m, dict) and m.get("from") == "you" and m.get("tick") == tick for m in messages
    )
    offer = duel.get("your_offer")
    return said or (isinstance(offer, dict) and offer.get("tick") == tick)


def duel_choice(book: TacticBook | None, duel: dict[str, Any], did: int, move: DuelMove, step: int) -> Choice | None:
    """The bluff tactic for an OFFER's text (N16). An accept or a hold gets none: an accept that is already good
    is sent as it is, never delayed or replaced by a bluff. The price and days stay the move's own."""
    role = duel.get("role")
    if book is None or move.kind != "offer" or move.price is None or role not in ("seller", "buyer"):
        return None
    private = private_numbers(duel.get("your_limit"), duel.get("your_days_weight"))
    side: Side = "sell" if role == "seller" else "buy"
    cp, their = Counterparty.rival(duel.get("rival"), did), _rival_price(duel)
    return book.choose(cp, side, f"duel:{did}", step, move.price, avoid=private, their_price=their)


def observe_duel(book: TacticBook | None, duel: dict[str, Any], did: int, tick: int) -> None:
    """Score our last duel tactic: the rival's next offer, or the duel's end (deal / no deal)."""
    if book is None:
        return
    if duel_done(duel):
        status = duel.get("status")
        book.ended(f"duel:{did}", status=status if isinstance(status, str) else None, closed_reason=None, tick=tick)
        return
    price, offer_id = rival_offer(duel)
    book.observe(f"duel:{did}", their_price=price, their_offer=offer_id, tick=tick)


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        # ASCII-escaped: a rival's text with a lone surrogate must never stop the duel loop
        handle.write(json.dumps(record, separators=(",", ":")) + "\n")
