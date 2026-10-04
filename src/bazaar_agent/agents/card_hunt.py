"""Card hunt (Sun 4 Oct): trade with the other teams for surplus at our values; dealers only fill ladder slots.

Round 3 restarted every score leg. On Sunday our five dealer buys (album collecting) left `neg_points` at 0 (a dealer
buy never moves it) and scored only as ladder slots, while each team trade moved its teams' board by +0.9 to +2.2.
While `BAZAAR_CARD_HUNT` is on (CLI default on; `TakerConfig`/`MakerConfig.card_hunt` default off in code), these
GUARDRAILS behaviours change, and GUARDRAILS.md says so under each entry:

1. taker dealer buys: only with a dealer whose ladder level has an empty slot this round (`fills_slot`; an unknown
   level is not a slot). Packs: only restock inventory for resale to teams (strategy `pack_restock`, and only while
   `pack_restock_enabled`, #289); a pack bought for its holding value is dropped. A human-approved buy target
   offered by a dealer on a full level is dropped too (fails closed);
2. `strategy_jev_refresh_ticks` / the ladder probe: runs on its deterministic plan (an empty slot, a price inside the
   dealer's range and under the official value, every guardrail per send) without Jev's yes, one dealer per tick
   (so at most one `GET /api/me/value` per tick for it, each dealer once per game hour);
3. `team_swap_jev_gate`: the team desk proposes and takes swaps on its deterministic gate (`guardrails.check()` on
   every leg, the fairness `judge`, the swap cash cap) without Jev's yes (undecided on every Sunday team thread);
   `team_threads_max_open` 2 -> `DESK_MAX_OPEN` 3 (`desk_limits`). `team_threads_dealer_reserve` is NOT lowered:
   the ladder probes keep their conversations. One more team thread can cost one more `GET /api/threads/{id}` per
   tick when the thread list does not carry its offers;
4. `protect_complete_pages_only` team sales (taker `_bids`): our single copy of a card on a page that misses more
   than `PAGE_HORIZON` cards (and not in `KEEP_SETS`) is priced at its `your_value` alone: that page cannot complete
   before the freeze, so its bonus share is not at stake. A complete page keeps its copy (`protect_page_sets`);
5. `dealer_sell_enabled` (unchanged, false): when Marius turns it on, the maker's dealer sell desk opens a thread only
   where it fills an empty ladder slot, without Jev's yes, and still sells only spare copies (never an only copy).

Every accept still goes through `guardrails.check()` and the shared accept ledger (duels first, one accept per tick);
nothing here lowers a value floor. `BAZAAR_CARD_HUNT=0` (or off/false/no) on a service restores its old behaviour at
its next start. Taking an ask below our value over our own lower bid is the taker's own rule since #287, not the hunt's.
"""

from __future__ import annotations

from bazaar_agent.agents.ladder_probe import LadderSlots
from bazaar_agent.guardrails import Guardrails

CARD_HUNT_ENV = "BAZAAR_CARD_HUNT"  # read by the taker and maker CLIs (`--card-hunt/--no-card-hunt`), default on
CARD_HUNT_HELP = (
    "Card hunt (default on): dealer buys and sells only for empty ladder slots, team swaps and ladder probes on "
    "their deterministic gates, packs only as restock for team resale (BAZAAR_CARD_HUNT=0: the behaviour before)"
)
RESALE_PACKS = "pack_restock"  # `strategy.pack_moves`: a pack bought to open and resell to teams (#289)
PAGE_HORIZON = 2  # a page missing more cards than this cannot complete before the freeze: no bonus at stake
# Chamberí, released this round: the page the maker's outreach bids still complete from teams (the completer bought
# from a team scores the whole page bonus, collections.md). Its single copies keep their bonus share and are not sold.
KEEP_SETS = frozenset({"CHA"})
DESK_MAX_OPEN = 3  # team threads at once while hunting: one more than GUARDRAILS' 2 (the dealer reserve stays)


def fills_slot(dealer: str, slots: LadderSlots | None) -> bool:
    """A dealer deal with `dealer` can fill a ladder slot: its level is known and has an empty slot this round."""
    if slots is None:
        return False
    level = slots.levels.get(dealer)
    return level is not None and slots.empty(level) > 0


def desk_limits(rules: Guardrails) -> tuple[int, int]:
    """(team threads at once, conversations reserved for dealers) while hunting: never fewer team threads than
    GUARDRAILS, and its dealer reserve unchanged (the ladder probes keep their conversations)."""
    return max(rules.team_threads_max_open, DESK_MAX_OPEN), rules.team_threads_dealer_reserve
