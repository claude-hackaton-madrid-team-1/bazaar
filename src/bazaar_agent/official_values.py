"""The official value cap on every buy (organisers' Day-2 hint 1: "Know your value before you buy").

`GET /api/me/value?card=<ref>` answers `{card, your_value}`: our private value of ONE MORE copy (book ×
set affinity × copy marginal), the value the scorer counts trades at. A buy whose price (fee included)
is above it costs points, whatever our own model says the card is worth (our model adds a page-bonus
share and often lands higher). Our model still ranks; this only caps, inside `guardrails.check()`.

Reads are made only for a card about to be bought (`check()` asks last, once every other rule passed),
at most once per (card, tick, copies held): a deal that changes our copies reads it again, a new tick
reads it again. A failed or malformed read is cached for that key too and refuses the buy (fail closed),
so a broken route never spends more of the key's shared 5 req/s than one read per card per tick.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

log = logging.getLogger(__name__)

ValueRead = Callable[[str], Any]  # the SDK's `Bazaar.value(card)`: GET /api/me/value?card=<ref>


class _Margin(Protocol):
    @property
    def official_value_margin(self) -> float: ...


class CardValue(BaseModel):
    """The `/api/me/value` answer, validated at the boundary: a finite, non-negative value for the card asked."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    card: str
    your_value: float

    @field_validator("your_value", mode="before")
    @classmethod
    def _finite(cls, value: Any) -> Any:
        if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value) or value < 0:
            raise ValueError("your_value must be a finite number >= 0")
        return value


class OfficialValues:
    """One process's reads of `/api/me/value`, cached per (card, tick, copies held). `reads` counts the
    requests sent (the shared key's budget); `failures` the reads that refused a buy."""

    def __init__(self, read: ValueRead) -> None:
        self._read = read
        self._cache: dict[tuple[str, int, int], float | None] = {}
        self.reads = 0
        self.failures = 0
        self._down_tick: int | None = None  # the route failed this tick (network, 429, 5xx): no other card is read

    @classmethod
    def of(cls, client: Any) -> OfficialValues:
        """The value book of a team client (the SDK's `value(card)`)."""
        return cls(lambda card: client.value(card))  # looked up at read time: a client without it fails closed

    def cached(self, ref: str, tick: int, held: int) -> float | None:
        """The value already read this tick, or None: never sends a request."""
        return self._cache.get((ref, tick, held))

    def value(self, ref: str, tick: int, held: int) -> float | None:
        """Our official value of one more `ref` this tick, or None when it could not be read (refuse the buy)."""
        key = (ref, tick, held)
        if key in self._cache:
            return self._cache[key]
        if any(k[1] != tick for k in self._cache):  # a new tick: every card is read again
            self._cache = {k: v for k, v in self._cache.items() if k[1] == tick}
        if self._down_tick == tick:  # one outage per tick: refuse at once, never another GET into it
            self.failures += 1
            self._cache[key] = None
            return None
        self.reads += 1
        found = self._fetch(ref, tick)
        if found is None:
            self.failures += 1
        self._cache[key] = found
        return found

    def _fetch(self, ref: str, tick: int) -> float | None:
        try:
            answer = CardValue.model_validate(self._read(ref))
        except ValidationError:
            log.warning("official value of %s: malformed answer; the buy is refused", ref)
            return None
        except Exception as e:  # BazaarError (429, network, bad key) or anything else: fail closed
            if not _card_specific(e):  # the route itself is down: every later card of this tick is refused
                self._down_tick = tick
            log.warning(
                "official value of %s: read failed (%s); the buy is refused",
                ref,
                getattr(e, "code", None) or type(e).__name__,
            )
            return None
        if answer.card != ref:
            log.warning("official value of %s: the answer names %s; the buy is refused", ref, answer.card)
            return None
        return float(answer.your_value)


def _card_specific(e: Exception) -> bool:
    """A 4xx other than 429 is about the card asked (unknown card, bad query): other cards may still be read.
    A network error, a 429, a 5xx or anything else means the route is down for this tick."""
    status = getattr(e, "status", 0)
    return isinstance(status, int) and 400 <= status < 500 and status != 429


UNREAD = "(GET /api/me/value): buying it is not allowed"  # ends every not-read / could-not-be-read refusal


def unread_only(violations: tuple[str, ...]) -> bool:
    """Refused only because the official value, the human approvals or our sales (`no_buyback_ticks`) could not be
    read: hold for the tick, never walk on it."""
    from bazaar_agent.approvals import UNREAD as APPROVALS_UNREAD
    from bazaar_agent.move_impact import SALES_UNREAD

    return bool(violations) and all(v.endswith((UNREAD, APPROVALS_UNREAD, SALES_UNREAD)) for v in violations)


def over_cap(price: int, ref: str, values: OfficialValues, tick: int, held: int, margin: float) -> str | None:
    """Why a standing bid of ours is above the official value now (cancel it); None when it is not, or when the
    value cannot be read this tick (a standing bid is left as it is on an outage)."""
    official = values.value(ref, tick, held)
    if official is None or price <= official - margin + 1e-9:
        return None
    less = f" - margin {margin:g}" if margin else ""
    return f"bid {price} > official value {official:g}{less} of {ref} (GET /api/me/value)"


def cap_violations(
    ref: str,
    price: int,
    gives_value: float,
    values: OfficialValues | None,
    tick: int,
    held: int,
    rules: _Margin,
    margin: float | None = None,
    rule: str | None = None,
) -> list[str]:
    """A card buy's price (fee included) plus what else it gives (a swap's copy) must stay at or under the official
    value of one more copy minus `official_value_margin` (or `margin`, when given: an epic or legendary's
    `Guardrails.value_margin_for`; below 0 with `rule` "dealer_ladder_value_tolerance": that much over it). No value
    book, or an unreadable value: refused."""
    if values is None:
        return [f"official value of {ref} not read {UNREAD}"]
    official = values.value(ref, tick, held)
    if official is None:
        return [f"official value of {ref} could not be read {UNREAD}"]
    named = margin is not None and margin != rules.official_value_margin
    margin = rules.official_value_margin if margin is None else margin
    if price + gives_value <= official - margin + 1e-9:
        return []
    gives = f" + copy given {gives_value:g}" if gives_value else ""
    if rule is not None:  # the tolerance, net of official_value_margin
        less = f" + {rule} {-margin:g}" if margin < 0 else f" - official_value_margin net of {rule} {margin:g}"
    else:
        rule = "off_page_min_surplus" if named else "official_value_margin"
        less = f" - {rule} {margin:g}" if margin else ""
    return [f"price {price}{gives} > official value {official:g}{less} of {ref} (GET /api/me/value)"]
