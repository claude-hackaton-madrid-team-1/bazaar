"""Lessons from outcomes: every settled decision the evals scored becomes one structured `Learning`.

A lesson is the situation (counterparty, item, price class, our ladder, the dealer's opening ask,
rounds, what the market fills at), the action we took, the outcome, the delta against what we could
have expected (price paid vs the dealer's floor, share of the range captured, P left on the table)
and one short sentence of what to do next time. It is built from numbers only: structure, never a
counterparty's words. The text is what the hybrid recall searches and what Jev and the words model
read, as quoted data.

Kinds written here (all `source="outcome"`, `team` = us):
- `lesson` per scored outcome (`thread:115`, `duel:85`, `settlement:67`);
- `behaviour` per (dealer, price class) from every team's public threads (`curves.CurveStats`).
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Iterable, Mapping
from typing import Any

from pydantic import ValidationError

from bazaar_agent.evals.model import Outcome
from bazaar_agent.learn.curves import CurveStats, known_class
from bazaar_agent.learn.model import TEXT_MAX, Learning

SUPPORT_FULL = 20  # threads of evidence at which a dealer pattern reaches its top confidence
_SLUG = re.compile(r"[^A-Za-z0-9_.:\-]+")
_TEAM = re.compile(r"[A-Za-z0-9_.:\-]{1,64}")


def clean(detail: Mapping[str, Any]) -> dict[str, Any]:
    """Detail as Postgres jsonb takes it: no None, and a NaN or an infinity (a duel's numeric column can hold
    one) dropped, so one odd number never makes the store refuse a whole batch of lessons."""
    out: dict[str, Any] = {}
    for k, v in detail.items():
        if v is None or (isinstance(v, float) and not math.isfinite(v)):
            continue
        if isinstance(v, list):
            v = [x for x in v if not (isinstance(x, float) and not math.isfinite(x))]
        out[k] = v
    return out


def slug(name: str) -> str:
    """A rival alias ("Rival Azul") as a learning subject ("rival_azul")."""
    return _SLUG.sub("_", name.strip()).strip("_").lower()[:64] or "unknown"


def _confidence(support: int, base: float = 0.5) -> float:
    return round(min(0.95, base + (0.95 - base) * min(support, SUPPORT_FULL) / SUPPORT_FULL), 3)


def _cap(text: str) -> str:
    return text if len(text) <= TEXT_MAX else text[: TEXT_MAX - 1] + "…"


def _ladder(prices: list[int]) -> str:
    if not prices:
        return "no bid"
    if len(prices) == 1:
        return f"one bid at {prices[0]}"
    steps = [b - a for a, b in zip(prices, prices[1:], strict=False)]
    return f"{prices[0]}→{prices[-1]} in {len(prices)} bids (step {max(steps)})"


# ---------------------------------------------------------------- dealer threads


def _dealer_advice(d: Mapping[str, Any], stats: CurveStats | None) -> str:
    """The one-sentence lesson for a dealer thread."""
    fill, ours = d.get("fill_price"), list(d.get("our_prices") or [])
    asks = list(d.get("dealer_prices") or [])
    dealer, cls = d.get("dealer"), d.get("price_class")
    lo = stats.fill_q(0.1) if stats else None
    hi = stats.fill_q(0.9) if stats else None
    if fill is not None and d.get("at_opening_price"):
        return f"Taking {dealer}'s opening ask voids the unlock credit: counter at least once first."
    if fill is not None:
        share = d.get("ladder_share")
        if share is not None and share >= 0.6:
            return f"This ladder worked for {dealer} {cls}: keep the start near {ours[0] if ours else fill}."
        floor = stats.floor if stats else None
        if floor is not None and fill > floor:
            return f"Paid {fill - floor} above the lowest {cls} fill ({floor}): open closer to it, step smaller."
        return f"Deal at {fill}: no cheaper {cls} fill is known."
    top = max(ours) if ours else None
    if not asks:
        if lo is None:
            return "No answer: open higher."
        return f"{dealer} never answered a bid of {top}: open at {math.ceil(lo)} or higher."
    if top is not None and stats and stats.fills and top < stats.fills[0]:
        return (
            f"Every {dealer} {cls} fill is {stats.fills[0]}-{stats.fills[-1]}, above our top bid {top}: "
            "skip this class until our cap allows it."
        )
    if hi is not None:
        return f"No deal although fills reach {hi:g}: reach the walk point sooner (bigger step, fewer rounds)."
    return f"No deal with {dealer}: not enough fills seen to set a walk point."


def dealer_lesson(o: Outcome, stats: CurveStats | None, us: str) -> Learning | None:
    d = dict(o.details)
    dealer, cls, item = d.get("dealer"), d.get("price_class"), d.get("item")
    if not isinstance(dealer, str) or not isinstance(item, str) or o.tick is None:
        return None
    if known_class(item) is None:  # a topic we cannot read as a card or a known pack: never quoted
        return None
    ours, asks = list(d.get("our_prices") or []), list(d.get("dealer_prices") or [])
    fill = d.get("fill_price")
    floor = stats.floor if stats else None
    p50 = stats.fill_q(0.5) if stats else None
    result = f"deal at {fill}" if fill is not None else "no deal"
    share = d.get("ladder_share")
    captured = f", captured {share:.0%} of the range" if isinstance(share, int | float) and fill is not None else ""
    situation = f"{dealer} {cls or '?'} {item} (thread {d.get('thread')})"
    asks_text = f"{asks[0]}→{asks[-1]}" if asks else "no ask"
    market = f"; market fills {stats.fills[0]}-{stats.fills[-1]}" if stats and stats.fills else ""
    text = (
        f"{situation}: we bid {_ladder(ours)} vs asks {asks_text}; {result}{captured}{market}. "
        f"{_dealer_advice(d, stats)}"
    )
    detail: dict[str, Any] = {
        "outcome": o.subject,
        "mechanic": "dealer",
        "target": "dealer",
        "item": item,
        "price_class": cls,
        "thread": d.get("thread"),
        "label": o.label,
        "score": o.score,
        "start": ours[0] if ours else None,
        "max_bid": max(ours) if ours else None,
        "bids": len(ours),
        "opening_ask": asks[0] if asks else None,
        "last_ask": asks[-1] if asks else None,
        "fill": fill,
        "at_opening_price": bool(d.get("at_opening_price")),
        "share": share,
        "market_floor": floor,
        "market_p50": p50,
        "delta_vs_floor": (fill - floor) if fill is not None and floor is not None else None,
        "delta_vs_p50": round(fill - p50, 1) if fill is not None and p50 is not None else None,
    }
    return Learning(
        subject_kind="dealer",
        subject=dealer,
        kind="lesson",
        tick=max(0, int(o.tick)),
        team=us,
        confidence=_confidence(stats.threads if stats else 0),
        text=_cap(text),
        source="outcome",
        detail=clean(detail),
    )


# ---------------------------------------------------------------- duels


def _duel_advice(d: Mapping[str, Any], score: float | None) -> str:
    best, limit, status = d.get("rival_best"), d.get("limit"), d.get("status")
    if status == "no_deal" and (d.get("missed_surplus") or 0) > 0:
        if not d.get("played"):
            return f"The rival's {best:g} was inside our limit and we never answered: answer every live duel."
        return f"The rival's {best:g} was inside our limit {limit:g}: accept inside the limit before time runs out."
    if status == "no_deal":
        return "Walking cost nothing: the rival never came inside our limit."
    kept = d.get("decay_kept")
    if isinstance(kept, int | float) and kept < 0.6:
        return f"Decay kept only {kept:.0%} after {d.get('rounds')} rounds: settle in fewer rounds."
    if score is not None and score < 0.3:
        return "A deal, but a thin share of the pie: anchor further from our limit."
    return "This pace worked: keep the anchor and the concession size."


def duel_lesson(o: Outcome, rival: str | None, us: str) -> Learning | None:
    d = dict(o.details)
    role, limit = d.get("role"), d.get("limit")
    if role not in ("seller", "buyer") or not isinstance(limit, int | float) or o.tick is None:
        return None
    who = slug(rival) if rival else "an unknown rival"  # the alias as a slug only: never raw text in a lesson
    best = d.get("rival_best")
    status = d.get("status")
    if status == "deal":
        result = f"deal after {d.get('rounds')} rounds, kept {o.surplus or 0:g} P"
    else:
        result = "no deal" + (f", rival best {best:g}" if isinstance(best, int | float) else ", no rival offer")
    text = f"Duel {d.get('duel')} as {role} (limit {limit:g}) vs {who}: {result}. {_duel_advice(d, o.score)}"
    detail = {
        "outcome": o.subject,
        "mechanic": "duel",
        "target": "duel",
        "item": d.get("item"),
        "role": role,
        "limit": limit,
        "status": status,
        "rounds": d.get("rounds"),
        "decay_kept": d.get("decay_kept"),
        "rival_best": best,
        "missed_surplus": d.get("missed_surplus"),
        "played": d.get("played"),
        "label": o.label,
        "score": o.score,
        "surplus": o.surplus,
        "issues": list(d.get("issues") or []),
    }
    return Learning(
        subject_kind="rival",
        subject=who if rival else "duels",
        kind="lesson",
        tick=max(0, int(o.tick)),
        team=us,
        confidence=0.7,
        text=_cap(text),
        source="outcome",
        detail=clean(detail),
    )


# ---------------------------------------------------------------- team trades


def trade_lesson(o: Outcome, us: str) -> Learning | None:
    d = dict(o.details)
    if o.tick is None:
        return None
    counterparty = d.get("counterparty")
    side, ref, price = d.get("side"), d.get("ref"), d.get("price")
    gained = d.get("surplus")
    if isinstance(gained, int | float):
        advice = "Good trade: this counterparty pays for it." if gained > 0 else "A loss: never trade below our value."
        result = f"{gained:+g} P at our values"
    else:
        advice, result = "Not valued yet.", "value unknown"
    who = counterparty if isinstance(counterparty, str) and _TEAM.fullmatch(counterparty) else None
    verb = "bought" if side == "buy" else "sold"
    text = f"Trade {o.subject}: we {verb} {ref} at {price} with {who or 'a team'}: {result}. {advice}"
    detail = {
        "outcome": o.subject,
        "mechanic": "trade",
        "target": "trade",
        "item": ref,
        "side": side,
        "price": price,
        "venue": d.get("venue"),
        "label": o.label,
        "score": o.score,
        "surplus": gained,
    }
    return Learning(
        subject_kind="team",
        subject=who or "teams",
        kind="lesson",
        tick=max(0, int(o.tick)),
        team=us,
        confidence=0.6,
        text=_cap(text),
        source="outcome",
        detail=clean(detail),
    )


# ---------------------------------------------------------------- dealer patterns (every team's threads)


def behaviour_learning(stats: CurveStats, us: str, tick: int) -> Learning:
    return Learning(
        subject_kind="dealer",
        subject=stats.dealer,
        kind="behaviour",
        tick=max(0, tick),
        team=us,
        evidence=(),
        confidence=_confidence(stats.threads),
        text=_cap(stats.describe()),
        source="outcome",
        detail=clean(
            {
                "mechanic": "dealer",
                "pattern": "concession",
                "price_class": stats.price_class,
                "threads": stats.threads,
                "fills": len(stats.fills),
                "floor": stats.floor,
                "fill_p25": stats.fill_q(0.25),
                "fill_p50": stats.fill_q(0.5),
                "fill_p75": stats.fill_q(0.75),
                "opening": stats.opening,
                "patience": stats.patience,
                "concession": stats.concession,
                "finals": stats.finals,
                "evidence_threads": list(stats.thread_ids[-20:]),
            }
        ),
    )


# ---------------------------------------------------------------- any strategy's own outcome (N14 writes back)

MECHANICS = ("dealer", "duel", "trade", "pack", "market", "venue", "page", "grant")
LESSON_TEXT = re.compile(r"[\w .,;:()%+\-→/'·]{1,300}")


def record_lesson(
    *,
    mechanic: str,
    subject_kind: str,
    subject: str,
    outcome: str,
    tick: int,
    team: str,
    text: str,
    features: Mapping[str, Any] | None = None,
    confidence: float = 0.6,
) -> Learning:
    """A lesson any strategy writes back after its own outcome (then `store.record([...])`).

    `outcome` is its identity ("pack:42", "venue:v04:fee"): the same outcome twice is one row. `features` are
    the situation's numbers (item, price_class, price, ...), searchable and filterable with `Query.where`.
    The text must be ours, built from structure: never a counterparty's words."""
    if mechanic not in MECHANICS:
        raise ValueError(f"unknown mechanic {mechanic!r} (one of {', '.join(MECHANICS)})")
    if not LESSON_TEXT.fullmatch(text):  # quoted to Jev and the words model as our own: plain words and numbers only
        raise ValueError("a lesson's text must be plain words, numbers and punctuation (no tags, no quotes)")
    detail = {k: v for k, v in (features or {}).items() if isinstance(v, str | int | float | bool)}
    return Learning(
        subject_kind=subject_kind,  # type: ignore[arg-type]  # validated by the model
        subject=subject,
        kind="lesson",
        tick=max(0, tick),
        team=team,
        confidence=confidence,
        text=_cap(text),
        source="outcome",
        detail={**detail, "mechanic": mechanic, "outcome": outcome},
    )


# ---------------------------------------------------------------- all of it


def lessons_from(
    outcomes: Iterable[Outcome],
    curves: Mapping[tuple[str, str], CurveStats],
    rivals: Mapping[int, str],
    us: str,
    tick: int,
) -> list[Learning]:
    """Every lesson the outcomes and the dealers' curves teach, deduped by key (one per outcome)."""
    out: dict[str, Learning] = {}

    def one(build: Callable[[], Learning | None]) -> None:
        try:
            learned = build()
        except (ValidationError, ValueError, TypeError, KeyError):  # one odd row (a new dealer id) is skipped
            return
        if learned is not None:
            out[learned.key()] = learned

    for o in outcomes:
        if o.target == "dealer":
            stats = curves.get((str(o.details.get("dealer")), str(o.details.get("price_class"))))
            one(lambda o=o, stats=stats: dealer_lesson(o, stats, us))  # type: ignore[misc]
        elif o.target == "duel":
            did = o.details.get("duel")
            rival = rivals.get(did) if isinstance(did, int) else None
            one(lambda o=o, rival=rival: duel_lesson(o, rival, us))  # type: ignore[misc]
        elif o.target == "trade":
            one(lambda o=o: trade_lesson(o, us))  # type: ignore[misc]
    for stats in curves.values():
        one(lambda stats=stats: behaviour_learning(stats, us, tick))  # type: ignore[misc]
    return sorted(out.values(), key=lambda lr: (lr.subject, lr.kind, lr.tick, lr.key()))
