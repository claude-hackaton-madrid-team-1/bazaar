"""Duel outcomes: the share of each deal's pie we captured, deals outside our limit, rounds vs decay.

RULES.md "Duels": we see only our own limit (a seller's cost, a buyer's value); a deal outside it
loses points; no deal scores zero; the value of a deal shrinks with every round of talk.

Verified on the practice session (`GET /api/duels?done=true`, 2026-10-02): a finished duel has
`status` deal | no_deal, `price`, `rounds`, `decay_per_round` and `result`, which is our surplus
times (1 - decay) ** rounds (duel 85: (138 - 109) * 0.94 ** 7 = 18.8). There is no `share` and no
pie: the rival's limit stays private. So the pie is bounded from below by what the rival revealed
(a buyer who bid B values the item at B or more; a seller who asked A costs A or less), and the
score is our surplus over that bound, times the value kept after decay. It is an upper bound on
the true share; an API `share`, if one ever appears, replaces it.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from bazaar_agent.agents.duelist import duel_deadline, duel_id
from bazaar_agent.evals.model import Outcome, clamp01, label_for

OUR_SENDER = "you"  # how /api/duels names our own messages (verified, practice session)


@dataclass(frozen=True)
class Closure:
    """A `duel.closed` feed event: the public status and the tick it closed."""

    status: str  # deal | no_deal
    tick: int | None


def _num(value: object) -> float | None:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


def _list(duel: Mapping[str, Any], key: str) -> list[Any]:
    """A list field of a stored payload; anything else (a malformed import) reads as empty."""
    value = duel.get(key)
    return value if isinstance(value, list) else []


def _worth(duel: Mapping[str, Any], price: float, days: object) -> float:
    """A price adjusted for delivery days in two-issue duels, as the duel player values it: the sign
    of `your_days_weight` is unverified, so days always count against us (duelist.effective_price)."""
    weight = _num(duel.get("your_days_weight"))
    n_days = _num(days)
    if "days" not in _list(duel, "issues") or weight is None or n_days is None:
        return price
    penalty = abs(weight) * n_days
    return price - penalty if duel.get("role") == "seller" else price + penalty


def _rival_worths(duel: Mapping[str, Any]) -> list[float]:
    offers = [m for m in _list(duel, "messages") if isinstance(m, Mapping) and m.get("from") != OUR_SENDER]
    if isinstance(duel.get("rival_offer"), Mapping):
        offers.append(duel["rival_offer"])
    return [_worth(duel, p, o.get("days")) for o in offers if (p := _num(o.get("price"))) is not None]


def _played(duel: Mapping[str, Any]) -> bool:
    mine = any(isinstance(m, Mapping) and m.get("from") == OUR_SENDER for m in _list(duel, "messages"))
    return mine or isinstance(duel.get("your_offer"), Mapping)


def _surplus(role: str, limit: float, worth: float) -> float:
    """What a deal at `worth` gives us before decay: a seller above its cost, a buyer below its value."""
    return worth - limit if role == "seller" else limit - worth


def _best_for_us(role: str, worths: list[float]) -> float | None:
    if not worths:
        return None
    return max(worths) if role == "seller" else min(worths)


def score_duel(duel: Mapping[str, Any], closure: Closure | None = None) -> Outcome | None:
    """One finished duel, or None while it is still live (or unreadable)."""
    did, role, limit = duel_id(duel), duel.get("role"), _num(duel.get("your_limit"))
    status = duel.get("status")
    if status in (None, "live") and closure is not None:
        status = closure.status
    if did is None or role not in ("seller", "buyer") or limit is None or status not in ("deal", "no_deal"):
        return None
    tick = closure.tick if closure is not None and closure.tick is not None else duel_deadline(duel)
    rounds = int(_num(duel.get("rounds")) or 0)
    decay = _num(duel.get("decay_per_round")) or 0.0
    kept = (1.0 - decay) ** rounds
    best = _best_for_us(role, _rival_worths(duel))
    details: dict[str, Any] = {
        "duel": did,
        "session": duel.get("session"),
        "role": role,
        "item": duel.get("item"),
        "limit": limit,
        "status": status,
        "rounds": rounds,
        "decay_per_round": decay,
        "decay_kept": round(kept, 4),
        "rival_best": best,
        "played": _played(duel),
        "issues": _list(duel, "issues"),
    }
    base = Outcome("duel", f"duel:{did}", None, "ok", "", tick, details=details)
    if status == "no_deal":
        return _no_deal(base, role, limit, best)
    return _deal(base, duel, role, limit, best, kept)


def _no_deal(base: Outcome, role: str, limit: float, best: float | None) -> Outcome:
    missed = _surplus(role, limit, best) if best is not None else None
    played = base.details["played"]
    details = {**base.details, "missed_surplus": missed if missed is not None and missed > 0 else 0}
    if missed is not None and missed > 0:
        how = "" if played else " We never sent an offer (log only)."
        text = (
            f"No deal (scores zero). The rival offered {best:g}, inside our limit {limit:g}: "
            f"{missed:g} P left on the table.{how}"
        )
        return _replace(base, 0.0, "bad", text, 0.0, details)
    seen = f"its best offer was {best:g}" if best is not None else "it made no offer"
    text = f"No deal (scores zero). Walking cost nothing: {seen}, never inside our limit {limit:g}."
    return _replace(base, 0.0, "ok", text, 0.0, details)


def _deal(base: Outcome, duel: Mapping[str, Any], role: str, limit: float, best: float | None, kept: float) -> Outcome:
    price = _num(duel.get("price"))
    result = _num(duel.get("result"))
    if price is None:
        text = "Deal closed, price not known yet: `bazaar duel done` stores the finished duel."
        return _replace(base, None, "ok", text, result, base.details)
    worth = _worth(duel, price, duel.get("days"))
    two_issue = "days" in _list(duel, "issues")
    # Price alone fixes our surplus; with delivery days only the API's `result` knows our day weight.
    raw = result / kept if two_issue and result is not None and kept > 0 else _surplus(role, limit, worth)
    after = result if result is not None else raw * kept
    details = {**base.details, "price": price, "surplus_before_decay": round(raw, 2), "result": round(after, 2)}
    if raw < 0:
        side = "sold below our cost" if role == "seller" else "paid above our value"
        text = f"Deal OUTSIDE our limit: {side} ({price:g} vs {limit:g}, {raw:+.1f} P). It loses points."
        return _replace(base, 0.0, "bad", text, after, {**details, "outside_limit": True})
    reach = best if best is not None else worth
    edge = max(reach, worth) if role == "seller" else min(reach, worth)
    pie = _surplus(role, limit, edge)
    share = clamp01(raw / pie) if pie > 0 else 0.0
    score = share * kept
    official = _num(duel.get("share"))
    if official is not None:  # an API share, if one ever appears, is the truth
        share, score = clamp01(official), clamp01(official) * kept
    text = (
        f"Deal at {price:g} vs our limit {limit:g}: {raw:+.1f} P, {after:.1f} P after {base.details['rounds']} rounds "
        f"(kept {kept:.0%}). The rival revealed a pie of at least {pie:g} P, so our share is at most {share:.0%}."
    )
    details |= {"pie_lower_bound": pie, "share_upper_bound": round(share, 4), "outside_limit": False}
    return _replace(base, score, label_for(score), text, after, details)


def _replace(
    base: Outcome, score: float | None, label: Any, text: str, surplus: float | None, details: Mapping[str, Any]
) -> Outcome:
    return Outcome(
        base.target,
        base.subject,
        None if score is None else round(score, 4),
        label,
        text,
        base.tick,
        surplus=None if surplus is None else round(surplus, 2),
        details=details,
    )
