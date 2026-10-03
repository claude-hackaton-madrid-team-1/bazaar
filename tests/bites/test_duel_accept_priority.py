"""BITE X17 (B15): the taker must not take the team's one accept before a duel's endgame accept claims it.

The team has one accept per tick, shared through the ledger, "duels first": the taker waits
`TakerConfig.duel_grace_s` (2 s, capped at 15 % of the tick) into the tick, then checks whether a `duel:`
accept is already booked, and if not reserves the slot itself. The duel loop (`bazaar duel run --play`,
Jev ON by default) used to book its accept only after `DuelJev.pick` had waited for every Jev question of
the tick (each up to `jev_timeout_s`, 3 s, and `pick` waits up to the whole tick budget): on a slow Jev tick
the taker took the slot first, and on a duel's deadline tick that was a lost deal (the duel scores 0).

Fixed: in the endgame an inside-limit offer is the duel's only legal move (`duel_jev.forced_pick`), so
the duel loop books and sends it right after `GET /api/duels`, before Jev is asked about any duel
(`tests/test_jev_journal.py::test_a_forced_endgame_accept_is_booked_and_sent_before_jev_is_asked`).

The invariant left: the settle margin, the loop's clock read, one duels read and six ledger round trips (the
guardrail's accept count, then `reserve_accept`: BEGIN, advisory lock, count, insert, COMMIT; counted on a
local Postgres statement log) against the taker's grace, measured through the taker's own code. Round trip
measured on 2026-10-03 (night): 42 ms p50 from a laptop to the shared Postgres (read-only `select 1`); inside
Railway it is unmeasured and lower. At 5 s ticks the 15 % cap leaves 0.75 s, and a laptop-run duel loop needs
about 0.95 s: still a race. r2's original constant-only invariant (8dd89f7) is replaced by this one.
"""

import pytest

from bazaar_agent.ticks import AFTER_TICK_S
from tests.agent_fakes import FakePublic, FakeTeam, ask, clock
from tests.test_taker import taker

CLOCK_READ_S = 0.2  # run_per_tick's GET /api/clock after the settle margin; optimistic
DUELS_READ_S = 0.2  # one GET /api/duels; optimistic
LEDGER_RTT_S = 0.042  # one Postgres round trip, laptop → shared DB, p50 (the worst place the loop runs)
LEDGER_ROUND_TRIPS = 6  # accepts_in_tick (1) + reserve_accept (5)
DUEL_BOOKS_AT = AFTER_TICK_S + CLOCK_READ_S + DUELS_READ_S + LEDGER_ROUND_TRIPS * LEDGER_RTT_S  # ≈ 0.95 s


@pytest.mark.parametrize(
    "tick_seconds",
    [
        60.0,
        30.0,
        15.0,
        10.0,
        pytest.param(
            5.0,
            marks=pytest.mark.xfail(
                strict=True,
                reason="residual X17: 5 s ticks cap the grace at 0.75 s, a laptop-run duel loop books ≈ 0.95 s",
            ),
        ),
    ],
)
def test_the_taker_waits_longer_than_the_duel_loop_takes_to_book_a_forced_accept(tmp_path, tick_seconds):
    now = clock(next_tick_in=tick_seconds, tick_seconds=tick_seconds)  # the taker starts as the tick lands
    team = FakeTeam()
    team.now = now
    t, _, _ = taker(tmp_path, team, FakePublic(boards={"rastro": [ask(1, "LAV-02", 10)]}), live=True)
    slept: list[float] = []
    t.sleep = slept.append
    t.on_tick(now)
    grace = sum(slept)  # Taker._duel_grace: how far into the tick its first accept waits
    assert team.sent == [("accept", 1)]
    assert grace >= DUEL_BOOKS_AT, f"taker takes the slot at {grace:.2f} s, the duel books it at {DUEL_BOOKS_AT:.2f} s"
