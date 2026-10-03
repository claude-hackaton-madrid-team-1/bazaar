"""The duel rival zoo: the real scoring rule, the real payload shape, parity with the simulator's bot, and
rivals that never cross their own limit."""

import json
import random
from pathlib import Path

import pytest

from bazaar_sim import duel_zoo as zoo
from bazaar_sim import duels
from tests.simkit import manual_world

FIXTURE = Path(__file__).parent / "fixtures" / "evals" / "duels_done.json"
US = "t01"


def real_duels() -> list[dict]:
    return json.loads(FIXTURE.read_text())["duels"]


def priced(duel: dict, ours: bool) -> int:
    return sum(1 for m in duel["messages"] if (m["from"] == "you") == ours and m["price"] is not None)


# ---------------------------------------------------------------- the real rules the engine follows


def test_rounds_are_the_fewer_priced_messages_of_the_two_sides_on_every_real_duel():
    rows = real_duels()
    assert len(rows) == 26
    for d in rows:
        assert d["rounds"] == min(priced(d, True), priced(d, False)), d["duel"]


def test_the_result_is_our_surplus_times_the_decay_per_round_on_every_real_deal():
    deals = [d for d in real_duels() if d["status"] == "deal"]
    assert len(deals) == 8
    for d in deals:
        surplus = abs(d["price"] - d["your_limit"])
        assert d["result"] == pytest.approx(surplus * (1 - d["decay_per_round"]) ** d["rounds"], abs=0.05), d["duel"]


class Script:
    """A policy that sends a fixed list of moves, one per tick, then holds."""

    def __init__(self, *moves: zoo.Act) -> None:
        self.moves = list(moves)

    def __call__(self, duel: dict, tick: int, started: int) -> zoo.Act:
        return self.moves.pop(0) if self.moves else zoo.HOLD


def scenario(**over) -> zoo.Scenario:
    base = dict(role="seller", limit=100, rival_limit=150, style="sim", duel_ticks=12, started_tick=100, seed=1)
    return zoo.Scenario(**(base | over))


def test_the_payload_has_the_real_rows_key_set_and_shapes():
    real = next(d for d in real_duels() if d["duel"] == 273)
    record, final = zoo.play(Script(zoo.Act("offer", 106)), scenario())
    assert set(final) == set(real)
    assert set(final["your_offer"]) == set(real["your_offer"]) and set(final["rival_offer"]) == set(real["rival_offer"])
    assert all(set(m) == set(real["messages"][0]) for m in final["messages"])
    assert final["status"] == record.status == "deal" and isinstance(final["result"], float)
    assert final["days_meaning"] is None and final["days"] == 0
    assert all(m["days"] is None for m in final["messages"])  # price-only: days travel as null


def test_a_finished_deal_scores_like_the_real_game():
    # The sim bot bids 105 at tick 100 and would bid 107 at 101: our 106 beats that (44 P of its 50 P pie vs 43),
    # so it accepts at once by echoing our price: one priced message each side, one round.
    record, final = zoo.play(Script(zoo.Act("offer", 106)), scenario())
    assert (record.status, record.closer, record.price, record.rounds) == ("deal", "rival", 106, 1)
    assert record.result == pytest.approx(6 * 0.94) and final["result"] == record.result
    assert record.share == pytest.approx(6 / 50) and record.score == pytest.approx(0.12 * 0.94, abs=1e-4)


def test_an_accept_settles_on_the_next_tick_and_costs_no_round():
    record, final = zoo.play(Script(zoo.Act("accept")), scenario(style="linear", params=LINEAR))
    assert record.status == "deal" and record.closer == "team" and record.rounds == 0
    assert record.close_tick == 101 and record.result == record.gain


def test_an_open_duel_closes_with_no_deal_at_its_deadline():
    record, final = zoo.play(Script(), scenario(style="no_show", duel_ticks=16))
    assert (record.status, record.close_tick, record.result, final["result"]) == ("no_deal", 116, 0.0, 0.0)
    assert final["messages"] == []


def test_a_two_issue_offer_without_days_is_refused_and_costs_nothing():
    sc = scenario(two_issues=True, days_weight=2.0, rival_days_weight=-1.0, style="no_show")
    record, final = zoo.play(Script(zoo.Act("offer", 160)), sc)
    assert record.errors == ("missing_days",) and record.our_messages == 0 and final["messages"] == []
    assert final["issues"] == ["price", "days"] and final["your_days_weight"] == 2.0


def test_days_are_valued_signed_or_at_the_worst_case():
    sc = scenario(two_issues=True, days_weight=-2.0, rival_days_weight=1.0)
    assert zoo.our_gain(sc, 120, 3) == 20 - 6
    assert zoo.our_gain(zoo.Scenario(**{**sc.__dict__, "days_truth": "worst"}), 120, 3) == 20 - 6
    pos = zoo.Scenario(**{**sc.__dict__, "days_weight": 2.0})
    assert zoo.our_gain(pos, 120, 3) == 26
    assert zoo.our_gain(zoo.Scenario(**{**pos.__dict__, "days_truth": "worst"}), 120, 3) == 14


def test_a_deal_outside_our_limit_is_flagged_and_scores_negative():
    record, _ = zoo.play(Script(zoo.Act("offer", 90)), scenario())
    assert record.status == "deal" and record.outside_limit and record.result == pytest.approx(-10 * 0.94)


# ---------------------------------------------------------------- the rivals


LINEAR = {"open": 0.2, "end": 0.05, "shape": 1.0, "cadence": 1.0}


def rival_prices(final: dict) -> list[int]:
    return [m["price"] for m in final["messages"] if m["from"] != "you"]


def test_each_style_has_its_shape_when_we_stay_silent():
    rng = random.Random(3)
    silent = Script()
    for role in ("seller", "buyer"):
        shapes = {}
        for style in zoo.STYLES:
            sc = zoo.draw_scenario(style, role, rng)
            shapes[style] = rival_prices(zoo.play(silent, sc)[1])
        assert shapes["no_show"] == []
        assert 1 <= len(shapes["one_shot"]) <= 2
        assert len(shapes["tit_for_tat"]) == 1  # it opens, then waits for us
        for style in ("linear", "convex", "sim", "holdout"):
            prices = shapes[style]
            assert len(prices) >= 2, style
            better = prices == sorted(prices) if role == "seller" else prices == sorted(prices, reverse=True)
            assert better, (style, prices)  # every move concedes toward us


def test_the_holdout_jumps_then_restates_one_price_to_every_offer_of_ours():
    sc = scenario(style="holdout", params={"open": 0.6, "hold": 0.2, "steps": 1}, rival_limit=200)
    offers = Script(*[zoo.Act("offer", 400 - 10 * i) for i in range(12)])
    record, final = zoo.play(offers, sc)
    prices = rival_prices(final)
    assert prices[0] == 80 and set(prices[1:]) == {160}  # 200 × (1 - 0.6), then 200 × (1 - 0.2)
    assert len(prices) == 12 and record.status == "no_deal"  # it never pays more than 160: no deal at 290+


def test_tit_for_tat_concedes_in_proportion_to_our_concessions():
    params = {"open": 0.5, "end": 0.05, "ratio": 1.0, "drift": 0.0}
    sc = scenario(style="tit_for_tat", params=params, rival_limit=200)
    big = Script(*[zoo.Act("offer", 300 - 20 * i) for i in range(4)])
    small = Script(*[zoo.Act("offer", 300 - 2 * i) for i in range(4)])
    moved_big = rival_prices(zoo.play(big, sc)[1])
    moved_small = rival_prices(zoo.play(small, sc)[1])
    assert moved_big[:4] == [100, 100, 120, 140] and moved_small[:4] == [100, 100, 102, 104]


def test_no_rival_ever_offers_or_accepts_outside_its_own_limit():
    def reckless(duel: dict, tick: int, started: int) -> zoo.Act:
        rng = random.Random(f"{duel['duel']}:{tick}")
        return zoo.Act("offer", rng.randint(1, 260), rng.randint(0, 10)) if rng.random() < 0.7 else zoo.HOLD

    for two in (False, True):
        grid = zoo.scenarios(zoo.STYLES, n=40, decays=(0.06,), two_issues=two)
        for sc in grid:
            record, final = zoo.play(reckless, sc)
            rival = zoo.RivalView(
                0, 0, 1, sc.rival_role, sc.rival_limit, sc.rival_days_weight, two, 0, {}, (), None, None,
                random.Random(0), sc.limit,
            )  # fmt: skip
            for m in final["messages"]:
                if m["from"] != "you":
                    assert rival.utility(m["price"], m["days"] or 0) >= 0, (sc.style, m)
            if record.deal:
                assert rival.utility(record.price or 0, record.days or 0) >= 0, sc.style


def test_the_sim_style_replays_the_simulators_own_bot_message_for_message():
    m = manual_world(duel_first_tick=1, duel_ticks=12)
    m.step()
    w = m.world
    mine = sorted((d for d in w.state.duels.values() if d.team == US), key=lambda d: d.duel)

    def conceding(duel: dict, tick: int, started: int):
        left = duel["deadline_tick"] - tick
        if left <= 3 and duel["rival_offer"]:
            return zoo.Act("accept")
        step = (tick - started) * 4
        limit = duel["your_limit"]
        return zoo.Act("offer", limit + 60 - step if duel["role"] == "seller" else max(1, limit - 60 + step))

    while any(d.status == "live" for d in mine):
        for d in mine:
            if d.status != "live" or d.accepted is not None:
                continue
            move = conceding(duels.duel_view(d), w.tick, d.started_tick)
            if move.kind == "offer":
                duels.say(w, US, d.duel, {"text": "hola", "price": move.price})
            elif move.kind == "accept":
                duels.accept(w, US, d.duel)
        m.step()
    for d in mine:
        sc = zoo.Scenario(
            role=d.role,
            limit=d.your_limit,
            rival_limit=d.rival_limit,
            style="sim",
            duel_ticks=d.deadline_tick - d.started_tick,
            started_tick=d.started_tick,
        )
        record, final = zoo.play(conceding, sc)
        theirs = [(x["tick"], x["from"] == "you", x["price"]) for x in d.messages]
        ours = [(x["tick"], x["from"] == "you", x["price"]) for x in final["messages"]]
        assert ours == theirs
        assert (record.status, record.price) == (d.status, d.price)


# ---------------------------------------------------------------- the live simulator's zoo rivals


def drive(m, policy) -> list:
    """Run every duel of ours in a manual world to its close with `policy`; return them."""
    w = m.world
    mine = sorted((d for d in w.state.duels.values() if d.team == US), key=lambda d: d.duel)
    while any(d.status == "live" for d in mine):
        for d in mine:
            if d.status != "live" or d.accepted is not None:
                continue
            move = policy(duels.duel_view(d), w.tick, d.started_tick)
            if move.kind == "offer":
                body = {"text": "hola", "price": move.price}
                if "days" in d.issues:
                    body["days"] = move.days if move.days is not None else 0
                duels.say(w, US, d.duel, body)
            elif move.kind == "accept" and d.rival_offer is not None:
                duels.accept(w, US, d.duel)
        m.step()
    return mine


def countering(duel: dict, tick: int, started: int) -> zoo.Act:
    limit, left = duel["your_limit"], duel["deadline_tick"] - tick
    if left <= 2 and duel["rival_offer"]:
        return zoo.Act("accept")
    step = (tick - started) * 3
    return zoo.Act("offer", limit + 50 - step if duel["role"] == "seller" else max(1, limit - 50 + step), 0)


def test_without_the_variable_every_duel_faces_the_simulators_bot(monkeypatch):
    monkeypatch.delenv(duels.STYLES_ENV, raising=False)
    m = manual_world(duel_first_tick=1)
    m.step()
    assert all(duels.rival_style(m.world, d) == ("sim", {}) for d in m.world.state.duels.values())
    assert duels.decay() == 0.06


def test_a_no_show_rival_never_speaks_in_the_live_simulator(monkeypatch):
    monkeypatch.setenv(duels.STYLES_ENV, "no_show")
    m = manual_world(duel_first_tick=1, duel_ticks=6)
    m.step()
    mine = drive(m, countering)
    assert mine and all(d.status == "no_deal" for d in mine)
    assert all(msg["from"] == "you" for d in mine for msg in d.messages)


@pytest.mark.parametrize("style", ["holdout", "tit_for_tat", "one_shot"])
def test_live_zoo_rivals_play_like_the_offline_engine(monkeypatch, style):
    monkeypatch.setenv(duels.STYLES_ENV, style)
    monkeypatch.setenv(duels.DECAY_ENV, "0.1")
    m = manual_world(duel_first_tick=1, duel_ticks=12)
    m.step()
    mine = drive(m, countering)
    for d in mine:
        got, params = duels.rival_style(m.world, d)
        assert got == style and d.decay_per_round == 0.1
        sc = zoo.Scenario(
            role=d.role,
            limit=d.your_limit,
            rival_limit=d.rival_limit,
            style=style,
            params=params,
            decay=0.1,
            duel_ticks=d.deadline_tick - d.started_tick,
            started_tick=d.started_tick,
        )
        record, final = zoo.play(countering, sc)
        assert [(x["tick"], x["from"] == "you", x["price"]) for x in final["messages"]] == [
            (x["tick"], x["from"] == "you", x["price"]) for x in d.messages
        ]
        assert (record.status, record.price) == (d.status, d.price)


def test_a_mix_of_styles_is_drawn_once_per_duel(monkeypatch):
    monkeypatch.setenv(duels.STYLES_ENV, "linear, holdout,no_show")
    m = manual_world(duel_first_tick=1)
    m.step()
    w = m.world
    drawn = {d.duel: duels.rival_style(w, d) for d in w.state.duels.values()}
    m.step(3)
    assert drawn == {d.duel: duels.rival_style(w, d) for d in w.state.duels.values()}
    assert {s for s, _ in drawn.values()} == {"linear", "holdout", "no_show"}


def test_bad_settings_fall_back_to_the_defaults_with_a_warning(monkeypatch, caplog):
    monkeypatch.setattr(duels, "_warned", set())
    monkeypatch.setenv(duels.STYLES_ENV, "linear,greedy")
    assert duels.styles() == ("sim",)
    for bad in ("1.5", "-0.1", "fast"):
        monkeypatch.setenv(duels.DECAY_ENV, bad)
        assert duels.decay() == 0.06
    assert "greedy" in caplog.text and "fast" in caplog.text
    m = manual_world(duel_first_tick=1)
    m.step(3)  # the tick still runs
    assert m.world.state.duels


def test_the_live_simulator_counts_rounds_and_decay_like_the_real_game(monkeypatch):
    monkeypatch.setenv(duels.STYLES_ENV, "no_show")
    m = manual_world(duel_first_tick=1, duel_ticks=6)
    m.step()
    silent_rival = drive(m, countering)
    assert all(d.rounds == 0 and priced(duels.duel_view(d), True) >= 3 for d in silent_rival)  # it never priced

    monkeypatch.delenv(duels.STYLES_ENV)
    m = manual_world(duel_first_tick=1, duel_every_ticks=20, duel_ticks=12)
    m.step(21)  # the practice session closes untouched; session 2 (scored, two issues) opens at tick 21
    scored = [d for d in drive(m, countering) if not d.practice]
    deals = [d for d in scored if d.status == "deal"]
    assert scored and deals
    for d in scored:
        view = duels.duel_view(d)
        assert d.rounds == min(priced(view, True), priced(view, False)) > 0
    for d in deals:
        kept = (1 - d.decay_per_round) ** d.rounds
        assert d.result["points"] == pytest.approx(10 * d.result["share"] * kept, abs=0.02)


def test_a_listening_one_shot_takes_a_fresh_offer_that_leaves_it_its_margin_and_a_deaf_one_never_does():
    params = {"open": 0.5, "shots": 1, "gap": 1, "listens": 1.0, "accept": 0.1}
    sc = scenario(style="one_shot", params=params, rival_limit=200)  # it bids 100 once; it takes ≤ 180
    late = Script(zoo.HOLD, zoo.HOLD, zoo.HOLD, zoo.Act("offer", 185), zoo.Act("offer", 180))
    record, final = zoo.play(late, sc)
    assert rival_prices(final) == [100, 180] and (record.status, record.price, record.closer) == ("deal", 180, "rival")
    deaf, _ = zoo.play(Script(zoo.Act("offer", 120)), scenario(style="one_shot", params={**params, "listens": 0.0}))
    assert deaf.status == "no_deal"


def test_moving_first_in_the_tick_hides_the_rivals_message_of_that_tick():
    seen: list[object] = []

    def watch(duel: dict, tick: int, started: int) -> zoo.Act:
        seen.append(duel["rival_offer"])
        return zoo.Act("accept")

    zoo.play(watch, scenario(style="linear", params=LINEAR))
    assert seen[0] is not None and seen[0]["tick"] == 100  # the simulator's order: the rival opened first
    seen.clear()
    record, _ = zoo.play(watch, scenario(style="linear", params=LINEAR, team_first=True))
    assert seen[0] is None and record.errors[0] == "no_offer" and record.close_tick == 102  # accepted at 101


@pytest.mark.parametrize("team_first", [False, True])
def test_a_responsive_rival_answers_each_offer_of_ours_once_in_either_tick_order(team_first):
    tft = {"open": 0.5, "end": 0.05, "ratio": 1.0, "drift": 0.0}
    sc = scenario(style="tit_for_tat", params=tft, rival_limit=200, team_first=team_first)
    record, final = zoo.play(Script(zoo.Act("offer", 300), zoo.Act("offer", 290)), sc)
    # It opens (after our 300 when we move first), answers 290 with +10 once, then waits: no repeat while we hold.
    assert rival_prices(final) == ([100, 110] if team_first else [100, 100, 110])
    hold = scenario(style="holdout", params={"open": 0.6, "hold": 0.2, "steps": 1}, rival_limit=200)
    _, final = zoo.play(Script(zoo.Act("offer", 400)), zoo.Scenario(**{**hold.__dict__, "team_first": team_first}))
    assert rival_prices(final).count(160) == 1  # it restated its hold price to our one offer once, not every tick


def test_a_deaf_conceder_still_concedes_but_never_accepts():
    sc = scenario(style="linear", params={**LINEAR, "listens": 0.0}, rival_limit=200)
    record, final = zoo.play(Script(*[zoo.Act("offer", 120)] * 12), sc)  # 120 leaves it 80 P: a listener takes it
    assert record.status == "no_deal" and len(rival_prices(final)) >= 2
    assert zoo.play(Script(*[zoo.Act("offer", 120)] * 12), scenario(style="linear", params=LINEAR, rival_limit=200))[
        0
    ].deal
