"""Replaying a duel policy on the real practice payloads, and labelling each real rival with a zoo style."""

import pytest

from bazaar_sim import duel_replay as replay
from bazaar_sim import duel_zoo as zoo

UNANSWERED = [5, 6, 23, 24, 95, 119, 120, 131, 132, 147, 148, 201]


def by_id() -> dict[int, dict]:
    return {d["duel"]: d for d in replay.load()}


def test_the_twelve_unanswered_duels_are_the_ones_we_never_spoke_in():
    assert [d["duel"] for d in replay.load() if replay.unanswered(d)] == UNANSWERED


def test_a_silent_player_could_have_taken_195_p_on_them():
    rows = by_id()
    assert sum(replay.oracle(rows[i]) for i in UNANSWERED) == 195.0  # the night plan's figure
    assert replay.oracle(rows[6]) == 42.0  # buyer, value 152: the rival's last ask was 110


def test_accepting_in_the_endgame_takes_the_rivals_late_price_with_no_round():
    out = {r.duel: r for r in replay.replay_all(zoo.endgame_accept)}
    assert set(out) == set(UNANSWERED)
    six = out[6].record
    assert (six.status, six.price, six.rounds, six.result) == ("deal", 112, 0, 40.0)  # 112 at tick 130
    assert out[95].record.status == "no_deal"  # its only offer (98) was below our cost (104)
    assert sum(r.result for r in out.values()) == 185.0


def test_talking_costs_rounds_against_the_recorded_path():
    def counter_every_tick(duel: dict, tick: int, started: int) -> zoo.Act:
        limit = duel["your_limit"]
        return zoo.Act("offer", limit * 2 if duel["role"] == "seller" else 1)

    six = replay.replay(counter_every_tick, by_id()[6])
    assert six.record.status == "no_deal" and six.record.our_messages == 12 and six.record.rounds == 12


def test_the_consistent_rival_takes_an_offer_that_beats_its_own_next_one():
    # Duel 6: we buy (value 152); the rival asked 141 at tick 120 and 137 at 121. Our 138 at 120 beats 137.
    once = lambda duel, tick, started: zoo.Act("offer", 138) if tick == 120 else zoo.HOLD  # noqa: E731
    calm = replay.replay(once, by_id()[6], "conservative").record
    kind = replay.replay(once, by_id()[6], "consistent").record
    assert calm.status == "no_deal"
    assert (kind.status, kind.closer, kind.price, kind.rounds) == ("deal", "rival", 138, 1)
    assert kind.result == pytest.approx(14 * 0.94)


@pytest.mark.parametrize(
    ("duel", "style"),
    [
        (23, "no_show"),
        (24, "no_show"),
        (95, "one_shot"),
        (119, "one_shot"),
        (120, "one_shot"),
        (131, "one_shot"),
        (132, "one_shot"),
        (147, "one_shot"),
        (148, "one_shot"),
        (201, "convex"),
        (5, "linear"),
        (6, "linear"),
        (273, "holdout"),
        (274, "holdout"),
        (202, "tit_for_tat"),
        (268, "tit_for_tat"),
        (255, "undetermined"),
    ],
)
def test_real_rivals_get_the_zoo_style_their_path_shows(duel, style):
    assert replay.classify(by_id()[duel]) == style


def test_features_read_a_path_relative_to_our_limit():
    f = replay.features(by_id()[6])  # buyer, value 152: 141 → 110 over 12 messages, one per tick
    assert (f.n, f.played, f.cadence) == (12, False, 1.0)
    assert f.open_gap == pytest.approx(11 / 152, abs=1e-4) and f.final_gap == pytest.approx(42 / 152, abs=1e-4)
    assert f.pace is not None and 0.015 < f.pace < 0.02
    assert replay.features(by_id()[201]).shape >= 1.6


def test_every_real_duel_gets_a_label():
    labels = {f.duel: f.label for f in replay.fit()}
    assert len(labels) == 26 and set(labels.values()) <= {*zoo.STYLES, "undetermined"}


def test_the_classifier_recognises_the_zoos_own_styles():
    """The classifier must name the style that played (≥ 80 %): silent against the unilateral styles; against
    the holdout we counter every tick, as in duels 273 and 274 (silent, a holdout looks like a one-shot)."""
    silent = lambda duel, tick, started: zoo.HOLD  # noqa: E731

    def stubborn(duel: dict, tick: int, started: int) -> zoo.Act:
        limit = duel["your_limit"]
        return zoo.Act("offer", limit * 3 - (tick - started) if duel["role"] == "seller" else 1 + tick - started)

    for style, policy in [*((s, silent) for s in ("linear", "convex", "one_shot", "no_show")), ("holdout", stubborn)]:
        grid = zoo.scenarios((style,), n=50, decays=(0.06,))
        hits = sum(replay.classify(zoo.play(policy, sc)[1]) == style for sc in grid)
        assert hits >= 0.8 * len(grid), (style, hits)


def test_the_practice_mix_counts_the_labels_and_its_two_brackets():
    assert replay.practice_mix() == {
        "convex": 1,
        "holdout": 2,
        "linear": 2,
        "no_show": 6,
        "one_shot": 7,
        "tit_for_tat": 7,
    }
    waiting = replay.practice_mix(waiting_is_tit_for_tat=True)  # 95, 119, 120 posted once and waited
    assert (waiting["one_shot"], waiting["tit_for_tat"]) == (4, 10)
    timed = replay.practice_mix(responsive_is_linear=True)  # 85, 86, 96, 202, 263, 268 conceded as we talked
    assert (timed["linear"], timed["tit_for_tat"]) == (8, 1)


def test_a_mix_weights_styles_not_duel_counts():
    def rec(style: str, result: float) -> zoo.Record:
        return zoo.Record(style, "seller", 0.06, 12, False, "deal", "team", 1, 0, 0, 0, result, result, 10, 1, 1,
                          False, 1)  # fmt: skip

    records = [rec("linear", 10.0), rec("linear", 20.0), rec("linear", 30.0), rec("one_shot", 0.0)]
    assert zoo.mix_mean(records, {"linear": 1, "one_shot": 3}) == 5.0  # 1/4 × 20 + 3/4 × 0
    assert zoo.mix_mean(records, {"linear": 1, "sim": 9}) == 20.0  # styles without records drop out
    assert zoo.mix_mean(records, {"linear": 1, "one_shot": 1}, "deal") == 1.0


def test_the_replayed_rival_keeps_the_days_it_offered():
    two = dict(by_id()[6], issues=["price", "days"], your_days_weight=-3.0)
    two["messages"] = [dict(m, days=10) for m in two["messages"]]
    seen: list[dict] = []

    def look(duel: dict, tick: int, started: int) -> zoo.Act:
        seen.append(duel["rival_offer"])
        return zoo.HOLD

    replay.replay(look, two)
    assert seen[0]["days"] == 10 and seen[0]["price"] == 141


def test_replaying_the_real_deadline_groups_with_one_accept_per_tick():
    free = {r.duel: r.result for r in replay.replay_groups(zoo.endgame_accept, accepts_per_tick=None)}
    capped = {r.duel: r for r in replay.replay_groups(zoo.endgame_accept)}
    assert sum(free.values()) == 185.0  # = replay_all: no cap, no interaction
    assert sum(r.result for r in capped.values()) == 140.0
    # Deadline 132: 5, 6, 119 and 120 all want the last two ticks; 5 and 6 come first in duel order.
    assert capped[119].record.result == capped[120].record.result == 0.0
    assert capped[119].record.errors.count("accept_cap") == 2
