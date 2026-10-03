"""El Taller (the Workshop, a level active since game hour 7.2): three spare copies of one rarity become one card
of the next rarity. The pull is luck, shown and never scored: its worth is a missing page card, or stock to sell.

The only source is `GET /api/levels` (id `taller`): "POST /api/taller {"assets": [a, b, c]}: three spare copies of
one rarity (you keep at least one of each card) become one card of the next rarity." Its answer, cost and cooldown
are not published: `TallerResult` keeps every field it gets, and nothing here assumes one.

`plan_taller` is pure and deterministic (no Jev, no LLM): FREE spares only (held, less the copies our open offers
give, less the one copy of each card we always keep), commons before uncommons, and within a rarity the copies of
cards held more than `max_copies_kept` times first, then the cheapest sets (lowest multiplier in /me `affinity`),
then the lowest `your_value`.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

from pydantic import BaseModel, ConfigDict

from bazaar_agent.agents.seller import Commitments, committed_context, open_commitments
from bazaar_agent.guardrails import Action, Context, Guardrails, LedgerStore, Verdict, check, context_from

INPUTS = 3  # copies per conversion
# The rarities we feed in, in order: a common triple first (→ an uncommon), then an uncommon triple (→ a rare).
# Rares and above are never fed in: a spare rare sells to Pilar or Chato for more than a lucky pull is worth.
TALLER_RARITIES: tuple[str, ...] = ("common", "uncommon")
NEXT_RARITY = {"common": "uncommon", "uncommon": "rare", "rare": "epic", "epic": "legendary"}
TALLER_KIND = "taller"  # the decisions kind, the ledger row kind and the guardrails action kind


class TallerResult(BaseModel):
    """`POST /api/taller`'s answer. Unpublished: every field is kept (`extra=allow`), none is required."""

    model_config = ConfigDict(extra="allow")

    def pulled(self) -> list[str]:
        """The card refs the answer names, wherever it puts them (`card`, `cards`, `asset`, `pulled`, ...)."""
        return sorted(set(_refs(self.model_dump())))


def _refs(value: Any) -> Iterable[str]:
    if isinstance(value, Mapping):
        ref = value.get("ref")
        if isinstance(ref, str) and value.get("kind", "card") == "card":
            yield ref
        for v in value.values():
            if isinstance(v, Mapping | list):
                yield from _refs(v)
    elif isinstance(value, list):
        for v in value:
            yield from _refs(v)


@dataclass(frozen=True)
class Spare:
    asset_id: int
    ref: str
    rarity: str
    your_value: float
    multiplier: float
    copies: int  # copies of this card we hold (all of them, listed ones included)


@dataclass(frozen=True)
class TallerPlan:
    rarity: str
    pulls: str  # the rarity the conversion gives
    spares: tuple[Spare, ...]

    @property
    def assets(self) -> list[int]:
        return [s.asset_id for s in self.spares]

    @property
    def refs(self) -> list[str]:
        return [s.ref for s in self.spares]

    @property
    def your_value(self) -> float:
        return round(sum(s.your_value for s in self.spares), 2)


def _rarity(asset: Mapping[str, Any], catalog: Mapping[str, Any] | None) -> str:
    rarity = asset.get("rarity")
    if isinstance(rarity, str) and rarity:
        return rarity.strip().lower()
    for s in (catalog or {}).get("sets") or []:
        for c in s.get("cards") or [] if isinstance(s, Mapping) else []:
            if isinstance(c, Mapping) and c.get("id") == asset.get("ref"):
                return str(c.get("rarity") or "").strip().lower()
    return ""  # unknown: never fed in


def _value(asset: Mapping[str, Any]) -> float | None:
    v = asset.get("your_value")
    return float(v) if isinstance(v, int | float) and not isinstance(v, bool) else None


def free_spares(
    me: Mapping[str, Any], commitments: Commitments, catalog: Mapping[str, Any] | None = None
) -> list[Spare]:
    """Every copy we may feed in: of each card, what we hold less the copies our open offers give (an offer that
    does not name its card counts against every card: fail closed) less the one we keep. A copy of unknown
    rarity, unknown `your_value` or no integer id is never a spare."""
    affinity = me.get("affinity") if isinstance(me.get("affinity"), Mapping) else {}
    cards: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for a in me.get("assets") or []:
        if isinstance(a, Mapping) and a.get("kind") == "card" and isinstance(a.get("ref"), str):
            cards[str(a["ref"])].append(a)
    listed = Counter(commitments.listed_refs)
    out: list[Spare] = []
    for ref, copies in cards.items():
        spare = len(copies) - listed[ref] - commitments.unnamed_listed - 1
        free = [a for a in copies if isinstance(a.get("id"), int) and a["id"] not in commitments.listed]
        for a in sorted(free, key=lambda a: int(a["id"]), reverse=True)[: max(0, spare)]:
            rarity, value = _rarity(a, catalog), _value(a)
            if not rarity or value is None:
                continue
            code = ref.split("-", 1)[0].upper()
            mult = affinity.get(code) if isinstance(affinity, Mapping) else None
            multiplier = float(mult) if isinstance(mult, int | float) and not isinstance(mult, bool) else 1.0
            out.append(Spare(int(a["id"]), ref, rarity, value, multiplier, len(copies)))
    return out


def plan_taller(
    me: Mapping[str, Any],
    open_offers: Iterable[dict[str, Any]],
    rules: Guardrails,
    catalog: Mapping[str, Any] | None = None,
) -> TallerPlan | None:
    """The best three free spares of one rarity to convert, or None (switch off, or no triple of free spares)."""
    if not rules.taller_enabled:
        return None
    commitments = open_commitments(open_offers, str(me.get("id") or ""))
    spares = free_spares(me, commitments, catalog)
    for rarity in TALLER_RARITIES:
        pool = [s for s in spares if s.rarity == rarity]
        if len(pool) < INPUTS:
            continue
        pool.sort(key=lambda s: (s.copies <= rules.max_copies_kept, s.multiplier, s.your_value, s.ref, s.asset_id))
        return TallerPlan(rarity, NEXT_RARITY[rarity], tuple(pool[:INPUTS]))
    return None


class TallerError(ValueError):
    """Hand-picked asset ids that cannot be fed in (not ours, not a card, in an open offer, not three)."""


def plan_from_ids(
    me: Mapping[str, Any],
    assets: Sequence[int],
    open_offers: Iterable[dict[str, Any]],
    catalog: Mapping[str, Any] | None = None,
) -> TallerPlan:
    """A plan for three hand-picked asset ids (`bazaar taller a b c`). The guardrails still check it: one rarity,
    never a last copy, the switch and the hourly cap."""
    if len(assets) != INPUTS or len(set(assets)) != INPUTS:
        raise TallerError(f"El Taller takes {INPUTS} different asset ids, got {list(assets)}")
    commitments = open_commitments(open_offers, str(me.get("id") or ""))
    affinity = me.get("affinity") if isinstance(me.get("affinity"), Mapping) else {}
    cards = [a for a in me.get("assets") or [] if isinstance(a, Mapping) and a.get("kind") == "card"]
    by_id = {a.get("id"): a for a in cards}
    copies = Counter(str(a.get("ref")) for a in cards)
    spares: list[Spare] = []
    for asset_id in assets:
        a = by_id.get(asset_id)
        if a is None:
            raise TallerError(f"asset {asset_id} is not a card we hold (read /api/me)")
        if asset_id in commitments.listed:
            raise TallerError(f"asset {asset_id} is in one of our open offers")
        ref, value = str(a.get("ref")), _value(a)
        mult = affinity.get(ref.split("-", 1)[0].upper()) if isinstance(affinity, Mapping) else None
        multiplier = float(mult) if isinstance(mult, int | float) and not isinstance(mult, bool) else 1.0
        spares.append(Spare(asset_id, ref, _rarity(a, catalog), value or 0.0, multiplier, copies[ref]))
    rarity = spares[0].rarity if len({s.rarity for s in spares}) == 1 else "mixed"
    return TallerPlan(rarity, NEXT_RARITY.get(rarity, "?"), tuple(spares))


def taller_context(
    me: Mapping[str, Any],
    open_offers: Iterable[dict[str, Any]],
    tick: int,
    t_hours: float,
    ledger: LedgerStore,
    rules: Guardrails,
) -> Context:
    """The guardrail context of a conversion: /me (read just before), the copies our open offers give
    (`sellable`), the kill switch, and the conversions every process booked this game hour."""
    offers = list(open_offers)
    base = context_from(dict(me), tick, t_hours, ledger, rules)
    ctx = committed_context(base, open_commitments(offers, str(me.get("id") or "")))
    return replace(ctx, tallers_last_hour=ledger.count_since(TALLER_KIND, t_hours - 1.0))


def taller_action(plan: TallerPlan) -> Action:
    return Action("taller", ",".join(plan.refs), plan.rarity)


@dataclass(frozen=True)
class Converted:
    plan: TallerPlan
    verdict: Verdict
    sent: bool  # the request went out (live, allowed); `result` is None when the server refused it
    result: TallerResult | None
    decision_id: int


def convert(
    team: Any,
    plan: TallerPlan,
    ctx: Context,
    rules: Guardrails,
    rec: Any,
    ledger: LedgerStore,
    *,
    tick: int,
    t_hours: float,
    live: bool,
) -> Converted:
    """Check, record and (live) send ONE conversion: the one code path of the taker, `bazaar taller` and the MCP
    tool. The ledger row is booked BEFORE the send, so a refusal over-counts the hourly cap (fail safe), never
    under-counts it. The answer is recorded raw (`executions`, scrubbed) and its pulled card in a decisions row."""
    verdict = check(taller_action(plan), ctx, rules)
    status = "approved" if verdict.allowed else "rejected"
    refs = ", ".join(plan.refs)
    did = int(
        rec.decide(
            tick,
            TALLER_KIND,
            f"El Taller: {plan.rarity} x{len(plan.spares)} ({refs}) -> one {plan.pulls} · guardrails {verdict}",
            inputs={
                "assets": plan.assets,
                "refs": plan.refs,
                "rarity": plan.rarity,
                "pulls": plan.pulls,
                "your_value": plan.your_value,
                "tallers_last_hour": ctx.tallers_last_hour,
            },
            reason=f"spares {refs}: one {plan.pulls} pull (luck, never scored)",
            guardrail=str(verdict),
            chosen=verdict.allowed,
            status=status,
            move={"taller": plan.assets},
        )
    )
    if not verdict.allowed or not live:
        return Converted(plan, verdict, False, None, did)
    ledger.record(TALLER_KIND, tick, t_hours, 0, ",".join(plan.refs))
    body = rec.send(did, tick, TALLER_KIND, {"assets": plan.assets}, lambda: team.taller(plan.assets))
    result = TallerResult.model_validate(body) if body is not None else None
    if result is not None:
        pulled = result.pulled()
        rec.decide(
            tick,
            "taller_pulled",
            f"El Taller pulled {', '.join(pulled) or 'a card the answer does not name'} for {refs}",
            inputs={"assets": plan.assets, "refs": plan.refs, "pulled": pulled, "response": result.model_dump()},
            reason="luck, never scored",
            guardrail="-",
            chosen=False,
            status="done",
        )
    return Converted(plan, verdict, True, result, did)
