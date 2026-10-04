"""Card hunt (Sun 4 Oct): trade with the other teams for surplus at our values; dealers only fill ladder slots.

Round 3 restarted every score leg. On Sunday our five dealer buys (album collecting) left `neg_points` at 0 (a dealer
buy never moves it) and scored only as ladder slots, while each team trade moved its teams' board by +0.9 to +2.2.
With `BAZAAR_CARD_HUNT` on (the default), the taker:

1. opens a dealer BUY only with a dealer whose ladder level has an empty slot this round (`fills_slot`; an unknown
   level is not a slot): collecting from dealers adds nothing to the score. A pack only as restock inventory for
   resale to teams (strategy `pack_restock`, GUARDRAILS `pack_restock_enabled`, #289): that is team trading; a pack
   bought for its holding value is dropped;
2. runs the ladder probe on its deterministic plan (an empty slot, a price inside the dealer's range and our value,
   every guardrail per send) without waiting for Jev's yes: at 15 s ticks Jev answers instead of the LLM decider
   and was undecided on most probes;
3. lets the team desk propose and take swaps on its deterministic gate (`guardrails.check()` on every leg, the
   fairness `judge`, the swap cash cap) instead of Jev's yes (undecided on every Sunday team thread), with more of
   the six conversations (`desk_limits`): the dealer reserve shrinks because dealer threads are now ladder-only;
4. (now the taker's own rule, #287, not a hunt switch) a standing ask below our value is taken even when our own
   bid for that card is lower (the bid is withdrawn after the accept);
5. prices a sale of the only copy of a card on a page that misses more than `PAGE_HORIZON` cards at its
   `your_value` alone: that page cannot complete before the freeze, so its bonus share is not at stake. A complete
   page keeps every protection (`protect_page_sets`, `protect_complete_pages_only`).

Every accept still goes through `guardrails.check()` and the shared accept ledger (duels first, one accept per tick);
nothing here lowers a value floor. No extra keyed request: everything reads what the tick already read.
The maker's dealer sell desk follows the same ladder rule, still behind `dealer_sell_enabled` (unchanged): a sell
thread only where it fills an empty ladder slot (L3 Pilar and L5 Banco sell us nothing we may buy; only a sale fills
them), on the desk's own plan (spare copies, floor at our value plus `dealer_sell_min_surplus`) without Jev's yes.
`BAZAAR_CARD_HUNT=0` (or off/false/no) restores the old behaviour at the next start.
"""

from __future__ import annotations

from bazaar_agent.agents.ladder_probe import LadderSlots
from bazaar_agent.guardrails import Guardrails

CARD_HUNT_ENV = "BAZAAR_CARD_HUNT"  # read by the taker and maker CLIs (`--card-hunt/--no-card-hunt`), default on
CARD_HUNT_HELP = (
    "Card hunt (default on): dealer buys and sells only for empty ladder slots, team trades on the deterministic "
    "gate, packs only as restock for team resale (BAZAAR_CARD_HUNT=0: the behaviour before)"
)
RESALE_PACKS = "pack_restock"  # `strategy.pack_moves`: a pack bought to open and resell to teams (#289)
PAGE_HORIZON = 2  # a page missing more cards than this cannot complete before the freeze: no bonus at stake
# Chamberí, released this round: the page the maker's outreach bids still complete from teams (the completer bought
# from a team scores the whole page bonus, collections.md). Its single copies keep their bonus share and are not sold.
KEEP_SETS = frozenset({"CHA"})
DESK_MAX_OPEN = 4  # team threads at once while hunting (the server's six conversations still bound it)
DESK_DEALER_RESERVE = 1  # conversations kept for dealers while hunting (ladder slots only)


def fills_slot(dealer: str, slots: LadderSlots | None) -> bool:
    """A dealer deal with `dealer` can fill a ladder slot: its level is known and has an empty slot this round."""
    if slots is None:
        return False
    level = slots.levels.get(dealer)
    return level is not None and slots.empty(level) > 0


def desk_limits(rules: Guardrails) -> tuple[int, int]:
    """(team threads at once, conversations reserved for dealers) while hunting: never fewer team threads and
    never a bigger dealer reserve than GUARDRAILS already gives."""
    return (
        max(rules.team_threads_max_open, DESK_MAX_OPEN),
        min(rules.team_threads_dealer_reserve, DESK_DEALER_RESERVE),
    )
