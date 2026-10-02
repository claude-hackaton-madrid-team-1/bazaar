"""A negotiation thread as people read it: every message with its sender, text and structured price.

`GET /api/threads/{id}` and `GET /api/me/threads` are validated here. Their shape is unverified in
`docs/api/openapi.json`, so unknown fields are kept, never rejected. The CLI tables, the telemetry
spans and the UI team's JSON all read the same `Line`s, so a conversation looks the same everywhere.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class OfferSide(BaseModel):
    model_config = ConfigDict(extra="allow")
    cash: int | None = None
    assets: list[Any] = Field(default_factory=list)
    types: list[str] = Field(default_factory=list)


class Offer(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: int
    maker: str | None = None
    status: str | None = None
    give: OfferSide = Field(default_factory=OfferSide)
    want: OfferSide = Field(default_factory=OfferSide)
    final: bool = False
    expires_tick: int | None = None

    @property
    def price(self) -> int | None:
        """The cash in the offer, whichever side carries it (the rule `latest_dealer_offer` uses)."""
        return self.want.cash or self.give.cash or None


class Message(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: int | None = None
    tick: int | None = None
    sender: str = "?"
    text: str | None = None
    offer: Offer | None = None


class Thread(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)
    id: int
    kind: str | None = None
    with_: str | None = Field(default=None, alias="with")
    team: str | None = None
    topic: dict[str, Any] | None = None
    item: str | None = None
    status: str = "open"
    closed_reason: str | None = None
    created_tick: int | None = None
    messages: list[Message] = Field(default_factory=list)
    standing_offers: list[Offer] = Field(default_factory=list)


@dataclass(frozen=True)
class Line:
    """One message as a person reads it."""

    id: int | None
    tick: int | None
    sender: str
    text: str
    price: int | None
    offer_id: int | None
    offer_status: str | None
    final: bool

    @property
    def key(self) -> tuple[object, ...]:
        """Identity across re-reads of the same thread: the message id, or its content when absent."""
        return (self.id,) if self.id is not None else (self.tick, self.sender, self.text)

    def transcript(self) -> str:
        price = f" [{self.price}{' FINAL' if self.final else ''}]" if self.price is not None else ""
        return f"t{self.tick if self.tick is not None else '?'} {self.sender}: {self.text}{price}"


def lines(thread: Thread) -> list[Line]:
    """Every message in order, with the structured price it carried."""
    return [
        Line(
            id=m.id,
            tick=m.tick,
            sender=m.sender,
            text=m.text or "",
            price=m.offer.price if m.offer else None,
            offer_id=m.offer.id if m.offer else None,
            offer_status=m.offer.status if m.offer else None,
            final=bool(m.offer and m.offer.final),
        )
        for m in thread.messages
    ]


def topic_ref(topic: dict[str, Any] | None) -> str | None:
    """'LAV-06' or 'sobre_barrio' from a buy or sell topic; None when there is none."""
    for side in ("buy", "sell"):
        spec = (topic or {}).get(side)
        if isinstance(spec, dict) and (spec.get("card") or spec.get("pack")):
            return str(spec.get("card") or spec.get("pack"))
    return None


def header(thread: Thread) -> dict[str, Any]:
    return {
        "id": thread.id,
        "kind": thread.kind,
        "with": thread.with_,
        "team": thread.team,
        "topic": thread.topic,
        "ref": topic_ref(thread.topic),
        "item": thread.item,
        "status": thread.status,
        "closed_reason": thread.closed_reason,
        "created_tick": thread.created_tick,
    }


def conversation_json(thread: Thread) -> dict[str, Any]:
    """The stable shape the UI team reads (`bazaar thread <id> --json`)."""
    return {
        "thread": header(thread),
        "messages": [asdict(line) for line in lines(thread)],
        "standing_offers": [
            {"id": o.id, "maker": o.maker, "status": o.status, "price": o.price, "final": o.final}
            for o in thread.standing_offers
        ],
    }


def summary_json(thread: Thread) -> dict[str, Any]:
    """One row of `bazaar threads --json`: the header plus the last message."""
    last = lines(thread)[-1] if thread.messages else None
    return {
        **header(thread),
        "messages": len(thread.messages),
        "last": asdict(last) if last else None,
    }
