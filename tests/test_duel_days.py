"""Two-issue duels (B8): the sign latch, the rival's days, our days and the repricing that keeps our value."""

from dataclasses import dataclass

import pytest

from bazaar_agent.agents import duel_days as dd

SIM_TEXT = "primas you gain (+) or lose (-) per delivery day, 0-10"


def duel(**over) -> dict:
    base = {
        "duel": 7,
        "role": "seller",
        "issues": ["price", "days"],
        "your_limit": 100,
        "your_days_weight": 2.0,
        "days_meaning": None,
        "rival": "Rival Azul",
        "messages": [],
    }
    return base | over


def rival(*offers: tuple[int, int, int]) -> list[dict]:
    return [{"tick": t, "from": "Rival Azul", "price": p, "days": d, "text": ""} for t, p, d in offers]


@pytest.mark.parametrize(
    ("meaning", "real", "verdict"),
    [
        (SIM_TEXT, True, "signed"),
        (SIM_TEXT, False, "unknown"),  # the simulator's own words are not evidence
        (None, True, "unknown"),  # Friday's real payloads
        ("Primas per delivery day", True, "unknown"),  # the openapi's words: no direction
        ("primas each delivery day costs you", True, "cost"),
        ("primas you gain per delivery day", True, "unknown"),  # a gain alone: can a weight be negative? not said
        ("primas it costs (+) or saves you per day", True, "reversed"),  # + is a cost: the other convention
        ("positive weights mean you gain primas per day", True, "signed"),
        ("positive weights cost you primas per day", True, "reversed"),
        ("primas you gain or lose per delivery day", True, "unknown"),  # no sign tied to either
    ],
)
def test_only_a_real_payload_that_names_a_gain_and_a_loss_confirms_the_sign(meaning, real, verdict):
    assert dd.evidence(duel(days_meaning=meaning), real) == verdict
    assert dd.evidence(duel(days_meaning=meaning, issues=["price"]), real) == "unknown"


@pytest.mark.parametrize(
    "meaning",
    [  # #150 review P1: a direction word or a comparative can turn "gain (+)" into a cost per day of OUR days
        "positive: you gain primas for each day earlier",
        "you gain (+) per day sooner",
        "primas you gain (+) per day the delivery is brought forward",
        "positive weights mean you earn less per day",
        "positive weights mean you gain fewer primas per day",
        "primas you gain (+) per day of delay",  # the same direction as ours, but a person reads it, not a regex
        "you gain (+) for every day later",
        "primas you gain (+) or lose (-) per day faster",
    ],
)
def test_a_direction_word_or_a_comparative_never_latches_a_sign(meaning):
    assert dd.evidence(duel(days_meaning=meaning), True) == "unknown"


def test_the_switch_latches_the_first_real_evidence_and_persists_it(tmp_path):
    path = tmp_path / "days.json"
    switch = dd.DaysSwitch.load(path)
    assert switch.observe([duel(days_meaning=SIM_TEXT)], real_game=False) == "unknown" and not path.exists()
    assert switch.observe([duel(days_meaning=None), duel(duel=8, days_meaning=SIM_TEXT)], real_game=True) == "signed"
    assert not switch.signed(allowed=True)  # a text alone is one signal: a score must agree (#150 security P1)
    again = dd.DaysSwitch.load(path)
    assert (again.verdict, again.duel, again.text, again.text_signed) == ("signed", 8, SIM_TEXT, True)


def test_a_reversed_convention_latches_and_keeps_the_switch_off(tmp_path):
    switch = dd.DaysSwitch.load(tmp_path / "days.json")
    assert switch.observe([duel(days_meaning="primas it costs (+) or saves you per day")], True) == "reversed"
    assert not switch.signed(True)


def test_real_payloads_that_disagree_switch_it_off_for_good(tmp_path):
    switch = dd.DaysSwitch.load(tmp_path / "days.json")
    switch.observe([duel(days_meaning=SIM_TEXT)], True)
    switch.observe([duel(days_meaning="each day costs you primas")], True)
    assert switch.verdict == "conflict" and not switch.signed(True)
    switch.observe([duel(days_meaning=SIM_TEXT)], True)
    assert dd.DaysSwitch.load(tmp_path / "days.json").verdict == "conflict"


def test_an_unreadable_state_file_is_a_conflict_until_a_person_deletes_it(tmp_path):
    path = tmp_path / "days.json"
    path.write_text("{not json")  # a truncated write must never silently undo a recorded conflict (security P3)
    assert dd.DaysSwitch.load(path).verdict == "conflict"
    assert dd.DaysSwitch.load(tmp_path / "missing.json").verdict == "unknown"


def test_the_rivals_days_show_which_end_it_prefers():
    tens = dd.rival_days(duel(messages=rival((1, 150, 10), (2, 145, 10), (3, 140, 10))))
    assert (tens.prefers, tens.confidence, tens.offers) == (10, 1.0, 3)
    zeros = dd.rival_days(duel(messages=rival((1, 150, 0), (2, 145, 0), (3, 140, 0))))
    assert (zeros.prefers, zeros.confidence) == (0, 0.5)  # a 0 may only mean it ignores days
    assert dd.rival_days(duel(messages=rival((1, 150, 5)))).prefers is None
    assert dd.rival_days(duel()).offers == 0


def test_our_days_are_0_in_the_worst_case_and_the_joint_best_end_when_signed():
    none = dd.RivalDays(None, 0.0, 0)
    likes_ten = dd.RivalDays(10, 1.0, 3)  # its weight is unknown: the prior, 2 P a day
    assert dd.choose_days(duel(), signed=False, rival=likes_ten) == 0
    assert dd.choose_days(duel(your_days_weight=2.0), signed=True, rival=none) == 10
    assert dd.choose_days(duel(your_days_weight=-1.0), signed=True, rival=likes_ten) == 10  # it gains ~2, we lose 1
    assert dd.choose_days(duel(your_days_weight=-4.0), signed=True, rival=likes_ten) == 0
    assert dd.choose_days(duel(issues=["price"]), signed=True, rival=none) is None


def test_repricing_keeps_our_value_and_never_leaves_our_limit():
    seller = duel(your_days_weight=-1.0)  # signed: each day costs us 1 P
    assert dd.reprice(seller, 120, 0, 10, signed=True) == 130  # 10 days cost 10: ask 10 more
    assert dd.value(seller, 130, 10, True) == dd.value(seller, 120, 0, True) == 120
    buyer = duel(role="buyer", your_limit=100, your_days_weight=-1.0)
    assert dd.reprice(buyer, 80, 0, 10, signed=True) == 70
    assert dd.reprice(buyer, 95, 0, 10, signed=True) == 85  # bid 10 less: still worth 95 to us
    # A gain per day lets a seller ask less for the same value: 100 at 1 day is worth 101, inside our cost of 100.
    assert dd.reprice(duel(your_days_weight=1.0, your_limit=100), 101, 0, 1, signed=True) == 100
    assert dd.reprice(duel(your_days_weight=1.0, your_limit=100), 99, 0, 1, signed=True) is None  # worth 99: outside


@dataclass(frozen=True)
class Move:
    kind: str
    price: int | None = None
    days: int | None = None


def test_the_wrapper_reprices_offers_only_and_keeps_moves_it_cannot_reprice():
    likes_ten = rival((1, 150, 10), (2, 145, 10), (3, 140, 10))
    d = duel(your_days_weight=-1.0, messages=likes_ten)  # we lose 1 a day, it prefers 10 days
    offer = dd.days_aware(lambda duel, tick, started: Move("offer", 120, 0), signed=True)
    assert offer(d, 4, 0) == Move("offer", 130, 10)
    accept = dd.days_aware(lambda duel, tick, started: Move("accept", 140), signed=True)
    assert accept(d, 4, 0) == Move("accept", 140)
    worst = dd.days_aware(lambda duel, tick, started: Move("offer", 120, 0), signed=False)
    assert worst(d, 4, 0) == Move("offer", 120, 0)


def done(weight: float, result: float, role: str = "seller", price: int = 120, days: int = 5) -> dict:
    """A finished real deal: limit 100, 2 rounds at decay 0.08 (kept 0.8464)."""
    return duel(role=role, your_days_weight=weight, status="deal", price=price, days=days, result=result, rounds=2,
                decay_per_round=0.08)  # fmt: skip


def test_a_finished_deals_score_shows_how_the_game_counts_days():
    kept = 0.92**2
    assert dd.scored_evidence(done(2.0, round((20 + 10) * kept, 1)), True) == "signed"  # 5 days at +2 added 10
    assert dd.scored_evidence(done(2.0, round((20 - 10) * kept, 1)), True) == "cost"  # they cost 10
    assert dd.scored_evidence(done(-2.0, round((20 + 10) * kept, 1)), True) == "reversed"  # -2 a day added 10
    assert dd.scored_evidence(done(-2.0, round((20 - 10) * kept, 1)), True) == "unknown"  # signed or cost: same
    assert dd.scored_evidence(done(2.0, round(20 * kept, 1)), True) == "conflict"  # days not scored: no model fits
    assert dd.scored_evidence(done(2.0, round(30 * kept, 1)), False) == "unknown"  # the simulator: no evidence
    assert dd.scored_evidence(done(2.0, round(30 * kept, 1), days=0), True) == "unknown"
    buyer = done(2.0, round((100 - 80 + 10) * kept, 1), role="buyer", price=80)
    assert dd.scored_evidence(buyer, True) == "signed"


def test_a_score_that_disagrees_with_the_text_is_a_conflict(tmp_path):
    switch = dd.latch(tmp_path)
    assert switch.observe([duel(days_meaning=SIM_TEXT)], True) == "signed"
    assert switch.observe([done(2.0, round(10 * 0.92**2, 1))], True) == "conflict"


def test_two_processes_on_one_file_never_undo_each_other(tmp_path):
    run, runtime = dd.latch(tmp_path), dd.latch(tmp_path)  # `duel run` and the runtime, both started at unknown
    run.observe([duel(days_meaning=SIM_TEXT)], True)
    runtime.observe([duel(days_meaning="each day costs you primas")], True)  # merges the file first: conflict
    assert dd.latch(tmp_path).verdict == "conflict"
    run.observe([duel(days_meaning=SIM_TEXT)], True)  # the stale "signed" in memory does not win
    assert run.verdict == "conflict" and dd.latch(tmp_path).verdict == "conflict"
    assert [p.name for p in (tmp_path / "duels").iterdir()] == ["days_sign.json"]  # no temp file left behind


# ---------------------------------------------------------------- two real signals before the sign is trusted


def signed_score(did: int) -> dict:
    """A finished real deal whose score shows the days added +2 a day (5 days, kept 0.92 ** 2)."""
    return {**done(2.0, round(30 * 0.92**2, 1)), "duel": did}


def test_one_signal_alone_never_turns_the_sign_on(tmp_path):
    # #150 security P1: one misread text (or one score) must not drop the guard's worst case for every duel.
    text_only = dd.latch(tmp_path / "a")
    assert text_only.observe([duel(days_meaning=SIM_TEXT)], True) == "signed" and not text_only.signed(True)
    score_only = dd.latch(tmp_path / "b")
    assert score_only.observe([signed_score(11)], True) == "signed" and not score_only.signed(True)
    assert score_only.observe([signed_score(11)], True) == "signed" and not score_only.signed(True)  # same deal


def test_a_text_and_a_score_or_two_scores_corroborate_the_sign(tmp_path):
    both = dd.latch(tmp_path / "a")
    both.observe([duel(days_meaning=SIM_TEXT)], True)
    both.observe([signed_score(11)], True)
    assert both.signed(True) and not both.signed(False)
    scores = dd.latch(tmp_path / "b")
    scores.observe([signed_score(11), signed_score(12)], True)
    assert scores.signed(True)


def test_corroboration_is_shared_through_the_file(tmp_path):
    duel_run, runtime = dd.latch(tmp_path), dd.latch(tmp_path)
    duel_run.observe([duel(days_meaning=SIM_TEXT)], True)
    runtime.observe([signed_score(11)], True)
    assert runtime.signed(True) and dd.latch(tmp_path).signed(True)


# ---------------------------------------------------------------- #150 round 2: what still fooled the latch


def test_a_score_that_fits_no_model_counts_against_the_sign(tmp_path):
    # Days counted back from 10: a 2-day deal at +2 a day adds 16, which none of signed / cost / reversed predicts.
    odd = done(2.0, round((20 + 16) * 0.92**2, 1), days=2)
    assert dd.scored_evidence(odd, True) == "conflict"
    switch = dd.latch(tmp_path)
    switch.observe([duel(days_meaning=SIM_TEXT), odd], True)
    assert switch.verdict == "conflict" and not switch.signed(True)


@pytest.mark.parametrize(
    "meaning",
    [
        "primas you gain (+) for each day before day 10",
        "primas you gain (+) per remaining day",
        "you gain (+) per day, counted backwards",
        "you gain (+) per day left until delivery",
    ],
)
def test_counting_words_never_latch_a_sign(meaning):
    assert dd.evidence(duel(days_meaning=meaning), True) == "unknown"


def test_a_conflict_reached_by_merging_the_file_is_written_back(tmp_path):
    runtime = dd.latch(tmp_path)
    runtime.observe([duel(days_meaning=SIM_TEXT), signed_score(11)], True)
    assert dd.latch(tmp_path).signed(True)
    duel_run = dd.DaysSwitch(verdict="conflict", path=tmp_path / dd.LATCH_FILE)  # held in memory, file says signed
    duel_run.observe([], True)
    assert dd.latch(tmp_path).verdict == "conflict" and not dd.latch(tmp_path).signed(True)


@pytest.mark.parametrize("issues", [5, True, 1.5, "days", None])
def test_a_malformed_issues_field_skips_only_its_own_row(tmp_path, issues):
    # #150 security r2 P3: `observe` runs on every v1 tick; a bad row used to raise and stop every duel that tick.
    switch = dd.latch(tmp_path)
    good = duel(duel=8, days_meaning=SIM_TEXT)
    assert switch.observe([duel(issues=issues, days_meaning=SIM_TEXT), good], True) == "signed"
    assert dd.two_issue({"issues": issues}) is False
