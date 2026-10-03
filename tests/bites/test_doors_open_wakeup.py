"""BITE X4: with the doors closed, every loop polls the clock every 300 s, whatever `next_opens` says.

`ticks.seconds_until_next_tick` returns `CLOSED_POLL_MAX_S` (300 s) while `doors != "open"`, and every agent
(taker, maker, duels, monitor, capture, evals) runs through `run_per_tick`. The Railway taker and maker
have been live and waiting since 01:45: the poll before 09:00 can land at 08:59:59 and the next one at
09:04:59, so the first 0-10 Saturday ticks (30 s) pass without us (mean 5), right when the 150 P grant,
El Retiro and the 09:00 ladder/trade plans land. Fixed by B13: the closed-doors sleep ends at `next_opens`
(the strict xfail markers are dropped).
"""

from datetime import UTC, datetime, timedelta

from bazaar_agent.ticks import Clock, run_per_tick, seconds_until_next_tick


def closed_clock(opens_in_s: float) -> dict:
    opens = datetime.now(UTC) + timedelta(seconds=opens_in_s)
    return {
        "tick": 159,
        "t_hours": 2.65,
        "tick_seconds": 60.0,
        "next_tick_in": 0.0,
        "paused": False,
        "doors": "closed",
        "next_opens": opens.isoformat(),
    }


def test_closed_doors_sleep_ends_at_the_next_opening():
    assert seconds_until_next_tick(Clock.model_validate(closed_clock(opens_in_s=20))) <= 21


def test_the_loop_wakes_for_the_opening_tick():
    reads = [closed_clock(opens_in_s=1), {**closed_clock(0), "doors": "open", "tick": 160, "next_tick_in": 29.0}]
    slept: list[float] = []
    handled: list[int] = []
    run_per_tick(
        lambda: reads.pop(0) if len(reads) > 1 else reads[0],
        lambda c: handled.append(c.tick),
        max_ticks=1,
        sleep=slept.append,
    )
    assert handled == [160]
    assert slept[0] <= 2, f"slept {slept[0]} s before reading the clock again across the 09:00 opening"
