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
