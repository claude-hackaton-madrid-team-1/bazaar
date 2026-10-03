"""Our structured offers on a venue (El Rastro by default): list a card for cash, bid cash for a card.

Both are dry runs unless `live`, and both pass `guardrails.check()` first. A sell carries the copy's
`your_value` from `/api/me`, so it is never listed below what that copy is worth to us. Words persuade,
structure binds: the listing IS the structured offer (`give` / `want`) a counterparty accepts.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, replace
from typing import Any, Literal

from bazaar_agent.guardrails import Action, Context, Guardrails, LedgerStore, Verdict, check, is_pack

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
            key=lambda a: (not _has_value(a), float(a.get("your_value") or 0), -int(a["id"])),
        )
    if not found:
        raise OfferError(f"we hold no card {target!r} (check `uv run bazaar status`)")
    return found[0]


def _has_value(asset: dict[str, Any]) -> bool:
    return isinstance(asset.get("your_value"), int | float)


def sell_listing(me: dict[str, Any], target: str, price: int, venue: str = "rastro") -> Listing:
    """Fails closed: a copy without `your_value` in /api/me has no floor, so it is never listed."""
    _check_price(price)
    asset = find_copy(me, target)
    if not _has_value(asset):
        raise OfferError(f"asset {asset['id']} ({asset.get('ref')}) has no your_value in /api/me: not pricing it blind")
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
class Commitments:
    """What our open offers already promise: cash out, cards we bid for, assets we listed. `thread_cash` and
    `thread_packs` are the part of it in dealer threads: a thread bid is booked as spend only when its deal
    settles, so the spend and pack caps count it here (a board bid is booked in the ledger when posted)."""

    cash: int = 0
    wanted: tuple[str, ...] = ()
    listed: frozenset[int] = frozenset()
    thread_cash: int = 0
    thread_packs: int = 0
    listed_refs: tuple[str, ...] = ()  # the card of each asset our asks give (open, or accepted and settling)
    unnamed_listed: int = 0  # such assets whose offer row does not name the card


def offers_in(response: dict[str, Any]) -> list[dict[str, Any]]:
    """Every offer in a `GET /api/me/offers` body, whatever list it sits in."""
    return [o for rows in response.values() if isinstance(rows, list) for o in rows if isinstance(o, dict)]


def open_commitments(offers: Iterable[dict[str, Any]], us: str) -> Commitments:
    """Our open or queued offers. An offer counts as ours unless another team addressed it to us, so an
    unknown maker fails closed: its cash and cards are counted as committed. A thread settles at most one
    deal, so it counts once, at its biggest open bid (`one_per_thread`); every asset it lists stays listed.
    For the sell count only, an ask of ours a team accepted is gone too: it settles at the next tick, while
    /api/me still shows it."""
    offers = list(offers)
    ours = [
        o
        for o in offers
        if o.get("status") in (None, "open", "queued") and not (o.get("to") == us and o.get("maker") != us)
    ]
    cash, wanted, thread_cash, thread_packs = 0, [], 0, 0
    for o in one_per_thread(ours):
        give, want = o.get("give") or {}, o.get("want") or {}
        refs = [str(t).split(":")[-1] for t in (want.get("cards") or []) + (want.get("types") or [])]
        cash += int(give.get("cash") or 0)
        wanted += refs
        if o.get("thread") is not None and give.get("cash"):
            thread_cash += int(give["cash"])
            thread_packs += sum(1 for ref in refs if is_pack(ref))
    listed = {
        asset_id
        for o in ours
        for a in (o.get("give") or {}).get("assets") or []
        if isinstance(asset_id := a.get("id") if isinstance(a, dict) else a, int)
    }
    settling = [o for o in offers if o.get("status") == "accepted" and o.get("maker") == us]
    given = [a for o in ours + settling for a in (o.get("give") or {}).get("assets") or []]
    named = tuple(str(a["ref"]) for a in given if isinstance(a, dict) and a.get("ref"))
    return Commitments(
        cash, tuple(wanted), frozenset(listed), thread_cash, thread_packs, named, len(given) - len(named)
    )


def one_per_thread(offers: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every offer outside a thread, and one per thread: the one giving the most cash (the newest on a tie).
    Each bid in a dealer thread is a new offer; if the old ones still read `open`, counting them all would
    deny real moves (a bid of 8 after one of 7 is 8 at risk, not 15)."""

    def rank(o: dict[str, Any]) -> tuple[int, int]:
        oid = o.get("id")
        return int((o.get("give") or {}).get("cash") or 0), oid if isinstance(oid, int) else -1

    out: list[dict[str, Any]] = []
    best: dict[Any, dict[str, Any]] = {}
    for o in offers:
        tid = o.get("thread")
        if tid is None:
            out.append(o)
        elif tid not in best or rank(o) > rank(best[tid]):
            best[tid] = o
    return out + list(best.values())


def committed_context(ctx: Context, commitments: Commitments) -> Context:
    """The guardrail context as if every open offer fills: less cash, the cards we bid for held, and our
    dealer-thread bids spent this game hour. Two open bids cannot both pass the cash floor or the spend
    cap, and a second bid for the same card is refused.
    For a sell, the copies we could still sell: what we hold now (a bid may never fill) minus the copies
    already in our asks, so two asks cannot take a new page's last card (`protect_page_sets`). An ask that
    does not name its card counts against every card (fail closed)."""
    held = dict(ctx.held)
    for ref in commitments.wanted:
        held[ref] = held.get(ref, 0) + 1
    listed = Counter(commitments.listed_refs)
    sellable = {ref: n - listed[ref] - commitments.unnamed_listed for ref, n in ctx.held.items()}
    return replace(
        ctx,
        cash=ctx.cash - commitments.cash,
        held=held,
        spent_last_hour=ctx.spent_last_hour + commitments.thread_cash,
        packs_last_hour=ctx.packs_last_hour + commitments.thread_packs,
        sellable=sellable,
    )


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
    ledger: LedgerStore | None = None,
    commitments: Commitments | None = None,
) -> Posted:
    """Check the listing against the guardrails, then post it only when `live`.

    Pass our open offers as `commitments`: the check then sees the cash and cards they already promise,
    and an asset already listed is not listed twice. A posted bid is recorded as spend in the shared
    ledger at once: it can fill on any later tick, so max_spend_per_game_hour counts the commitment.
    """
    commitments = commitments or Commitments()
    if listing.asset_id is not None and listing.asset_id in commitments.listed:
        verdict = Verdict(False, (f"asset {listing.asset_id} is already in one of our open offers",))
    else:
        verdict = check(listing.action(), committed_context(ctx, commitments), rules)
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
