"""The persona model: a dealer's published behaviour (`GET /api/dealers` personas: traits, menu, unlock rules)
turned into negotiation parameters, so a dealer we have no data on (the L4/L5 dealers) is traded sensibly from
its first message, and the learned curves take over once its fills say more.

Pure: no I/O. The inputs are the persona payload (untrusted: validated here, a bad field falls back to a
neutral default) and, optionally, the learned `CurveStats` for one (dealer, price class).

The trait prior, calibrated on Friday's real threads (`learn.curves` over the captured feed, tick 0-458):
- the dealer's limit sits near `list × (1 + LIMIT_SLOPE × (shrewdness − generosity))`: Abuela's uncommon
  fills p50 22.5 of list 25 (0.90), her packs 21 of 26 (0.81); Chato's uncommons 30 of 26 (1.15), rares 89.5
  of 77 (1.16);
- it opens at about `list × (OPEN_BASE + OPEN_SLOPE × shrewdness)` (Abuela 29-30 = 1.16-1.2; Chato 33/97 = 1.26);
- it names a final after ~`BIDS_BASE + BIDS_SLOPE × patience` of our bids (Friday: 4-6 for both: the trait
  barely moves it, so the slope is small);
- every dealer matched our step on Friday (Chato: "You moved 1, I move 1"): step 1; a shrewd or strict one
  also gets terse words, no theatrics.

Learned curves win once a class has at least `MIN_LEARNED_FILLS` informative fills. Whatever the source, the
params never raise a price: the caller applies them only to lower a start, narrow a walk point or lengthen a
ladder, and every send still goes through `guardrails.check` and the official-value cap.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from bazaar_agent.learn.curves import CurveStats, quantile

Tone = Literal["kind", "neutral", "terse"]

MIN_LEARNED_FILLS = 5  # informative fills before a class's curve replaces the trait prior (as `evolve.MIN_FILLS`)
LIMIT_SLOPE = 0.25  # limit / list = 1 + this × (shrewdness − generosity)
SPREAD = 0.15  # the prior's start sits this share of list below the expected limit; the walk the same above it
OPEN_BASE, OPEN_SLOPE = 1.12, 0.17  # opening ask / list = OPEN_BASE + OPEN_SLOPE × shrewdness
BIDS_BASE, BIDS_SLOPE = 4.0, 2.0  # bids before a final = BIDS_BASE + BIDS_SLOPE × patience
MIN_OPEN_SHARE = 0.4  # never open below this share of the dealer's opening ask (as `dealer_plan.MIN_OPEN_SHARE`)
KIND_GENEROSITY, KIND_STRICTNESS = 0.6, 0.3  # generosity at or above, or strictness at or below: kind words
TERSE_LEVEL = 0.7  # strictness or shrewdness at or above: terse words, no haggling theatrics
COOLOFF_RISKY = 0.6  # cooloff risk at or above: never re-send a message the dealer did not answer
NEUTRAL = 0.5
STEP = 1  # our step between bids, for every dealer (see `trait_prior`)


class Traits(BaseModel):
    """The six published traits, each in [0, 1]. A missing or bad trait reads as neutral (0.5)."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    patience: float = Field(default=NEUTRAL, ge=0, le=1)
    generosity: float = Field(default=NEUTRAL, ge=0, le=1)
    shrewdness: float = Field(default=NEUTRAL, ge=0, le=1)
    memory: float = Field(default=NEUTRAL, ge=0, le=1)
    strictness: float = Field(default=NEUTRAL, ge=0, le=1)
    chattiness: float = Field(default=NEUTRAL, ge=0, le=1)

    @classmethod
    def of(cls, raw: Any) -> Traits:
        """Each trait on its own: one out-of-range value never discards the others."""
        if not isinstance(raw, Mapping):
            return cls()
        good: dict[str, float] = {}
        for name in cls.model_fields:
            value = raw.get(name)
            if isinstance(value, int | float) and not isinstance(value, bool) and 0 <= value <= 1:
                good[name] = float(value)
        try:
            return cls(**good)
        except ValidationError:
            return cls()


@dataclass(frozen=True)
class SellLine:
    item: str  # a pack id or a rarity
    list_price: int
    opening_ask: int | None
    per_team_per_hour: int | None
    sets: tuple[str, ...] | None  # None: every released set


@dataclass(frozen=True)
class BuyLine:
    rarity: str
    sets: tuple[str, ...] | None  # None: every released set


@dataclass(frozen=True)
class Unlock:
    always: bool
    early_deals_with: str | None
    early_min_deals: int
    early_min_level: int
    open_to_all: bool


@dataclass(frozen=True)
class Persona:
    id: str
    name: str
    kind: str
    level: int
    status: str
    traits: Traits
    sells: tuple[SellLine, ...]
    buys: tuple[BuyLine, ...]
    deals_per_team_per_hour: int | None
    unlock: Unlock
    traits_published: bool = True  # False: the payload had no traits; neutral defaults stand in, no prior is used

    def list_price(self, item: str) -> int | None:
        line = next((s for s in self.sells if s.item == item), None)
        return line.list_price if line else None

    def buys_card(self, rarity: str | None, set_code: str | None) -> bool:
        """The menu buys this rarity in this set (never a common from a collector that lists none)."""
        return any(b.rarity == rarity and (b.sets is None or set_code in b.sets) for b in self.buys)

    def preferred_sets(self) -> frozenset[str]:
        """Sets a buy line names explicitly: a collector's favourites (Pilar: SAL, RET)."""
        return frozenset(s for b in self.buys if b.sets is not None for s in b.sets)


def _int(value: Any) -> int | None:
    return int(value) if isinstance(value, int | float) and not isinstance(value, bool) and value >= 0 else None


def _sets(raw: Any) -> tuple[str, ...] | None:
    if isinstance(raw, list):
        return tuple(str(s)[:8] for s in raw if isinstance(s, str))
    if isinstance(raw, str) and raw not in ("released", "all"):
        return (raw[:8],)
    return None


def _rarities(raw: Any) -> list[str]:
    return [str(r) for r in (raw if isinstance(raw, list) else [raw]) if isinstance(r, str)]


def parse_persona(raw: Mapping[str, Any]) -> Persona | None:
    """One persona payload (untrusted) → a Persona; None without an id."""
    pid = raw.get("id")
    if not isinstance(pid, str) or not pid:
        return None
    raw_menu = raw.get("menu")
    menu: Mapping[str, Any] = raw_menu if isinstance(raw_menu, Mapping) else {}
    sells: list[SellLine] = []
    for s in menu.get("sells") or []:
        if not isinstance(s, Mapping):
            continue
        item, price = s.get("pack") or s.get("rarity"), _int(s.get("list_price"))
        if isinstance(item, str) and price:
            sells.append(
                SellLine(
                    item, price, _int(s.get("opening_ask")), _int(s.get("per_team_per_hour")), _sets(s.get("sets"))
                )
            )
    buys = [
        BuyLine(r, _sets(b.get("sets")))
        for b in menu.get("buys") or []
        if isinstance(b, Mapping)
        for r in _rarities(b.get("rarity"))
    ]
    raw_unlock = raw.get("unlock")
    un: Mapping[str, Any] = raw_unlock if isinstance(raw_unlock, Mapping) else {}
    early = un.get("early_deals_with")
    unlock = Unlock(
        always=un.get("always") is True,
        early_deals_with=early if isinstance(early, str) and early else None,
        early_min_deals=_int(un.get("early_min_deals")) or 0,
        early_min_level=_int(un.get("early_min_level")) or 0,
        open_to_all=raw.get("open_to_all") is True,
    )
    return Persona(
        id=pid,
        name=str(raw.get("name") or pid)[:60],
        kind=str(raw.get("kind") or "dealer"),
        level=_int(raw.get("level")) or 0,
        status=str(raw.get("status") or "unknown"),
        traits=Traits.of(raw.get("traits")),
        sells=tuple(sells),
        buys=tuple(buys),
        deals_per_team_per_hour=_int(menu.get("deals_per_team_per_hour")),
        unlock=unlock,
        traits_published=isinstance(raw.get("traits"), Mapping) and bool(raw.get("traits")),
    )


def parse_personas(payload: Iterable[Any]) -> dict[str, Persona]:
    out: dict[str, Persona] = {}
    for raw in payload:
        if isinstance(raw, Mapping) and (p := parse_persona(raw)) is not None:
            out[p.id] = p
    return out


# ---------------------------------------------------------------- negotiation params


@dataclass(frozen=True)
class NegotiationParams:
    """How we negotiate one (dealer, item). Prices are whole primas; fractions are of the list price."""

    dealer: str
    item: str
    source: Literal["learned", "traits"]
    list_price: int | None
    open_fraction: float  # our first bid / list
    start: int | None  # our first bid (None: no list price known)
    step: int
    walk: int | None  # the most we bid in this conversation, before the caps
    expected_limit: int | None
    opening_ask: int | None  # the dealer's expected first ask
    bids_before_final: int  # bids before the dealer names its final
    reply_wait_ticks: int  # ticks we wait for an answer before we move again (a dealer offer lapses in 2)
    reopen_after_ticks: int  # after a walk: ticks before a new thread for this item with this dealer
    accept_opening_ask: bool  # always False: a deal at the opening ask scores nothing on the ladder
    tone: Tone
    cooloff_risk: float  # 0-1: memory × strictness raise it
    never_repeat_price: bool  # always True: the same price twice earns nothing and reads as spam

    def ladder(self) -> tuple[int, int, int] | None:
        """(start, walk, step), the shape of `strategy.Move.ladder`; None without a list price."""
        if self.start is None or self.walk is None:
            return None
        return (self.start, self.walk, self.step)

    def as_row(self) -> dict[str, Any]:
        return {
            "dealer": self.dealer,
            "item": self.item,
            "source": self.source,
            "list": self.list_price,
            "open_frac": round(self.open_fraction, 3),
            "ladder": f"{self.start}→{self.walk} step {self.step}" if self.start is not None else "-",
            "limit~": self.expected_limit,
            "opens~": self.opening_ask,
            "final_after": self.bids_before_final,
            "wait": self.reply_wait_ticks,
            "reopen": self.reopen_after_ticks,
            "tone": self.tone,
            "cooloff_risk": round(self.cooloff_risk, 2),
        }


def tone_of(t: Traits) -> Tone:
    if t.strictness >= TERSE_LEVEL or t.shrewdness >= TERSE_LEVEL:
        return "terse"
    if t.generosity >= KIND_GENEROSITY or t.strictness <= KIND_STRICTNESS:
        return "kind"
    return "neutral"


def cooloff_risk(t: Traits) -> float:
    """How likely an annoyed dealer locks us out, and for how long it remembers: memory and strictness."""
    return round(min(1.0, 0.5 * t.memory + 0.5 * t.strictness), 3)


def _behaviour(t: Traits) -> tuple[int, int, int]:
    """(bids before a final, reply wait ticks, reopen-after ticks) from the traits alone."""
    bids = max(3, round(BIDS_BASE + BIDS_SLOPE * t.patience))
    wait = 1 if t.chattiness >= NEUTRAL else 2  # a chatty dealer answers within the tick; 2 = its offers' lapse
    reopen = max(2, round(2 + 10 * t.memory * t.strictness))  # Abuela ~2, Chato ~10: a strict memory needs a rest
    return bids, wait, reopen


def trait_prior(persona: Persona, item: str) -> NegotiationParams:
    """The params from the traits and the menu alone: a new dealer's first conversations."""
    t = persona.traits
    line = next((s for s in persona.sells if s.item == item), None)
    bids, wait, reopen = _behaviour(t)
    tone, risk = tone_of(t), cooloff_risk(t)
    if line is None:
        return NegotiationParams(
            persona.id,
            item,
            "traits",
            None,
            0.0,
            None,
            1,
            None,
            None,
            None,
            bids,
            wait,
            reopen,
            False,
            tone,
            risk,
            True,
        )
    lst = line.list_price
    limit_frac = 1 + LIMIT_SLOPE * (t.shrewdness - t.generosity)
    opening = line.opening_ask or math.ceil(lst * (OPEN_BASE + OPEN_SLOPE * t.shrewdness))
    limit = math.ceil(lst * limit_frac)
    open_frac = max(limit_frac - SPREAD, MIN_OPEN_SHARE * opening / lst)
    start = max(1, min(math.floor(lst * open_frac), opening - 1))
    walk = max(start, min(opening - 1, math.ceil(lst * (limit_frac + SPREAD))))
    # Small steps earn small steps: every dealer on Friday matched our step (Chato one for one), and the replay of
    # Abuela's real threads scores step 1 above step 2 (uncommons 0.40 vs 0.33).
    step = STEP
    return NegotiationParams(
        persona.id,
        item,
        "traits",
        lst,
        start / lst,
        start,
        step,
        walk,
        limit,
        opening,
        bids,
        wait,
        reopen,
        False,
        tone,
        risk,
        True,
    )


def derive(persona: Persona, item: str, curve: CurveStats | None = None) -> NegotiationParams:
    """The params for (dealer, item): the learned curve when it has `MIN_LEARNED_FILLS` informative fills,
    else the trait prior. The tone, cooloff risk and waits always come from the traits (behaviour, not price)."""
    prior = trait_prior(persona, item)
    fills: Sequence[int] = curve.informative_fills if curve is not None else ()
    if curve is None or len(fills) < MIN_LEARNED_FILLS:
        return prior
    lo, hi, mid = quantile(fills, 0.1), quantile(fills, 0.9), quantile(fills, 0.5)
    assert lo is not None and hi is not None and mid is not None
    bids = round(curve.patience) if curve.patience else prior.bids_before_final
    opening = round(curve.opening) if curve.opening is not None else prior.opening_ask
    start = max(1, math.floor(lo))
    if opening is not None:
        start = max(start, math.ceil(MIN_OPEN_SHARE * opening))
        start = min(start, opening - 1)
    walk = max(start, math.ceil(hi) if opening is None else min(opening - 1, math.ceil(hi)))
    step = STEP
    lst = prior.list_price
    return NegotiationParams(
        persona.id,
        item,
        "learned",
        lst,
        start / lst if lst else 0.0,
        start,
        step,
        walk,
        math.ceil(mid),
        opening,
        max(3, bids),
        prior.reply_wait_ticks,
        prior.reopen_after_ticks,
        False,
        prior.tone,
        prior.cooloff_risk,
        True,
    )


def plan_with_prior(ladder: tuple[int, int, int], params: NegotiationParams) -> tuple[tuple[int, int, int], str | None]:
    """The strategy's (start, top, step) shaped by the params, never above it: the start and walk only go
    down, the step only shrinks. Returns the ladder and what changed (None: unchanged)."""
    start, top, step = ladder
    mine = params.ladder()
    if mine is None:
        return ladder, None
    p_start, p_walk, p_step = mine
    new_top = max(1, min(top, p_walk))
    new_start = max(1, min(start, p_start, new_top))
    new = (new_start, new_top, max(1, min(step, p_step)))
    if new == ladder:
        return ladder, None
    return (
        new,
        f"persona {params.dealer} ({params.source}): {start}→{top} step {step} → {new[0]}→{new[1]} step {new[2]}",
    )


# ---------------------------------------------------------------- budgets, unlocks, sells


def deals_left(persona: Persona, our_deal_ticks: Iterable[int], tick: int, ticks_per_hour: float) -> int | None:
    """Deals this team may still close with the dealer in the rolling hour (None: the menu sets no budget)."""
    cap = persona.deals_per_team_per_hour
    if cap is None:
        return None
    since = tick - ticks_per_hour
    return max(0, cap - sum(1 for t in our_deal_ticks if t > since))


def unlock_targets(
    personas: Mapping[str, Persona], unlocked: Iterable[str], deals_with: Mapping[str, int]
) -> dict[str, int]:
    """dealer we trade with → deals still needed with it to unlock a locked dealer early (`early_deals_with`,
    `early_min_deals`). A dealer already unlocked for us or open to all needs nothing."""
    ours = set(unlocked)
    out: dict[str, int] = {}
    for p in personas.values():
        via = p.unlock.early_deals_with
        if p.id in ours or p.unlock.always or p.unlock.open_to_all or via is None or via not in ours:
            continue
        need = p.unlock.early_min_deals - deals_with.get(via, 0)
        if need > 0:
            out[via] = min(need, out.get(via, need))
    return out


def sell_weight(persona: Persona, rarity: str | None, set_code: str | None, fever: Mapping[str, float]) -> float:
    """How much this dealer should want our copy, as a multiplier for ranking (0: its menu does not buy it).
    A set its buy lines name (a collector's favourite) counts 1.1; an official fever for the set (news: pct over
    book for that set, this persona) multiplies on top. A ranking weight only: no price is raised with it."""
    if not persona.buys_card(rarity, set_code):
        return 0.0
    weight = 1.1 if set_code in persona.preferred_sets() else 1.0
    return round(weight * (1 + max(0.0, fever.get(set_code or "", 0.0)) / 100), 4)
