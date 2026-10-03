"""The accept gate (S1): the last check before ANY accept leaves us, from a dealer thread, a board or a duel.

Words persuade, structure binds (RULES.md). An accept binds a structured offer: a dealer's or a board's by
id, a duel rival's standing one. The gate reads that structure again and refuses the accept when it is not
what our decision priced: another item, a lesser rarity, our assets in `want`, another price or other days.
The counterparty's words are compared with the structure too (a dealer's through the offer inspector, a duel
rival's price claims), as evidence for the decision row and for a flag. Words never approve a structure: a
clean structure with lying words is still the deal we priced, and a bad structure is refused whatever the
words say. Pure functions, no network: the callers pass what they read. GUARDRAILS.md `inspect_accepts`
(true) is the kill flag the callers check.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from bazaar_agent.agents.duelist import DuelMove, duel_done, duel_id, effective_price, inside_limit, rival_text
from bazaar_agent.agents.inspector import CardIndex, Verdict, inspect_offer, message_for_offer
from bazaar_agent.agents.market import BoardOffer
from bazaar_agent.swaps import TheirOffer, is_the_planned_swap, read_offer
from bazaar_agent.trade_desk import Trade

GateKind = Literal["dealer", "board", "duel", "team"]
# A price the words claim: "80 P", "80p", "80 primas", "80 €" (a bare number is too ambiguous to read as one).
PRICE_CLAIM = re.compile(r"(?<![\w.])(\d{1,7})\s*(?:p|primas?|€)(?!\w)", re.IGNORECASE)


@dataclass(frozen=True)
class Gate:
    """One accept's verdict: `clean` goes ahead; `block` and `flag` are refused (`flag` is also bad faith)."""

    kind: GateKind
    offer_id: int | None
    verdict: Verdict
    findings: tuple[str, ...] = ()
    words: str | None = None  # what the words claim against the structure: evidence, never a reason to accept
    message_id: int | None = None

    @property
    def allowed(self) -> bool:
        return self.verdict == "clean"

    @property
    def reason(self) -> str:
        return "; ".join(self.findings) or "the structure is what we priced"

    def as_inputs(self) -> dict[str, Any]:
        """For the decision row's `inputs`: what the inspector saw (no private numbers beyond the row's own)."""
        return {
            "kind": self.kind,
            "offer_id": self.offer_id,
            "message_id": self.message_id,
            "verdict": self.verdict,
            "findings": list(self.findings),
            "words": self.words,
        }


def _block(kind: GateKind, offer_id: int | None, why: str) -> Gate:
    return Gate(kind, offer_id, "block", (why,))


def _standing(thread: Mapping[str, Any], dealer: str, offer_id: int) -> Mapping[str, Any] | None:
    for o in thread.get("standing_offers") or []:
        if isinstance(o, dict) and o.get("id") == offer_id and o.get("maker") == dealer and o.get("status") == "open":
            return o
    return None


def dealer_gate(
    thread: Mapping[str, Any],
    dealer: str,
    offer_id: int | None,
    price: int | None,
    topic: Mapping[str, Any],
    cards: CardIndex,
) -> Gate:
    """A dealer's standing offer `offer_id`, about to be accepted at `price`: the offer inspector's verdict
    (structure against the thread's topic, then the words against the structure), and the price it binds."""
    if offer_id is None:
        return _block("dealer", None, "no offer id to accept")
    offer = _standing(thread, dealer, offer_id)
    if offer is None:
        return _block("dealer", offer_id, f"offer {offer_id} is not {dealer}'s standing offer in this thread")
    message_id, text = message_for_offer(thread, offer_id)
    inspection = inspect_offer(offer, topic, text, cards, dealer=dealer, message_id=message_id)
    findings = list(inspection.findings)
    selling = isinstance(topic.get("sell"), Mapping)  # a sale: the dealer's bid is the cash it gives
    asks = (offer.get("give" if selling else "want") or {}).get("cash")
    if price is not None and asks != price:
        findings.append(f"the structure {'bids' if selling else 'asks'} {asks} P, our decision priced {price} P")
    verdict: Verdict = inspection.verdict
    if verdict == "clean" and findings:
        verdict = "block"
    return Gate("dealer", offer_id, verdict, tuple(findings), None, message_id)


def board_gate(offer: BoardOffer, ref: str, total: int, fee: int, catalog_rarity: str | None) -> Gate:
    """A board ask about to be accepted: one copy of `ref` for cash, at the price we priced, and the copy's
    own rarity is the catalog's for that card (a lesser copy under a better name is refused)."""
    findings = []
    if offer.side != "ask":
        findings.append(f"offer {offer.id} is a {offer.side}, not an ask")
    if offer.ref != ref:
        findings.append(f"it binds {offer.ref}, our decision priced {ref}")
    if offer.price + fee != total:
        findings.append(f"it asks {offer.price} + fee {fee}, our decision priced {total} in all")
    if offer.rarity and catalog_rarity and offer.rarity != catalog_rarity:
        findings.append(f"the copy says {offer.rarity}; the catalog has {ref} as {catalog_rarity}")
    return Gate("board", offer.id, "block" if findings else "clean", tuple(findings))


def bid_gate(offer: BoardOffer, ref: str, price: int, copy: Mapping[str, Any] | None) -> Gate:
    """A board bid about to be accepted with one of our copies (`accept_bids`): cash for any copy of `ref`, at
    the price we priced, and the copy we hand over (from /me) is a card of that ref."""
    findings = []
    if offer.side != "bid":
        findings.append(f"offer {offer.id} is an {offer.side}, not a bid")
    if offer.ref != ref:
        findings.append(f"it wants {offer.ref}, our decision priced {ref}")
    if offer.price != price:
        findings.append(f"it pays {offer.price}, our decision priced {price}")
    if offer.asset_id is not None:
        findings.append(f"it binds asset {offer.asset_id}, not any copy of {offer.ref}")
    if copy is None:
        findings.append("the copy we would hand over is not in /me")
    elif copy.get("kind", "card") != "card" or copy.get("ref") != offer.ref:
        findings.append(f"the copy we would hand over is {copy.get('ref')}, not {offer.ref}")
    return Gate("board", offer.id, "block" if findings else "clean", tuple(findings))


def swap_gate(
    thread: Mapping[str, Any], us: str, priced: TheirOffer, trade: Trade, copy: Mapping[str, Any] | None
) -> Gate:
    """A team's offer in a swap thread (N17) about to be accepted: read from the thread again, it is still an
    open plain offer from that team to us, with the structure we priced (the planned cards, the same cash
    either way), and the copy we hand over is our copy of the planned card in /me."""
    oid = priced.offer_id
    raw = next((o for o in thread.get("standing_offers") or [] if isinstance(o, dict) and o.get("id") == oid), None)
    if raw is None:
        return _block("team", oid, f"offer {oid} is not a standing offer in this thread")
    fresh = read_offer(raw, us)
    if fresh is None:
        return _block("team", oid, f"offer {oid} is no longer a plain open offer to us")
    findings = []
    if fresh.team != priced.team:
        findings.append(f"it is {fresh.team}'s offer, our decision priced {priced.team}'s")
    if (fresh.cash_in, fresh.cash_out) != (priced.cash_in, priced.cash_out):
        findings.append(
            f"its cash is +{fresh.cash_in}/-{fresh.cash_out}, our decision priced +{priced.cash_in}/-{priced.cash_out}"
        )
    if fresh != priced or not is_the_planned_swap(fresh, trade):
        findings.append("it does not move the cards we priced")
    if copy is None:
        findings.append("the copy we would hand over is not in /me")
    elif copy.get("kind", "card") != "card" or copy.get("ref") != trade.refs[0]:
        findings.append(f"the copy we would hand over is {copy.get('ref')}, not {trade.refs[0]}")
    return Gate("team", oid, "block" if findings else "clean", tuple(dict.fromkeys(findings)))


def price_claims(text: str | None) -> list[int]:
    """Prices the words name ("80 P", "80 primas"), in order."""
    return [int(m) for m in PRICE_CLAIM.findall(text or "")]


def _days(duel: Mapping[str, Any]) -> Any:
    offer = duel.get("rival_offer")
    return offer.get("days") if isinstance(offer, dict) else None


def _worse(worth: float, than: float, role: str) -> bool:
    """`worth` is worse for us than `than`: a seller wants more, a buyer less."""
    return worth < than if role == "seller" else worth > than


def duel_gate(decided: Mapping[str, Any], fresh: Mapping[str, Any] | None, move: DuelMove) -> Gate:
    """Accepting a duel binds the rival's offer standing WHEN the accept lands. `decided` is the duel we
    priced the move on, `fresh` the same duel read again just before the accept (None: gone). An offer that
    moved against us (price or days) is refused; one that moved in our favour, still inside our limit, is
    accepted (refusing it could turn the last tick's deal into no deal)."""
    if fresh is None or duel_done(fresh):
        return _block("duel", None, "the duel is no longer live")
    offer = fresh.get("rival_offer")
    rival = offer if isinstance(offer, dict) else {}
    offer_id = rival.get("id") if isinstance(rival.get("id"), int) else None
    price = rival.get("price")
    if not isinstance(price, int | float) or isinstance(price, bool) or not math.isfinite(price):
        return _block("duel", offer_id, "the rival has no standing priced offer")
    limit, role = fresh.get("your_limit"), fresh.get("role")
    worth = effective_price(dict(fresh), int(price))
    priced = effective_price(dict(decided), move.price) if move.price is not None else None
    two_issue = "days" in (fresh.get("issues") or [])  # days bind only in a two-issue duel
    moved = int(price) != move.price or (two_issue and _days(fresh) != _days(decided))
    findings, notes = [], []
    if not isinstance(limit, int) or role not in ("seller", "buyer") or worth is None or priced is None:
        findings.append("our limit or the rival's days cannot be read")
    elif not inside_limit(round(worth), limit, str(role)):
        findings.append(f"{int(price)} is not inside our limit")
    elif moved and _worse(worth, priced, str(role)):
        findings.append(
            f"the rival's offer moved against us: we priced {move.price} (days {_days(decided)}), "
            f"it is {int(price)} (days {_days(fresh)}) now"
        )
    elif moved:
        notes.append(f"the rival's offer moved in our favour: {move.price} → {int(price)}, days {_days(fresh)}")
    claims = price_claims(rival_text(dict(fresh)))
    words = None
    if claims and int(price) not in claims:
        words = f"the words name {', '.join(map(str, claims))} P; the structure binds {int(price)}"
    if findings:
        return Gate("duel", offer_id, "block", tuple(findings), words)
    return Gate("duel", offer_id, "clean", tuple(notes), words)


def duel_accept_check(read_duels: Callable[[], Any], decided: Mapping[str, Any], did: int, move: DuelMove) -> Gate:
    """Read our live duels once more (`GET /api/duels`) and gate the accept on that read. A failed read
    fails closed: no accept this tick (the caller re-decides on the next one)."""
    try:
        payload = read_duels()
    except Exception as e:  # a refused or broken re-read never lets an unchecked accept through
        return _block("duel", None, f"the duel could not be read again ({type(e).__name__})")
    duels = payload.get("duels") if isinstance(payload, Mapping) else None
    fresh = next((d for d in duels or [] if isinstance(d, dict) and duel_id(d) == did), None)
    try:
        return duel_gate(decided, fresh, move)
    except (ArithmeticError, ValueError, TypeError) as e:  # a non-finite days weight, a malformed shape
        return _block("duel", None, f"the duel could not be judged ({type(e).__name__})")


@dataclass
class DuelRereads:
    """A fresh `GET /api/duels` before EACH duel accept (a rival can move between two accepts of one tick).
    A failed re-read fails every later accept of ITS tick only: no retry burst on the key every process shares."""

    read: Callable[[], Any]
    failed: dict[int, Exception] = field(default_factory=dict)

    def new_tick(self) -> None:
        self.failed.clear()  # a reset world may repeat a tick number: a failure never outlives its tick

    def for_tick(self, tick: int) -> Callable[[], Any]:
        def read() -> Any:
            if tick in self.failed:
                raise self.failed[tick]
            try:
                return self.read()
            except Exception as e:
                self.failed = {tick: e}
                raise

        return read
