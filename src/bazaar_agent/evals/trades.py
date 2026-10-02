"""Team-trade outcomes: the value we gained at our private values (RULES.md "Scoring").

A trade with another team settles on a venue. Its value to us comes from our own `/me` snapshots:
a card we bought is valued at its `your_value` in the first snapshot that holds it, a card we sold
at its `your_value` in the last snapshot before the sale. The cash side is the snapshots' cash change
when no other deal of ours settled in between (it shows who paid the venue fee), else the price,
with the fee charged to the buyer as the taker prices it ("ask + fee").
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from bazaar_agent.evals.model import JevCheck, Outcome, clamp01, label_for

JEV_OFFER_QUESTION = "offer_is_worth_accepting"  # the only question the taker asks (cli._offer_jev)


@dataclass(frozen=True)
class Settlement:
    """One `tape` row where our team is a party and no dealer is."""

    settlement: int
    tick: int
    venue: str | None
    buyer: str
    seller: str
    ref: str
    asset_ids: tuple[int, ...]
    price: int
    fee: int


@dataclass(frozen=True)
class Valuation:
    """What the snapshots around the settlement say."""

    card_value: float | None  # your_value of the traded card (after a buy, before a sale)
    cash_change: int | None  # our cash after minus before, when only this deal moved it
    before_tick: int | None = None
    after_tick: int | None = None


def jev_check(jev: Mapping[str, Any] | None, gained: float) -> JevCheck | None:
    """Whether a decided `offer_is_worth_accepting` verdict was right: yes pays when we gained."""
    if not jev or not isinstance(jev.get("verdict"), str):
        return None
    verdict = str(jev["verdict"])
    question = str(jev.get("question") or JEV_OFFER_QUESTION)
    right = None if verdict not in ("yes", "no") else (verdict == "yes") == (gained > 0)
    return JevCheck(question, verdict, right)


def score_trade(
    s: Settlement,
    ours: str,
    valuation: Valuation,
    *,
    decision_id: int | None = None,
    jev: Mapping[str, Any] | None = None,
) -> Outcome:
    buying = s.buyer == ours
    if buying:
        cash = -valuation.cash_change if valuation.cash_change is not None else s.price + s.fee
    else:
        cash = valuation.cash_change if valuation.cash_change is not None else s.price
    details: dict[str, Any] = {
        "settlement": s.settlement,
        "venue": s.venue,
        "side": "buy" if buying else "sell",
        "counterparty": s.seller if buying else s.buyer,
        "ref": s.ref,
        "asset_ids": list(s.asset_ids),
        "price": s.price,
        "fee": s.fee,
        "cash": cash,
        "cash_from": "snapshots" if valuation.cash_change is not None else "price",
        "card_value": valuation.card_value,
        "snapshots": [valuation.before_tick, valuation.after_tick],
        "decision_id": decision_id,
    }
    base = Outcome("trade", f"settlement:{s.settlement}", None, "ok", "", s.tick, decision_id=decision_id)
    if valuation.card_value is None:
        text = f"Trade of {s.ref} at {s.price}: no /me snapshot holds the card around tick {s.tick}, so no value yet."
        return _done(base, None, text, None, details, None)
    value = valuation.card_value
    gained = value - cash if buying else cash - value
    gross = value if buying else cash
    score = clamp01(gained / gross) if gross > 0 else 0.0
    verb = f"Bought {s.ref} for {cash}" if buying else f"Sold {s.ref} for {cash}"
    text = f"{verb} (worth {value:g} to us): {gained:+.1f} P at our private values."
    if gained < 0:
        text += " A loss: below our value."
    return _done(base, score, text, gained, details, jev_check(jev, gained))


def _done(
    base: Outcome,
    score: float | None,
    text: str,
    gained: float | None,
    details: Mapping[str, Any],
    jev: JevCheck | None,
) -> Outcome:
    return Outcome(
        base.target,
        base.subject,
        None if score is None else round(score, 4),
        "ok" if score is None else ("bad" if (gained or 0) < 0 else label_for(score)),
        text,
        base.tick,
        surplus=None if gained is None else round(gained, 2),
        decision_id=base.decision_id,
        jev=jev,
        details={**details, "surplus": None if gained is None else round(gained, 2)},
    )
