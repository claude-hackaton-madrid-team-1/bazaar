"""BITE X17: the taker can take the team's one accept before a slow duel tick claims it.

The team has one accept per tick, shared through the ledger, "duels first": the taker waits
`TakerConfig.duel_grace_s` (2 s, capped at 15 % of the tick) into the tick, then checks whether a `duel:`
accept is already booked, and if not reserves the slot itself. The duel loop (`bazaar duel run --play`,
Jev ON by default) books its accept only after it has read `/api/duels` and after `DuelJev.pick` has waited
for every Jev question of the tick: each call may take `jev_timeout_s` (3 s), and they start after the
0.3 s settle margin and the duels read. On a tick where Jev is slow and the taker holds any accept candidate
(a dealer's ask inside our ladder, a cheap board ask), the duel's accept is refused with "another process
took the team's accept this tick". On a duel's deadline tick that is a lost deal: the duel scores 0.

Strict xfail: passes once the grace covers the duel loop's worst case (or the duel loop books its slot
before asking Jev), then drop the marker.
"""

import pytest

from bazaar_agent.agents.taker import TakerConfig
from bazaar_agent.guardrails import Guardrails
from bazaar_agent.ticks import AFTER_TICK_S
from tests.bites.strictness import STRICT

DUELS_READ_S = 0.2  # one GET /api/duels from Railway; optimistic


@pytest.mark.xfail(strict=STRICT, reason="BITE X17: taker grace (2 s) < duel loop's worst time to book its accept")
@pytest.mark.parametrize("tick_seconds", [30.0, 15.0])
def test_the_taker_waits_longer_than_the_duel_loop_can_take_to_book_its_accept(tick_seconds):
    grace = min(TakerConfig().duel_grace_s, tick_seconds * 0.15)  # Taker._duel_grace
    duel_books_at = AFTER_TICK_S + DUELS_READ_S + Guardrails().jev_timeout_s  # cli.py duel on_tick → pick → reserve
    assert grace >= duel_books_at, f"taker takes the slot at {grace:.2f} s, the duel books it at {duel_books_at:.2f} s"
