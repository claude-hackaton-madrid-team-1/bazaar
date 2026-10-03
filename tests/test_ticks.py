import time
from datetime import UTC, datetime

from bazaar_agent.ticks import (
    AFTER_TICK_S,
    CLOSED_POLL_MAX_S,
    CLOSED_POLL_MIN_S,
    PAUSED_POLL_S,
    Clock,
    action_budget_s,
    run_per_tick,
    seconds_until_next_tick,
)


def clock(**kw):
    base = {"tick": 10, "tick_seconds": 60.0, "next_tick_in": 20.0, "paused": False, "doors": "open"}
    return Clock.model_validate({**base, **kw})


def test_sleeps_just_past_the_next_tick():
    assert seconds_until_next_tick(clock(next_tick_in=12.0)) == 12.0 + AFTER_TICK_S


def test_paused_and_closed_poll_slowly_instead_of_spinning():
    assert seconds_until_next_tick(clock(paused=True)) == PAUSED_POLL_S
    assert seconds_until_next_tick(clock(doors="closed")) == CLOSED_POLL_MAX_S


# B13 (bite X4): the doors open at 09:00; a closed clock sleeps until then, not a blind 300 s.
OPENS = "2026-10-03T09:00:00+02:00"
OPENS_EPOCH = datetime.fromisoformat(OPENS).timestamp()


def closed(**kw):
    return clock(**{"doors": "closed", "paused": True, "next_tick_in": 0.0, "next_opens": OPENS, **kw})


def test_closed_doors_sleep_until_just_after_the_announced_opening():
    assert seconds_until_next_tick(closed(), now=OPENS_EPOCH - 20) == 20 + AFTER_TICK_S
    assert seconds_until_next_tick(closed(), now=OPENS_EPOCH - 0.1) == CLOSED_POLL_MIN_S  # floor: no spin
    assert seconds_until_next_tick(closed(), now=OPENS_EPOCH - 8 * 3600) == CLOSED_POLL_MAX_S  # overnight: cap


def test_closed_doors_past_the_announced_opening_poll_like_a_pause():
    """Our clock ahead of the server's, or a late opening: poll every 5 s, never every 1 s on one key."""
    assert seconds_until_next_tick(closed(), now=OPENS_EPOCH + 1) == PAUSED_POLL_S
    assert seconds_until_next_tick(closed(), now=OPENS_EPOCH + 600) == PAUSED_POLL_S


def test_closed_doors_inside_a_day_window_poll_like_a_pause_whatever_next_opens_says():
    """If `next_opens` already points to the next day while the doors are still closed at 09:00."""
    days = [{"day": "sat", "opens": OPENS, "closes": "2026-10-03T23:00:00+02:00"}]
    rolled = closed(next_opens="2026-10-04T09:00:00+02:00", days=days)
    assert seconds_until_next_tick(rolled, now=OPENS_EPOCH + 2) == PAUSED_POLL_S
    assert seconds_until_next_tick(rolled, now=OPENS_EPOCH - 60) == 60 + AFTER_TICK_S  # the earliest opening
    assert seconds_until_next_tick(rolled, now=OPENS_EPOCH + 14 * 3600) == CLOSED_POLL_MAX_S  # after 23:00


def test_closed_doors_without_a_usable_opening_keep_the_slow_poll():
    for bad in (None, "", "09:00", "2026-10-03T09:00:00", "not a date"):  # naive stamps have no zone: ignored
        assert seconds_until_next_tick(closed(next_opens=bad), now=OPENS_EPOCH - 20) == CLOSED_POLL_MAX_S
    odd_days = closed(next_opens=None, days=["sat", {"opens": 3}, {"opens": "09:00", "closes": None}])
    assert seconds_until_next_tick(odd_days, now=OPENS_EPOCH + 2) == CLOSED_POLL_MAX_S
    assert (
        seconds_until_next_tick(closed(next_opens=None, days=[{"opens": OPENS}]), now=OPENS_EPOCH - 9)
        == 9 + AFTER_TICK_S
    )
    assert seconds_until_next_tick(closed(days="sat"), now=OPENS_EPOCH - 20) == 20 + AFTER_TICK_S


def test_run_per_tick_wakes_for_the_opening_tick():
    opens_in_10s = datetime.fromtimestamp(time.time() + 10, UTC).isoformat()
    reads = iter([closed(next_opens=opens_in_10s).model_dump(), {"tick": 160, "next_tick_in": 29.0}])
    slept: list[float] = []
    seen: list[int] = []
    run_per_tick(lambda: next(reads), lambda c: seen.append(c.tick), max_ticks=1, sleep=slept.append)
    assert seen == [160] and CLOSED_POLL_MIN_S <= slept[0] <= 10 + AFTER_TICK_S


def test_action_budget_keeps_a_margin_that_scales_with_fast_ticks():
    assert action_budget_s(clock(next_tick_in=20.0)) == 18.0  # 60 s tick: 2 s margin
    assert action_budget_s(clock(tick_seconds=15.0, next_tick_in=10.0)) == 8.0  # Sunday: still 2 s
    assert action_budget_s(clock(tick_seconds=5.0, next_tick_in=4.0)) == 4.0 - 5.0 * 0.15  # fastest pace
    assert action_budget_s(clock(next_tick_in=1.0)) == 0.0  # too late: drop, do not send late
    assert action_budget_s(clock(paused=True)) == 0.0


def test_limits_default_to_the_published_rules_and_accept_overrides():
    c = clock(limits={"accepts_per_team_per_tick": 2, "brand_new_limit": 3})
    assert c.limits.accepts_per_team_per_tick == 2
    assert c.limits.offers_per_team_per_tick == 12


def test_run_per_tick_handles_each_live_tick_once():
    clocks = iter(
        [
            {"tick": 1, "next_tick_in": 5},
            {"tick": 1, "next_tick_in": 1},
            {"tick": 2, "next_tick_in": 5},
            {"tick": 2, "paused": True, "next_tick_in": 5},
            {"tick": 3, "next_tick_in": 5},
        ]
    )
    seen, sleeps = [], []
    n = run_per_tick(lambda: next(clocks), lambda c: seen.append(c.tick), max_ticks=3, sleep=sleeps.append)
    assert (n, seen) == (3, [1, 2, 3])
    assert PAUSED_POLL_S in sleeps


def test_a_network_failure_on_the_clock_is_retried_with_backoff_not_fatal():
    from bazaar_agent.ticks import ERROR_BACKOFF_MAX_S

    reads = iter([OSError("nodename nor servname provided"), OSError("again"), {"tick": 5, "next_tick_in": 1}])

    def read():
        item = next(reads)
        if isinstance(item, Exception):
            raise item
        return item

    seen, sleeps, errors = [], [], []
    n = run_per_tick(
        read,
        lambda c: seen.append(c.tick),
        max_ticks=1,
        sleep=sleeps.append,
        on_error=lambda stage, e: errors.append(stage),
    )
    assert (n, seen, errors) == (1, [5], ["clock read", "clock read"])
    assert sleeps[:2] == [1.0, 2.0] and max(sleeps) <= ERROR_BACKOFF_MAX_S


def test_a_failing_tick_is_reported_once_and_the_loop_goes_on():
    clocks = iter([{"tick": 1, "next_tick_in": 1}, {"tick": 1, "next_tick_in": 1}, {"tick": 2, "next_tick_in": 1}])

    def on_tick(c):
        if c.tick == 1:
            raise RuntimeError("boom")
        seen.append(c.tick)

    seen, errors = [], []
    n = run_per_tick(
        lambda: next(clocks), on_tick, max_ticks=2, sleep=lambda _: None, on_error=lambda stage, e: errors.append(stage)
    )
    assert (n, seen, errors) == (2, [2], ["tick 1"])  # tick 1 not retried within the same tick


def test_ctrl_c_still_stops_the_loop():
    import pytest

    def read():
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        run_per_tick(read, lambda c: None, sleep=lambda _: None)
