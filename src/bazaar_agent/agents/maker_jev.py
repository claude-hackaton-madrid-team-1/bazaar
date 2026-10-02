"""Jev as the maker's decision model: three candidate prices, only the legal ones, Jev picks one.

Spec §3 step 4 and §7.1. For every offer the maker posts:
  1. `price_candidates`: the strategy's price and two others around it. An ask: `aggressive` is the
     strategy's ask (today's price), `quick_sale` the lowest ask that still clears `sell_min_surplus` over
     what selling costs us, `fair` halfway. A bid: `fair` is the strategy's bid at the tape (today's price),
     `quick_sale` the highest bid that keeps `min_buy_surplus` and the rarity's price cap, `aggressive` as
     far below the tape as `quick_sale` is above it.
  2. The maker keeps only the candidates its own checks allow (never an ask below the sell floor, never a
     bid that breaks the cash floor, the spend cap or the price cap), then Jev `list_price_choice`
     (questions/maker.json) picks one. `undecided`, no budget, or an illegal pick keep today's price.
  3. A stale offer (its target moved) asks `reprice_or_hold`, only when holding is still legal: a decided
     `no` holds it, anything else reprices as today.
The chosen label is remembered while the offer stands, so its price follows the market and the maker does
not reprice it back to today's price on the next tick. `OfferWatch` writes one outcome line per decided
verdict once its offer leaves our open offers: filled (right), expired unfilled (wrong) or withdrawn by us
(unknown). Dry runs post nothing, so they never produce an outcome.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

from bazaar_agent.agents.jev_journal import JevJournal
from bazaar_agent.agents.market import OpenOffer
from bazaar_agent.agents.runtime import JevAdvice, JevFn, no_jev
from bazaar_agent.guardrails import Guardrails
from bazaar_agent.strategy import StrategyParams

if TYPE_CHECKING:
    from bazaar_agent.agents.maker import Target

PRICE_QUESTION = "list_price_choice"
REPRICE_QUESTION = "reprice_or_hold"
LABELS = ("aggressive", "fair", "quick_sale")
CACHED_REASONS = (None, "below_threshold")  # an answer the model gave; a failure is asked again next tick
ROUNDING = 1e-9
CACHE_MAX = 2000  # answers kept across ticks; cleared when full (a weekend of listings stays small)

OfferKey = tuple[str, object]


@dataclass(frozen=True)
class MakerJevConfig:
    min_budget_s: float = 4.0  # ask Jev only with this much of the tick left (jev_timeout_s is 3 s)
    max_calls_per_tick: int = 3  # questions per tick; the cache answers repeats for free


def target_key(t: Target) -> OfferKey:
    return ("ask", t.asset_id) if t.side == "ask" else ("bid", t.ref)


def offer_key(o: OpenOffer) -> OfferKey:
    return ("ask", o.asset_id) if o.side == "ask" else ("bid", o.ref)


def default_label(side: str) -> str:
    """The label of today's price: the strategy prices an ask at the top and a bid at the tape."""
    return "aggressive" if side == "ask" else "fair"


def ask_floor(value: float, rules: Guardrails) -> int:
    """The lowest ask GUARDRAILS.md allows for what selling costs us (`value × sell_min_value_ratio`)."""
    return math.ceil(value * rules.sell_min_value_ratio - ROUNDING)


def price_candidates(t: Target, params: StrategyParams, rules: Guardrails) -> dict[str, int]:
    """Distinct prices by label, in label order. Today's price is always there; a tie keeps its label."""
    if t.side == "ask":
        clears = math.ceil(t.value + params.sell_min_surplus - ROUNDING)
        quick = min(t.price, max(ask_floor(t.value, rules), clears))
        prices = {"aggressive": t.price, "fair": math.ceil((t.price + quick) / 2), "quick_sale": quick}
    else:
        top = math.floor(t.value - params.min_buy_surplus + ROUNDING)
        cap = rules.max_price_for(t.rarity)
        quick = max(t.price, top if cap is None else min(top, cap))
        prices = {"aggressive": max(1, 2 * t.price - quick), "fair": t.price, "quick_sale": quick}
    default = default_label(t.side)
    kept: dict[str, int] = {default: prices[default]}
    for label in LABELS:
        if prices[label] not in kept.values():
            kept[label] = prices[label]
    return {label: kept[label] for label in LABELS if label in kept}


def hold_is_legal(o: OpenOffer, t: Target, rules: Guardrails) -> bool:
    """Holding a stale offer must still respect the hard limits: an ask at or above the sell floor, a bid at
    or below its price cap and below what the card is worth to us."""
    if o.side == "ask":
        return o.price >= ask_floor(t.value, rules)
    cap = rules.max_price_for(t.rarity)
    return o.price < t.value and (cap is None or o.price <= cap)


def listing_state(
    t: Target, candidates: Mapping[str, int], legal: Mapping[str, int], context: Mapping[str, Any]
) -> dict[str, Any]:
    """What Jev reads for `list_price_choice`: the target, its legal candidates and our capacity."""
    return {
        "listing": {
            "side": t.side,
            "card": t.ref,
            "rarity": t.rarity,
            "candidates": dict(legal),
            "not_allowed": sorted(set(candidates) - set(legal)),
            "default": default_label(t.side),
            "value_to_us": t.value,
            "strategy_score": t.score,
            "strategy_reason": t.reason,
        },
        **context,
    }


def reprice_state(o: OpenOffer, t: Target, tick: int, context: Mapping[str, Any]) -> dict[str, Any]:
    """What Jev reads for `reprice_or_hold`: the standing offer, the new price and how long it stood."""
    return {
        "offer": {
            "side": o.side,
            "card": o.ref,
            "price": o.price,
            "age_ticks": None if o.created_tick is None else tick - o.created_tick,
            "expires_in_ticks": None if o.expires_tick is None else o.expires_tick - tick,
            "venue": o.venue,
        },
        "new_price": t.price,
        "change": t.price - o.price,
        "value_to_us": t.value,
        "strategy_reason": t.reason,
        **context,
    }


# ---------------------------------------------------------------- outcomes for calibration


@dataclass(frozen=True)
class _Watched:
    digest: str
    question: str
    verdict: str
    expires_tick: int | None


class OfferWatch:
    """Decided verdicts per live offer id, settled into outcome lines when the offer leaves our open offers."""

    def __init__(self, journal: JevJournal | None) -> None:
        self.journal = journal
        self._watched: dict[int, list[_Watched]] = {}
        self._cancelled: set[int] = set()

    def watch(self, offer_id: int, advice: JevAdvice | None, question: str, expires_tick: int | None) -> None:
        if self.journal is None or advice is None or not advice.decided or not advice.digest:
            return
        entry = _Watched(advice.digest, question, advice.verdict, expires_tick)
        self._watched[offer_id] = [*self._watched.get(offer_id, []), entry]

    def cancelled(self, offer_id: int) -> None:
        if offer_id in self._watched:
            self._cancelled.add(offer_id)

    def observe(self, mine: Iterable[OpenOffer], tick: int) -> list[str]:
        """Outcomes for every watched offer no longer open; one summary line per verdict."""
        open_now = {o.id: o for o in mine}
        lines = []
        for offer_id in [i for i in self._watched if i not in open_now]:
            for w in self._watched.pop(offer_id):
                outcome, note = self._outcome(offer_id, w, tick)
                if self.journal is not None:
                    self.journal.outcome(w.digest, outcome, w.question, note)
                lines.append(f"offer {offer_id}: jev {w.question} {w.verdict} was {outcome} ({note})")
            self._cancelled.discard(offer_id)
        for offer_id, o in open_now.items():  # the server's expiry wins over ours
            if offer_id in self._watched and o.expires_tick is not None:
                self._watched[offer_id] = [replace(w, expires_tick=o.expires_tick) for w in self._watched[offer_id]]
        return lines

    def _outcome(self, offer_id: int, w: _Watched, tick: int) -> tuple[str, str]:
        if offer_id in self._cancelled:
            return "unknown", f"offer {offer_id} withdrawn by us before it filled"
        if w.expires_tick is not None and tick >= w.expires_tick:
            return "wrong", f"offer {offer_id} expired unfilled at tick {w.expires_tick}"
        return "right", f"offer {offer_id} left our open offers before expiry: filled (inferred)"


# ---------------------------------------------------------------- the maker's Jev


class MakerJev:
    """Picks list prices and reprice-or-hold with Jev, inside what the maker already allows."""

    def __init__(
        self,
        price_fn: JevFn = no_jev,
        reprice_fn: JevFn = no_jev,
        *,
        journal: JevJournal | None = None,
        config: MakerJevConfig | None = None,
    ) -> None:
        self.price_fn, self.reprice_fn = price_fn, reprice_fn
        self.config = config or MakerJevConfig()
        self.watch = OfferWatch(journal)
        self._labels: dict[OfferKey, str] = {}
        self._cache: dict[tuple[object, ...], JevAdvice] = {}
        self._calls = 0

    def begin_tick(self, mine: Iterable[OpenOffer]) -> None:
        """A label lives as long as its offer stands: no open offer, no remembered label."""
        standing = {offer_key(o) for o in mine}
        self._labels = {key: label for key, label in self._labels.items() if key in standing}
        self._calls = 0

    def remembered(self, t: Target, params: StrategyParams, rules: Guardrails) -> Target:
        """The target at its remembered label's price, so a standing offer is not repriced back to today's."""
        label = self._labels.get(target_key(t))
        candidates = price_candidates(t, params, rules)
        if label is None or label not in candidates or candidates[label] == t.price:
            return t
        return replace(t, price=candidates[label], reason=f"{t.reason}; jev {label} (kept)")

    def choose_price(
        self,
        t: Target,
        candidates: Mapping[str, int],
        legal: Mapping[str, int],
        state: dict[str, Any],
        left: Callable[[], float],
    ) -> tuple[str, JevAdvice | None, str]:
        """(label, Jev's advice, why). Today's label unless Jev decided on another legal one."""
        default = default_label(t.side)
        key = target_key(t)
        kept = self._labels.get(key)
        if kept in legal and kept is not None:
            return kept, None, f"jev {kept} (kept)"
        if len(legal) < 2 or self.price_fn is no_jev:
            return default, None, "one legal price" if len(legal) < 2 else "jev off"
        cache_key = (PRICE_QUESTION, key, tuple(sorted(legal.items())))
        advice = self._ask(cache_key, self.price_fn, state, left)
        if advice.decided and advice.verdict in legal:
            self._labels[key] = advice.verdict
            return advice.verdict, advice, f"jev {advice.verdict} ({advice.value:.2f}) of {dict(legal)}"
        why = advice.reason if not advice.decided else f"{advice.verdict} is not a legal price here"
        return default, advice, f"jev undecided ({why}): today's {default} {candidates[default]}"

    def should_hold(
        self, o: OpenOffer, t: Target, rules: Guardrails, state: dict[str, Any], left: Callable[[], float]
    ) -> tuple[bool, JevAdvice | None]:
        """True only on a decided `no` to `reprice_or_hold`, and only when holding is legal."""
        if self.reprice_fn is no_jev or not hold_is_legal(o, t, rules):
            return False, None
        advice = self._ask((REPRICE_QUESTION, o.id, t.price), self.reprice_fn, state, left)
        return advice.verdict == "no", advice

    def _ask(self, key: tuple[object, ...], fn: JevFn, state: dict[str, Any], left: Callable[[], float]) -> JevAdvice:
        cached = self._cache.get(key)
        if cached is not None:  # one call, one outcome: a reused verdict carries no digest
            return replace(cached, digest=None, reason=cached.reason or "cached")
        if self._calls >= self.config.max_calls_per_tick or left() < self.config.min_budget_s:
            return JevAdvice("undecided", 0.0, reason="no tick budget for jev")
        self._calls += 1
        advice = fn(state)
        if advice.reason in CACHED_REASONS:
            if len(self._cache) >= CACHE_MAX:
                self._cache.clear()
            self._cache[key] = advice
        return advice
