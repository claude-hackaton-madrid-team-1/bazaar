"""Forgiving dealers: a trickster's FINAL is not its limit (Los Pícaros, Sat 3 Oct).

A dealer whose published kind is `trickster` (or, when GUARDRAILS `trickster_max_strictness` is above 0, whose
published strictness is at most it) names deadlines it does not keep ("Today only, eh?"; their persona: "their
deadlines never are"). At tick 863 the taker bid 54→55→56 for LAV-10 and took their FINAL 63, their list price, as
if it were their limit: a deal at the list price captures ~0 of the dealer's range on the ladder (RULES.md "Dealers").

The plan we bid such a dealer with (`dealer.BidPlan`) is marked `forgiving`, and `dealer.decide` then reads its FINAL as
a plain ask: never taken nor walked from for being final, we keep stepping by 1. An ask is taken only below its list
price and at or under `accept_max`: its lowest observed fill plus `trickster_accept_fill_share` of its fill range, for
that dealer and price class (a card's rarity, or the pack). No fill seen: no accept at all, we only bid (the dealer may
still take our bid). Every other dealer's plan comes back unchanged.

Pure: the persona is the `/api/dealers` snapshot (`persona_model.parse_persona`), the fills the feed's tape
(`intel.Print`, as `strategy.dealer_fills` reads them).
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import replace

from bazaar_agent import intel
from bazaar_agent.agents.dealer import BidPlan
from bazaar_agent.evals.dealers import RARITIES, card_rarity, price_class
from bazaar_agent.guardrails import Guardrails
from bazaar_agent.persona_model import Persona
from bazaar_agent.strategy import is_team

TRICKSTER = "trickster"  # the published kind of the L4 dealers (`/api/dealers`)
EPSILON = 1e-9  # a share of a whole range (1/3 of 9) never floors one prima low


def is_forgiving(persona: Persona | None, rules: Guardrails) -> bool:
    """A trickster by its published kind, or a dealer whose published strictness is at most the bar. A payload with
    no traits is never forgiving by its neutral defaults, and a dealer `/api/dealers` does not list is not either."""
    if persona is None:
        return False
    if persona.kind.strip().lower() == TRICKSTER:
        return True
    bar = rules.trickster_max_strictness  # 0 (shipped): off, Abuela publishes strictness 0.1 and her FINAL is real
    return bar > 0 and persona.traits_published and persona.traits.strictness <= bar


def list_price_for(persona: Persona, item: str, rarity: str | None = None) -> int | None:
    """Its list price for a pack, or for a card's rarity in the card's set (the lowest menu line that covers it)."""
    if "-" not in item:
        return persona.list_price(item)
    rarity = rarity if rarity in RARITIES else card_rarity(item)  # the catalog's, else the card number's
    set_code = intel.set_of(item)
    prices = [s.list_price for s in persona.sells if s.item == rarity and (s.sets is None or set_code in s.sets)]
    return min(prices) if prices else None


def class_fills(prints: Iterable[intel.Print], dealer: str, item: str) -> list[int]:
    """What teams paid this dealer for one item of `item`'s price class (`strategy.dealer_fills`, by class)."""
    cls = price_class(item)
    if cls is None:
        return []
    sold = (p for p in prints if p.persona == dealer and p.seller == dealer and is_team(p.buyer) and p.items == 1)
    return [p.price for p in sold if p.price > 0 and price_class(p.ref) == cls]


def accept_cap(fills: Sequence[int], share: float) -> int | None:
    """The most we take from a forgiving dealer: its lowest fill plus `share` of its fill range, in whole primas;
    None when no fill is known (we only bid then)."""
    if not fills:
        return None
    low, high = min(fills), max(fills)
    return math.floor(low + share * (high - low) + EPSILON)


def forgiving_plan(
    plan: BidPlan,
    persona: Persona | None,
    item: str,
    rarity: str | None,
    prints: Iterable[intel.Print],
    rules: Guardrails,
) -> BidPlan:
    """The plan for this dealer and item. A forgiving dealer's: step 1 (a fake deadline is no reason to jump), no
    lifted final (N14a), its list price and the most we take from its fills. Any other dealer's: unchanged."""
    if persona is None or not is_forgiving(persona, rules):
        return plan
    fills = class_fills(prints, persona.id, item)
    return replace(
        plan,
        step=1,
        final_max=None,
        lift_after=0,
        forgiving=True,
        list_price=list_price_for(persona, item, rarity),
        accept_max=accept_cap(fills, rules.trickster_accept_fill_share),
    )


def note(plan: BidPlan) -> str:
    """What the plan does with a forgiving dealer, for the logs (private: it holds the most we take)."""
    take = f"we take an ask only at or under {plan.accept_max}" if plan.accept_max is not None else "no fill seen"
    below = f", never at its list price {plan.list_price}" if plan.list_price is not None else ""
    return f"forgiving dealer: its FINAL is not its limit; {take}{below} (else we only bid, by 1)"
