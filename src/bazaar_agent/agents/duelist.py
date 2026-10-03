"""Duels: log every raw /api/duels response each tick, and (with --play) play inside our limit.

The duel shape in docs/api/openapi.json is unverified (x-verified: false), so the logger stores raw
responses first and the policy reads fields defensively. Rules (RULES.md): we see only our own limit
(a seller's cost or a buyer's value); a deal outside it loses points; no deal scores zero; the pie
shrinks with every round of talk; a priced message in a two-issue session must also carry `days`.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from bazaar_agent.agents.bluff import Choice, Counterparty, TacticBook
from bazaar_agent.agents.tactics import Side, private_numbers
from bazaar_agent.agents.words import WordsRequest

ANCHOR = 0.6  # open this far beyond our limit (fraction of the limit)
FLOOR_MARGIN = 0.05  # never settle closer than this to our limit (fraction), until the last ticks
ENDGAME_TICKS = 2  # in the last ticks, any deal strictly inside our limit beats no deal
DUEL_WORDS = "Propongo este precio, creo que es justo para los dos."


@dataclass(frozen=True)
class DuelMove:
    kind: Literal["accept", "offer", "hold"]
    price: int | None = None
    days: int | None = None
    reason: str = ""


def _rival_price(duel: dict[str, Any]) -> int | None:
    offer = duel.get("rival_offer")
    if isinstance(offer, dict) and isinstance(offer.get("price"), int | float):
        return int(offer["price"])
    return None


def effective_price(duel: dict[str, Any], price: int) -> float | None:
    """The rival's price, adjusted for delivery days in two-issue duels. None = cannot value it safely.

    The sign of `your_days_weight` is not verified yet, so we assume the worst: days always cost us.
    """
    if "days" not in (duel.get("issues") or []):
        return float(price)
    offer = duel.get("rival_offer") or {}
    days, weight = offer.get("days"), duel.get("your_days_weight")
    if not isinstance(days, int | float) or not isinstance(weight, int | float):
        return None
    penalty = abs(float(weight)) * float(days)
    return price - penalty if duel.get("role") == "seller" else price + penalty


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
    """Our ask (seller) or bid (buyer) at `progress` 0..1 of the duel: anchor → limit ± margin."""
    progress = min(1.0, max(0.0, progress))
    reach = anchor - (anchor - floor) * progress
    price = limit * (1 + reach) if role == "seller" else limit * (1 - reach)
    return max(1, round(price))


def inside_limit(price: int, limit: int, role: str) -> bool:
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
    days = 5 if "days" in (duel.get("issues") or []) else None  # neutral until the days module (#7)
    rival = _rival_price(duel)
    worth = effective_price(duel, rival) if rival is not None else None
    if rival is not None and worth is not None and inside_limit(round(worth), limit, role):
        good_enough = worth >= target if role == "seller" else worth <= target
        if good_enough or left <= endgame_ticks:
            return DuelMove(
                "accept", rival, reason="rival meets our target" if good_enough else "endgame, inside limit"
            )
    return DuelMove("offer", target, days, reason=f"concede toward limit ({left} ticks left)")


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
        handle.write(json.dumps(record, separators=(",", ":"), ensure_ascii=False) + "\n")
