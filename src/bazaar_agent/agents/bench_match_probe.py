"""ONE live Market Test probe: does the server match a pair whose quotes do not cross?

RULES.md and the kit say "a quote is not a limit": the Market Test counts the gains between the hidden limits. Our
broker (and the free stall) only ever sends pairs whose quotes cross, so we cannot tell whether the server would
match a pair on its hidden limits instead. This module picks the ONE pair worth asking about and nothing else:

  - the non-crossing pair with the smallest gap (`ask + fee - bid`) among the traders today's exact plan leaves
    unmatched this tick, so the probe never takes a seller or a buyer the exact matcher wants;
  - at a price between the two quotes;
  - once per game, ever: the claim is a durable row in the shared Postgres (`KeyVault.claim_once`), taken before
    the request goes out. A redeploy, a second process or a laptop finds the row and never sends a second one, and a
    ledger that cannot be read means nothing is sent.

It is armed only by BAZAAR_BENCH_MATCH_PROBE=once on the maker (default off); `agents/broker.py` calls it after
the exact matches of the tick went out.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from bazaar_agent.agents.matcher import Fee, Match, Quote

PROBE_ENV = "BAZAAR_BENCH_MATCH_PROBE"
PROBE_MODES = ("once",)
CLAIM_NAME = "bench_match_probe"  # the one durable claim: one per target, for the whole game
# The probe waits for a pair this close, or until the run is PATIENCE_TICKS old (quotes relax toward the limits as
# traders age, so a later pair is closer to a limit): then it takes the smallest gap there is.
CLOSE_GAP = 12
PATIENCE_TICKS = 8


@dataclass(frozen=True)
class Probe:
    match: Match
    gap: int  # how far the quotes are from crossing: ask + fee - bid, always positive


def pick_probe(
    quotes: Iterable[Quote], used: set[str], fee: Fee, *, run_age: dict[str, int] | None = None
) -> Probe | None:
    """The non-crossing bench pair with the smallest gap, from traders `used` (this tick's exact plan, and every
    trader already matched) does not hold. None when there is no such pair, or when the best one is still far and
    the run is young (`run_age`: ticks since the run's first read)."""
    free = [q for q in quotes if q.bench and str(q.id) not in used]
    best: Probe | None = None
    for sell in (q for q in free if q.side == "sell"):
        for buy in (q for q in free if q.side == "buy" and q.item == sell.item):
            gap = sell.price + fee.of(sell.price) - buy.price
            if gap <= 0:  # it crosses: the exact matcher's business, never a probe
                continue
            price = max(1, (sell.price + buy.price) // 2)
            cand = Probe(Match(sell, buy, price, fee.of(price)), gap)
            if best is None or (cand.gap, str(sell.id), str(buy.id)) < (
                best.gap,
                str(best.match.sell.id),
                str(best.match.buy.id),
            ):
                best = cand
    if best is None:
        return None
    age = (run_age or {}).get(best.match.sell.item.removeprefix("bench:"), PATIENCE_TICKS)
    if best.gap > CLOSE_GAP and age < PATIENCE_TICKS:
        return None
    return best
