"""Our structured offers on a venue (El Rastro by default): list a card for cash, bid cash for a card.

Both are dry runs unless `live`, and both pass `guardrails.check()` first. A sell carries the copy's
`your_value` from `/api/me`, so it is never listed below what that copy is worth to us. Words persuade,
structure binds: the listing IS the structured offer (`give` / `want`) a counterparty accepts.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace
from typing import Any, Literal

from bazaar_agent.guardrails import (
    ANY_TEAM,
    Action,
    Context,
    Guardrails,
    LedgerStore,
    TradeBook,
    Verdict,
    check,
    is_pack,
)
from bazaar_agent.intel import TEAM_ID

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
    to: str | None = None  # addressed to one team (only it may accept); None: anyone on the venue

    def action(self) -> Action:
        return Action(
            self.kind, self.ref, self.rarity, self.price, your_value=self.your_value, counterparty=self.to or ANY_TEAM
        )

    def describe(self) -> str:
        what = f"asset {self.asset_id} ({self.ref})" if self.asset_id is not None else f"any {self.ref}"
        where = f"{self.venue}{f' to {self.to}' if self.to else ''}"
        if self.kind == "sell":
            return f"sell {what} for {self.price} P on {where} (your_value {self.your_value:g})"
        return f"bid {self.price} P for {what} on {where}"


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


def sell_listing(me: dict[str, Any], target: str, price: int, venue: str = "rastro", to: str | None = None) -> Listing:
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
        to=to,
    )


def bid_listing(ref: str, rarity: str | None, price: int, venue: str = "rastro", to: str | None = None) -> Listing:
    _check_price(price)
    return Listing("bid", ref, rarity, price, venue, give={"cash": price}, want={"cards": [ref]}, to=to)


@dataclass(frozen=True)
class Swap:
    """Our copy (plus cash, when we add some) for any copy of a card (plus cash, when they add some),
    addressed to one team: the structured offer of a direct swap proposal, posted on a venue's board."""

    asset_id: int
    give_ref: str
    give_rarity: str | None
    your_value: float
    want_ref: str
    want_rarity: str | None
    worth: float  # what the wanted card is worth to us (`strategy.buy_case`)
    venue: str
    to: str
    give_cash: int = 0
    want_cash: int = 0
    notional: int = 0  # the larger of the cash and the book of both cards (`intel.settled_volume`)

    @property
    def give(self) -> dict[str, Any]:
        return {"assets": [self.asset_id], **({"cash": self.give_cash} if self.give_cash else {})}

    @property
    def want(self) -> dict[str, Any]:
        return {"cards": [self.want_ref], **({"cash": self.want_cash} if self.want_cash else {})}

    def actions(self) -> list[Action]:
        """What the guardrails check: the copy leaves at the value we receive (the wanted card's worth to us
        plus their cash: never below `your_value × sell_min_value_ratio`), and the wanted card is a bid of
        the cash we add, zero or more (`block_buying_held_cards`: never a card we hold or already want; its
        price cap, the cash floor, the spend cap). Both count the swap's notional toward `to`'s share."""
        received = round(self.worth) + self.want_cash - self.give_cash
        out = [
            Action(
                "sell",
                self.give_ref,
                self.give_rarity,
                received,
                your_value=self.your_value,
                counterparty=self.to,
                volume=self.notional,
            ),
        ]
        out.append(
            Action("bid", self.want_ref, self.want_rarity, self.give_cash, counterparty=self.to, volume=self.notional)
        )
        return out

    def describe(self) -> str:
        give = f"asset {self.asset_id} ({self.give_ref})" + (f" + {self.give_cash} P" if self.give_cash else "")
        want = f"any {self.want_ref}" + (f" + {self.want_cash} P" if self.want_cash else "")
        return f"swap {give} for {want} on {self.venue} to {self.to}"


def post_swap(
    client: Any,
    swap: Swap,
    ctx: Context,
    rules: Guardrails,
    *,
    live: bool,
    expires_in_ticks: int = 40,
    ledger: LedgerStore | None = None,
    commitments: Commitments | None = None,
) -> Posted:
    """Check every action of the swap against the guardrails (with our open offers), then post it only when
    `live`, addressed to its team. Cash we add is recorded as spend at once, as for a bid."""
    commitments = commitments or Commitments()
    if swap.asset_id in commitments.listed:
        verdict = Verdict(False, (f"asset {swap.asset_id} is already in one of our open offers",))
    else:
        ctx = committed_context(ctx, commitments)
        problems = [v for a in swap.actions() for v in check(a, ctx, rules).violations]
        halted = bool(ctx.stops) if ctx.stops is not None else not rules.trading_enabled
        verdict = Verdict(not problems, tuple(dict.fromkeys(problems)), halted)
    if not verdict.allowed:
        return Posted(False, verdict, None, f"guardrails refuse to {swap.describe()}: {verdict}")
    if not live:
        return Posted(
            False,
            verdict,
            None,
            f"dry run: would {swap.describe()} · give {swap.give} want {swap.want} · "
            f"guardrails {verdict}. Add --live to post.",
        )
    offer = client.list_offer(swap.give, swap.want, venue=swap.venue, expires_in_ticks=expires_in_ticks, to=swap.to)
    if swap.give_cash and ledger is not None:
        ledger.record("spend", ctx.tick, ctx.t_hours, swap.give_cash, swap.want_ref)
    return Posted(True, verdict, offer, f"posted offer {offer.get('id')}: {swap.describe()}")


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


def offers_in(response: dict[str, Any]) -> list[dict[str, Any]]:
    """Every offer in a `GET /api/me/offers` body, whatever list it sits in."""
    return [o for rows in response.values() if isinstance(rows, list) for o in rows if isinstance(o, dict)]


def open_commitments(offers: Iterable[dict[str, Any]], us: str) -> Commitments:
    """Our open or queued offers. An offer counts as ours unless another team addressed it to us, so an
    unknown maker fails closed: its cash and cards are counted as committed. A thread settles at most one
    deal, so it counts once, at its biggest open bid (`one_per_thread`); every asset it lists stays listed."""
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
    return Commitments(cash, tuple(wanted), frozenset(listed), thread_cash, thread_packs)


def trade_book(
    offers: Iterable[dict[str, Any]], us: str, settled: dict[str, int], book: dict[str, float] | None = None
) -> TradeBook:
    """Our team-to-team volume for `max_counterparty_share`: `settled` (`intel.settled_volume`) plus the
    notional of our open offers another team could take (the larger of the cash and the book of the cards
    in it, as `settled_volume` counts a settlement), addressed to it or public (on a board, to anyone).
    Offers to a dealer are not team trades; an offer counts as ours unless another team addressed it to us
    (the rule of `open_commitments`: an unknown maker fails closed)."""
    addressed: dict[str, int] = {}
    public = 0
    for o in offers:
        if o.get("status") not in (None, "open", "queued") or (o.get("to") == us and o.get("maker") != us):
            continue
        give, want = o.get("give") or {}, o.get("want") or {}
        refs = [str(a.get("ref")) for a in give.get("assets") or [] if isinstance(a, dict)]
        refs += [str(t).split(":")[-1] for t in (want.get("cards") or []) + (want.get("types") or [])]
        cash = int(give.get("cash") or 0) + int(want.get("cash") or 0)
        notional = o.get("notional")  # this tick's accept: the price without the fee
        cash = (
            int(notional)
            if isinstance(notional, int)
            else max(cash, round(sum((book or {}).get(r, 0.0) for r in refs)))
        )
        to = o.get("to")
        if isinstance(to, str) and TEAM_ID.match(to):
            addressed[to] = addressed.get(to, 0) + cash
        elif to is None and o.get("thread") is None:
            public += cash
    return TradeBook(dict(settled), addressed, public)


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
    cap, and a second bid for the same card is refused."""
    held = dict(ctx.held)
    for ref in commitments.wanted:
        held[ref] = held.get(ref, 0) + 1
    return replace(
        ctx,
        cash=ctx.cash - commitments.cash,
        held=held,
        spent_last_hour=ctx.spent_last_hour + commitments.thread_cash,
        packs_last_hour=ctx.packs_last_hour + commitments.thread_packs,
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
    to = {"to": listing.to} if listing.to else {}
    offer = client.list_offer(listing.give, listing.want, venue=listing.venue, expires_in_ticks=expires_in_ticks, **to)
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
