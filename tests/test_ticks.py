from bazaar_agent.ticks import (
    AFTER_TICK_S,
    CLOSED_POLL_MAX_S,
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


def test_the_opt_in_stagger_wakes_later_but_never_past_forty_percent_of_the_tick():
    assert seconds_until_next_tick(clock(next_tick_in=12.0), 4.0) == 12.0 + AFTER_TICK_S + 4.0
    assert seconds_until_next_tick(clock(tick_seconds=15.0, next_tick_in=12.0), 9.0) == 12.0 + AFTER_TICK_S + 6.0
    assert seconds_until_next_tick(clock(paused=True), 4.0) == PAUSED_POLL_S


def test_the_stagger_is_off_unless_the_service_sets_it():
    import pytest

    from bazaar_agent.ticks import TICK_OFFSET_ENV, tick_offset_from_env

    assert tick_offset_from_env({}) == 0.0 and tick_offset_from_env({TICK_OFFSET_ENV: " "}) == 0.0
    assert tick_offset_from_env({TICK_OFFSET_ENV: "2.5"}) == 2.5
    for bad in ("-1", "nan", "inf", "soon"):
        with pytest.raises(ValueError, match=TICK_OFFSET_ENV):
            tick_offset_from_env({TICK_OFFSET_ENV: bad})


def test_run_per_tick_sleeps_the_offset_on_top_of_the_tick(monkeypatch):
    import pytest

    from bazaar_agent.ticks import TICK_OFFSET_ENV

    clocks = iter([{"tick": 1, "next_tick_in": 5}, {"tick": 2, "next_tick_in": 5}])
    today, staggered = [], []
    run_per_tick(lambda: next(clocks), lambda c: None, max_ticks=2, sleep=today.append)
    monkeypatch.setenv(TICK_OFFSET_ENV, "4")
    clocks = iter([{"tick": 1, "next_tick_in": 5}, {"tick": 2, "next_tick_in": 5}])
    run_per_tick(lambda: next(clocks), lambda c: None, max_ticks=2, sleep=staggered.append)
    assert len(today) == len(staggered) == 1
    assert staggered[0] - today[0] == pytest.approx(4.0, abs=0.05)  # minus the (tiny) work time
    assert run_per_tick(lambda: {"tick": 3, "next_tick_in": 5}, lambda c: None, max_ticks=1, start_offset_s=0.0) == 1
