"""Duels: log every raw /api/duels response each tick, and (with --play) play inside our limit.

The duel shape in docs/api/openapi.json is unverified (x-verified: false), so the logger stores raw
responses first and the policy reads fields defensively. Rules (RULES.md): we see only our own limit
(a seller's cost or a buyer's value); a deal outside it loses points; no deal scores zero; the pie
shrinks with every round of talk; a priced message in a two-issue session must also carry `days`.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from bazaar_agent.agents.words import WordsRequest

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


def _rival_price(duel: dict[str, Any]) -> int | None:
    offer = duel.get("rival_offer")
    if isinstance(offer, dict) and isinstance(offer.get("price"), int | float):
        return int(offer["price"])
    return None


def _two_issue(duel: Mapping[str, Any]) -> bool:
    return "days" in (duel.get("issues") or [])


def _number(value: object) -> float | None:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


def worth(duel: Mapping[str, Any], price: int, days: object) -> float | None:
    """A price with its delivery days, valued in the worst case. None = cannot value it safely.

    The sign of `your_days_weight` is not verified yet, so we assume the worst: days always cost us.
    """
    if not _two_issue(duel):
        return float(price)
    n_days, weight = _number(days), _number(duel.get("your_days_weight"))
    if n_days is None or weight is None:
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


def our_price(target: int, role: str, days: int, weight: float) -> int:
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
    if rival is not None and theirs is not None and inside_limit(round(theirs), limit, role):
        good_enough = theirs >= target if role == "seller" else theirs <= target
        if good_enough or left <= endgame_ticks:
            return DuelMove(
                "accept", rival, reason="rival meets our target" if good_enough else "endgame, inside limit"
            )
    ours = worth(duel, price, days)
    if price < 1 or ours is None or not inside_limit(ours, limit, role):
        return DuelMove("hold", reason=f"no offer strictly inside our limit {limit}")
    return DuelMove("offer", price, days, reason=f"concede toward limit ({left} ticks left)")


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


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, separators=(",", ":"), ensure_ascii=False) + "\n")
