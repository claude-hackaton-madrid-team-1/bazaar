"""Per-trader beliefs on the Market Test bench: limit bands from quotes, leave hazards from the patience prior."""

import pytest

from bazaar_agent.agents.bench_model import PRIORS, BenchPrior, TraderModel


def seller(*quotes, first_tick=10, prior=PRIORS["normal"]):
    t = TraderModel("b1-0", "sell", first_tick, prior)
    for i, q in enumerate(quotes):
        t.observe(first_tick + i, q)
    return t


def buyer(*quotes, first_tick=10, prior=PRIORS["normal"]):
    t = TraderModel("b1-1", "buy", first_tick, prior)
    for i, q in enumerate(quotes):
        t.observe(first_tick + i, q)
    return t


def test_the_patience_prior_is_a_distribution():
    for prior in PRIORS.values():
        assert sum(prior.patience_pmf(t) for t in range(0, 10)) == pytest.approx(1.0)


def test_hazard_by_age_under_the_normal_prior():
    # impatient 25 % (1–2 ticks), patient 75 % (3–6 ticks)
    hazards = [PRIORS["normal"].hazard(age) for age in range(1, 8)]
    assert hazards == pytest.approx([0.125, 0.125 / 0.875, 0.25, 1 / 3, 0.5, 1.0, 1.0])


def test_hard_traders_leave_sooner():
    assert PRIORS["hard"].hazard(1) > PRIORS["normal"].hazard(1)


def test_a_sellers_cost_band_comes_from_its_first_ask_and_never_exceeds_an_ask_it_showed():
    t = seller(130)
    assert t.band() == pytest.approx((100.0, 130 / 1.05))
    t.observe(11, 110)
    assert t.band() == pytest.approx((100.0, 110.0))
    assert t.limit() == pytest.approx(105.0)
    assert t.relaxing and t.quote == 110


def test_a_buyers_value_band_never_falls_below_a_bid_it_showed():
    t = buyer(75)
    assert t.band() == pytest.approx((75 / 0.95, 100.0))
    t.observe(11, 90)
    assert t.band() == pytest.approx((90.0, 100.0))
    assert t.relaxing


def test_a_trader_that_breaks_its_prior_is_trusted_only_for_what_it_showed():
    t = seller(130, 90)  # an ask under the lowest cost the prior allows
    assert t.band() == (90.0, 90.0)
    assert t.p_limit_below(90) == 1.0 and t.p_limit_below(89) == 0.0


def test_a_second_read_in_one_tick_replaces_the_quote():
    t = seller(130)
    t.observe(10, 128)
    assert t.quotes == [(10, 128)] and t.age(10) == 1 and t.age(12) == 3


def test_a_firm_trader_does_not_look_relaxing():
    assert not seller(50, 50, 50).relaxing and not buyer(40, 40).relaxing


def test_acceptance_chances_are_probabilities_and_monotone():
    s, b = seller(130), buyer(75)
    below = [s.p_limit_below(p) for p in range(90, 140)]
    above = [b.p_limit_above(p) for p in range(70, 110)]
    assert below == sorted(below) and above == sorted(above, reverse=True)
    assert below[0] == 0.0 and below[-1] == 1.0 and above[0] == 1.0 and above[-1] == 0.0


def test_the_widened_prior_keeps_the_patience_and_widens_the_bands():
    wide = BenchPrior().widened()
    assert wide.seller_markup == (1.0, 1.6) and wide.buyer_shade == (0.5, 1.0)
    assert wide.hazard(3) == BenchPrior().hazard(3)
    assert seller(130, prior=wide).band() == pytest.approx((130 / 1.6, 130.0))
