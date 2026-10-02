"""Our structured offers on a venue (El Rastro by default): list a card for cash, bid cash for a card.

Both are dry runs unless `live`, and both pass `guardrails.check()` first. A sell carries the copy's
`your_value` from `/api/me`, so it is never listed below what that copy is worth to us. Words persuade,
structure binds: the listing IS the structured offer (`give` / `want`) a counterparty accepts.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from bazaar_agent.guardrails import Action, Context, Guardrails, Ledger, Verdict, check

MAX_PRICE = 10_000_000  # RULES.md: whole primas from 1 to 10,000,000


class OfferError(ValueError):
    """The requested offer cannot be built from what we hold (or the price is out of range)."""


@dataclass(frozen=True)
class Listing:
    kind: Literal["sell", "bid"]
    ref: str
    rarity: str | None
    price: int
    venue: str
    give: dict[str, Any]
    want: dict[str, Any]
    asset_id: int | None = None
    your_value: float | None = None

    def action(self) -> Action:
        return Action(self.kind, self.ref, self.rarity, self.price, self.your_value)

    def describe(self) -> str:
        what = f"asset {self.asset_id} ({self.ref})" if self.asset_id is not None else f"any {self.ref}"
        if self.kind == "sell":
            return f"sell {what} for {self.price} P on {self.venue} (your_value {self.your_value:g})"
        return f"bid {self.price} P for {what} on {self.venue}"


def _check_price(price: int) -> None:
    if not 1 <= price <= MAX_PRICE:
        raise OfferError(f"price {price} is outside 1..{MAX_PRICE}")


def find_copy(me: dict[str, Any], target: str) -> dict[str, Any]:
    """The card asset to sell: by asset id ("15"), or the copy of a ref ("LAT-09") we lose least by selling."""
    cards = [a for a in me.get("assets") or [] if a.get("kind") == "card" and isinstance(a.get("id"), int)]
    if target.isdigit():
        found = [a for a in cards if a["id"] == int(target)]
    else:
        found = sorted(
            (a for a in cards if str(a.get("ref")) == target),
            key=lambda a: (float(a.get("your_value") or 0), -int(a["id"])),
        )
    if not found:
        raise OfferError(f"we hold no card {target!r} (check `uv run bazaar status`)")
    return found[0]


def sell_listing(me: dict[str, Any], target: str, price: int, venue: str = "rastro") -> Listing:
    _check_price(price)
    asset = find_copy(me, target)
    return Listing(
        "sell",
        str(asset.get("ref")),
        asset.get("rarity"),
        price,
        venue,
        give={"assets": [int(asset["id"])]},
        want={"cash": price},
        asset_id=int(asset["id"]),
        your_value=float(asset.get("your_value") or 0),
    )


def bid_listing(ref: str, rarity: str | None, price: int, venue: str = "rastro") -> Listing:
    _check_price(price)
    return Listing("bid", ref, rarity, price, venue, give={"cash": price}, want={"cards": [ref]})


@dataclass(frozen=True)
class Posted:
    sent: bool
    verdict: Verdict
    offer: dict[str, Any] | None
    message: str


def post(
    client: Any,
    listing: Listing,
    ctx: Context,
    rules: Guardrails,
    *,
    live: bool,
    expires_in_ticks: int = 40,
    ledger: Ledger | None = None,
) -> Posted:
    """Check the listing against the guardrails, then post it only when `live`.

    A posted bid is recorded as spend in the shared ledger at once: it can fill on any later tick,
    so max_spend_per_game_hour counts the commitment, not the uncertain fill.
    """
    verdict = check(listing.action(), ctx, rules)
    if not verdict.allowed:
        return Posted(False, verdict, None, f"guardrails refuse to {listing.describe()}: {verdict}")
    if not live:
        return Posted(
            False,
            verdict,
            None,
            f"dry run: would {listing.describe()} · give {listing.give} want {listing.want} · guardrails "
            f"{verdict}. Add --live to post.",
        )
    offer = client.list_offer(listing.give, listing.want, venue=listing.venue, expires_in_ticks=expires_in_ticks)
    if listing.kind == "bid" and ledger is not None:
        ledger.record("spend", ctx.tick, ctx.t_hours, listing.price, listing.ref)
    return Posted(True, verdict, offer, f"posted offer {offer.get('id')}: {listing.describe()}")


def offer_side(side: dict[str, Any] | None) -> str:
    """'12 P', 'LAT-09#15', 'any LAV-09' — one side of a structured offer in a few words."""
    side = side or {}
    parts = [f"{side['cash']} P"] if side.get("cash") else []
    parts += [f"{a.get('ref')}#{a.get('id')}" if isinstance(a, dict) else f"#{a}" for a in side.get("assets") or []]
    parts += [f"any {str(t).split(':')[-1]}" for t in (side.get("cards") or []) + (side.get("types") or [])]
    return " + ".join(parts) or "-"
