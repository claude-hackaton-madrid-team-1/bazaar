"""B12: seeded step jitter for dealer bids. Every hard rule of the ladder holds for every seed."""

from __future__ import annotations

import random
from dataclasses import replace

import pytest

from bazaar_agent.agents import dealer as dealer_mod
from bazaar_agent.agents.dealer import BidPlan, Negotiation, StepJitter, bid_schedule, decide, make_jitter
from bazaar_agent.agents.taker import Taker, TakerConfig
from bazaar_agent.jitter_eval import (
    OFF,
    Level,
    StepCapped,
    dealer_min_step,
    play_capped,
    predictability,
    silent_sequences,
)
from bazaar_agent.ladder_replay import Episode, play
from bazaar_agent.strategy import dealer_jitter, load_strategy
from tests.agent_fakes import FakePublic, FakeTeam, clock, parts
from tests.test_strategy import PARAMS

SEEDS = range(1, 1501)
PLANS = (BidPlan(21, 1, 25), BidPlan(8, 1, 12), BidPlan(89, 1, 93), BidPlan(17, 1, 26), BidPlan(3, 2, 30))
JITTERS = (
    dict(start_spread=2, jump_share=0.5, band_jump_share=0.0, jump_max=3, min_step=1),
    dict(start_spread=3, jump_share=0.5, band_jump_share=0.2, jump_max=4, min_step=1),
    dict(start_spread=10, jump_share=1.0, band_jump_share=1.0, jump_max=20, min_step=2),
    dict(start_spread=0, jump_share=0.0, band_jump_share=0.35, jump_max=4, min_step=3),
    dict(start_spread=2, jump_share=1.0, band_jump_share=0.5, jump_max=2, min_step=1, band_gap=3),
)


def jittered(plan: BidPlan, seed: int, **knobs: object) -> BidPlan:
    return replace(plan, jitter=StepJitter(seed, **knobs))  # type: ignore[arg-type]


def schedule(plan: BidPlan, salt: str = "") -> list[int]:
    neg, out = Negotiation(plan, salt=salt), []
    while (move := decide(neg, None, None, False)).kind == "bid" and move.price is not None:
        neg.bids.append(move.price)
        out.append(move.price)
    return out


# ---------------------------------------------------------------- off by default = today


def test_no_jitter_is_todays_ladder():
    assert bid_schedule(BidPlan(21, 1, 25)) == [21, 22, 23, 24, 25]
    assert bid_schedule(BidPlan(3, 4, 12)) == [3, 7, 11, 12]
    off = dict(start_spread=0, jump_share=0.0, band_jump_share=0.0, jump_max=3, band_gap=2, min_step_pct=0.02, seed=7)
    assert make_jitter(max_price=25, **off) is None  # type: ignore[arg-type]


def test_strategy_md_ships_with_the_jitter_off():
    params = load_strategy().params
    assert dealer_jitter(params, 25) is None and dealer_jitter(PARAMS, 93) is None


def test_the_knobs_build_a_jitter_with_the_dealer_minimum_step():
    on = PARAMS.model_copy(update={"dealer_jitter_band_jump_share": 0.2, "dealer_jitter_seed": 42})
    assert dealer_jitter(on, 25) == StepJitter(42, 0, 0.0, 0.2, 3, 1)
    jitter = dealer_jitter(on, 93)
    assert jitter is not None and jitter.min_step == 2  # 2 % of 93, rounded up
    assert dealer_jitter(on.model_copy(update={"dealer_min_step_pct": 0.0}), 93).min_step == 1  # type: ignore[union-attr]


def test_seed_zero_draws_the_process_seed_never_a_committed_one():
    on = PARAMS.model_copy(update={"dealer_jitter_start_spread": 2})
    jitter = dealer_jitter(on, 25)
    assert jitter is not None and jitter.seed == dealer_mod.PROCESS_SEED != 0


@pytest.mark.parametrize("bad", [dict(start_spread=-1), dict(jump_share=1.5), dict(jump_max=0), dict(min_step=0)])
def test_bad_knobs_are_refused(bad):
    with pytest.raises(ValueError):
        StepJitter(1, **bad)  # type: ignore[arg-type]


# ---------------------------------------------------------------- the ladder's invariants, every seed


@pytest.mark.parametrize("knobs", JITTERS)
@pytest.mark.parametrize("plan", PLANS)
def test_every_jittered_ladder_keeps_the_hard_rules(plan, knobs):
    base = max(plan.step, int(knobs["min_step"]))  # type: ignore[call-overload]
    top = max(base, int(knobs["jump_max"]))  # type: ignore[call-overload]
    seen = set()
    for seed in SEEDS:
        bids = schedule(jittered(plan, seed, **knobs), salt=str(seed % 7))
        seen.add(tuple(bids))
        assert plan.start - int(knobs["start_spread"]) <= bids[0] <= plan.start and bids[0] >= 1  # type: ignore[call-overload]
        assert bids[-1] == plan.max_price  # a silent dealer: we climb all the way to our max, never past it
        assert all(1 <= b <= plan.max_price for b in bids)
        for last, nxt in zip(bids, bids[1:], strict=False):
            raise_by = nxt - last
            assert raise_by > 0  # strictly rising: never the same price twice
            assert raise_by >= base or nxt == plan.max_price  # never under the dealer's minimum, but the cap
            assert raise_by <= top
            if last < plan.start and raise_by > base:
                assert nxt <= plan.start  # a jump below the band never lands inside it
    assert len(seen) > 1 or plan.max_price - plan.start <= 1


def test_without_band_jumps_the_band_is_climbed_by_the_base_step():
    for seed in SEEDS:
        bids = schedule(jittered(BidPlan(21, 1, 25), seed, start_spread=3, jump_share=1.0, jump_max=5))
        inside = [b for b in bids if b >= 21]
        assert inside == list(range(inside[0], 26))


def test_the_draws_are_a_function_of_seed_salt_and_bid_index():
    plan = jittered(BidPlan(17, 1, 40), 99, start_spread=3, jump_share=0.5, band_jump_share=0.5, jump_max=4)
    assert schedule(plan, "5000") == schedule(plan, "5000")
    distinct = {tuple(schedule(plan, str(tid))) for tid in range(5000, 5200)}
    assert len(distinct) > 150  # each thread draws its own ladder
    # deciding twice on the same state (a tick skipped before sending) sends the same bid
    neg = Negotiation(plan, salt="5000")
    for _ in range(6):
        first, again = decide(neg, None, None, False), decide(neg, None, None, False)
        assert first == again and first.price is not None
        neg.bids.append(first.price)


class RandomDealer:
    """A dealer that answers anything: random asks (sometimes inside our max, sometimes not), random
    finals, never rising. It checks the rules of `decide()` move by move, not a fitted behaviour."""

    def __init__(self, rng: random.Random, plan: BidPlan) -> None:
        self.rng = rng
        self.opening = plan.max_price + rng.randint(-3, 8)
        self.ask: int | None = self.opening if rng.random() < 0.5 else None
        self.final = False

    def answer(self) -> None:
        if self.ask is None:
            self.ask = self.opening
        elif self.rng.random() < 0.7:
            self.ask = max(1, self.ask - self.rng.randint(0, 3))
        self.final = self.final or self.rng.random() < 0.15


@pytest.mark.parametrize("knobs", JITTERS)
def test_decide_keeps_the_dealer_rules_with_a_jitter(knobs):
    for seed in SEEDS:
        rng = random.Random(seed)
        plan = jittered(rng.choice(PLANS), seed, **knobs)
        neg, dealer = Negotiation(plan, salt=str(seed)), RandomDealer(rng, plan)
        for _ in range(20):
            ask, final = dealer.ask, dealer.final
            move = decide(neg, ask, 1 if ask is not None else None, final)
            last = neg.bids[-1] if neg.bids else 0
            if final and ask is not None:
                # a final is take-it-or-walk: inside our max we take it, above it we walk
                assert (move.kind, move.price) == (("accept", ask) if ask <= plan.max_price else ("walk", None))
                break
            if move.kind == "accept":
                assert move.price == ask and ask is not None and ask <= plan.max_price
                # never at her opening ask, unless no whole price is left between our last bid and her ask
                assert neg.may_close or last + 1 >= ask
                break
            if move.kind == "walk":
                assert last == plan.max_price or (neg.next_bid() is None)
                break
            assert move.kind == "bid" and move.price is not None
            assert last < move.price <= plan.max_price  # strictly rising, inside the max
            if ask is not None:
                assert move.price < ask  # a bid at or above her ask would just pay her ask
            neg.bids.append(move.price)
            dealer.answer()


def test_against_fitted_and_capped_dealers_no_price_repeats_and_none_above_max():
    level = Level("all", start_spread=3, jump_share=0.5, band_jump_share=0.35, jump_max=4)
    rng = random.Random(3)
    for i in range(2000):
        opening = rng.randint(10, 40)
        ep = Episode(
            opening=opening,
            limit=rng.randint(max(1, opening - 12), opening - 1),
            patience=rng.randint(2, 8),
            first_drop=rng.randint(0, 5),
            later_drops=tuple(rng.randint(0, 2) for _ in range(8)),
            opens_first=rng.random() < 0.5,
            matches_moves=rng.random() < 0.3,
        )
        plan = level.plan(BidPlan(max(1, ep.limit - 2), 1, ep.limit + 2), i + 1)
        for r in (play(plan, ep), play_capped(plan, ep, rng.randint(1, 3))):
            assert not r.repeated
            assert all(b <= plan.max_price for b in r.bids)
            assert r.price is None or r.price <= plan.max_price
            if r.how == "her_ask":
                assert r.price != opening or r.bids[-1] + 1 >= opening


# ---------------------------------------------------------------- the desk


def test_the_desk_draws_a_jitter_per_thread_when_strategy_md_turns_it_on(tmp_path):
    on = PARAMS.model_copy(update={"dealer_jitter_start_spread": 3, "dealer_jitter_seed": 11})
    kw = parts(tmp_path) | {"params": lambda tick: on}
    team = FakeTeam()
    t = Taker(team, FakePublic(), live=True, log=lambda s: None, now=lambda: 1000.0, sleep=lambda s: None,
              config=TakerConfig(max_dealer_threads=3), **kw)  # fmt: skip
    t.on_tick(clock())
    conv = t.convs["abuela"]
    assert conv.neg.plan.jitter == StepJitter(11, 3, 0.0, 0.0, 3, 1) and conv.neg.salt == str(conv.thread_id)
    (said,) = [s for s in team.sent if s[0] == "say"]
    assert said[2] == conv.neg.plan.jitter.first_bid(conv.neg.plan, conv.neg.salt) <= conv.neg.plan.start


def test_the_desk_keeps_todays_ladder_with_the_knobs_off(tmp_path):
    team = FakeTeam()
    t = Taker(team, FakePublic(), live=True, log=lambda s: None, now=lambda: 1000.0, sleep=lambda s: None,
              config=TakerConfig(max_dealer_threads=3), **parts(tmp_path))  # fmt: skip
    t.on_tick(clock())
    assert t.convs["abuela"].neg.plan.jitter is None


# ---------------------------------------------------------------- the evaluation harness


def test_a_capped_dealer_never_concedes_more_than_our_step_nor_below_the_minimum():
    ep = Episode(opening=29, limit=15, patience=9, first_drop=5, later_drops=(6,), opens_first=False)
    d = StepCapped(ep, min_step=2)
    assert d.answer(10) is None and d.ask == 29  # our first price: she answers with her opening
    assert d.answer(11) is None and d.ask == 29  # a step of 1 is under the minimum of 2: nothing
    assert d.answer(14) is None and d.ask == 26  # a step of 3: her drop of 6, capped at 3
    assert d.answer(26) == "deal"  # she takes a bid at her standing ask


def test_the_dealer_minimum_step_is_two_percent_of_book():
    assert dealer_min_step("abuela", "card:uncommon") == 1 and dealer_min_step("chato", "card:rare") == 2


def test_predictability_is_perfect_on_todays_ladder_and_falls_with_jitter():
    plan = BidPlan(17, 1, 26)
    fixed = [("u", s) for s in silent_sequences(plan, OFF, 200, 1)]
    assert predictability(fixed[:100], fixed[100:]).hit_rate == 1.0
    noisy = Level("j", start_spread=3, jump_share=0.5, band_jump_share=0.35, jump_max=4)
    seqs = [("u", s) for s in silent_sequences(plan, noisy, 400, 1)]
    p = predictability(seqs[:200], seqs[200:])
    assert p.hit_rate < 0.9 and p.first_hit_rate < 0.5 and p.step_entropy_bits > 0.3


def test_the_seed_never_shows_in_a_log_line_or_a_trace():
    plan = BidPlan(21, 1, 25, jitter=StepJitter(987654321, start_spread=2))
    assert "987654321" not in repr(plan) and "987654321" not in str(plan.__dict__)


def test_counter_below_never_steps_past_the_dealer_minimum_and_keeps_its_plus_one():
    plan = jittered(BidPlan(89, 1, 93), 5, min_step=2)
    # her opening 90 is inside our max and our next bid (90) meets it, but she has not come down yet:
    # one under her ask a +1 still counters (no concession earned, but a bid at her limit is taken)
    assert decide(Negotiation(plan, bids=[88]), 90, 7, False).price == 89
    # a jump past her unconceded ask counters at ask − base step, never above it
    jumpy = jittered(BidPlan(80, 1, 99), 5, band_jump_share=1.0, jump_max=9, min_step=2)
    neg = Negotiation(jumpy, bids=[80], salt="t")
    nxt = neg.next_bid()
    assert nxt is not None and nxt > 84
    assert decide(neg, 84, 7, False).price == 82


def test_a_band_gap_allows_a_jump_only_while_her_ask_is_far_above_where_it_lands():
    always = jittered(BidPlan(20, 1, 40), 3, band_jump_share=1.0, jump_max=3, band_gap=3)
    for salt in map(str, range(200)):
        far, near = Negotiation(always, bids=[22], salt=salt), Negotiation(always, bids=[22], salt=salt)
        far.see_ask(35)
        near.see_ask(26)
        assert far.next_bid() in (24, 25)  # a jump: her ask 35 is at least 3 above 24 or 25
        assert near.next_bid() == 23  # 24 or 25 would land within 3 of her ask 26: the base step
        assert Negotiation(always, bids=[22], salt=salt).next_bid() == 23  # no ask seen yet: no jump


def test_a_below_start_jump_alone_changes_nothing_so_it_stays_off():
    alone = dict(start_spread=0, jump_share=1.0, band_jump_share=0.0, jump_max=3, band_gap=0, min_step_pct=0.02)
    assert make_jitter(seed=7, max_price=93, **alone) is None  # type: ignore[arg-type]


def test_a_below_start_jump_never_lands_on_or_near_her_ask():
    plan = jittered(BidPlan(21, 1, 25), 3, start_spread=6, jump_share=1.0, jump_max=6, band_gap=2)
    for salt in map(str, range(300)):
        neg = Negotiation(plan, bids=[15], salt=salt)
        neg.see_ask(19)  # she came down under our start
        nxt = neg.next_bid()
        assert nxt is not None and nxt <= 17  # at most her ask − gap, never the start 21


def test_a_rival_reading_her_asks_predicts_gap_gated_jumps_better():
    plan = BidPlan(20, 1, 40)
    level = Level("gap", band_jump_share=0.9, jump_max=2, band_gap=3)
    rng = random.Random(5)
    seqs, asks = [], []
    for i in range(1200):
        opening = rng.randint(30, 36)
        ep = Episode(opening, rng.randint(22, 28), 9, rng.randint(1, 4), (1, 0, 2), True)
        seen: list[int | None] = []
        r = play_capped(level.plan(plan, i + 1), ep, 1, asks=seen)
        seqs.append(("u", list(r.bids)))
        asks.append(seen)
        assert len(seen) == len(r.bids)
    blind = predictability(seqs[:600], seqs[600:])
    reader = predictability(seqs[:600], seqs[600:], train_asks=asks[:600], test_asks=asks[600:])
    assert reader.hit_rate > blind.hit_rate + 0.05
