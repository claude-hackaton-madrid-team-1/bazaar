"""Jev as the maker's decision model: legal candidate prices only, `undecided` keeps today's price,
never below the sell floor whatever Jev says, floats on the decision rows, outcomes once offers settle."""

from bazaar_agent.agents.jev_journal import JevJournal
from bazaar_agent.agents.maker import Maker, Target
from bazaar_agent.agents.maker_jev import (
    PRICE_QUESTION,
    REPRICE_QUESTION,
    MakerJev,
    MakerJevConfig,
    hold_is_legal,
    price_candidates,
)
from bazaar_agent.agents.market import OpenOffer
from bazaar_agent.agents.runtime import JevAdvice
from bazaar_agent.config import REPO_ROOT
from bazaar_agent.guardrails import Guardrails
from bazaar_agent.jev import read_log
from bazaar_agent.jev.judge import load_questions
from tests.agent_fakes import TICK, FakePublic, FakeTeam, bid, clock, our_ask, parts, rows
from tests.test_strategy import ME, PARAMS

PROBS = {"aggressive": 0.1, "fair": 0.1, "quick_sale": 0.8}


class FakeJev:
    def __init__(self, verdict, value=0.8, reason=None, probabilities=None, digest=None):
        self.advice = JevAdvice(verdict, value, probabilities, reason, digest)
        self.states = []

    def __call__(self, state):
        self.states.append(state)
        return self.advice


def maker(tmp_path, team, price_fn=None, reprice_fn=None, *, live=False, now=1000.0, journal=None, **rules):
    jev = MakerJev(price_fn or FakeJev("undecided"), reprice_fn or FakeJev("undecided"), journal=journal)
    lines: list[str] = []
    m = Maker(team, FakePublic(), live=live, log=lines.append, now=lambda: now, jev=jev, **parts(tmp_path, **rules))
    return m, lines


def posted(team):
    return [s for s in team.sent if s[0] == "list_offer"]


def asks(team):
    return {p[1]["assets"][0]: p[2]["cash"] for p in posted(team) if p[1].get("assets")}


def bids(team):
    return [p[1]["cash"] for p in posted(team) if p[1].get("cash")]


# ---------------------------------------------------------------- the candidates


def test_candidates_keep_todays_price_and_stay_on_our_side_of_every_limit():
    ask = Target("ask", "LAT-09", "rare", 68, 5, 45.0, 42.0, "r")
    assert price_candidates(ask, PARAMS, Guardrails()) == {"aggressive": 68, "fair": 59, "quick_sale": 50}
    rare_bid = Target("bid", "LAV-09", "rare", 65, None, 145.6, 147.7, "r")
    assert price_candidates(rare_bid, PARAMS, Guardrails()) == {"aggressive": 50, "fair": 65, "quick_sale": 80}
    capped = price_candidates(rare_bid, PARAMS, Guardrails(max_price_rare=70))
    assert capped["quick_sale"] == 70  # the guardrail cap, not the 143 our value allows
    flat = Target("ask", "LAT-03", "common", 7, 4, 1.2, 1.0, "r")
    assert price_candidates(flat, PARAMS, Guardrails()) == {"aggressive": 7}  # no room: no question
    floor = price_candidates(ask, PARAMS, Guardrails(sell_min_value_ratio=1.5))
    assert floor == {"aggressive": 68}  # 45 × 1.5 = 68: nothing lower is legal


def test_the_maker_pack_matches_the_labels_the_code_maps():
    questions = load_questions(REPO_ROOT / "questions" / "maker.json")
    assert set(questions[PRICE_QUESTION]["criteria"]) == {"aggressive", "fair", "quick_sale"}
    assert questions[REPRICE_QUESTION]["type"] == "noul"


# ---------------------------------------------------------------- the choice


def test_jev_picks_a_legal_price_and_the_row_carries_its_floats(tmp_path):
    team = FakeTeam()
    fn = FakeJev("quick_sale", 0.8, probabilities=PROBS)
    m, _ = maker(tmp_path, team, fn, live=True)
    m.on_tick(clock())
    assert asks(team) == {5: 50, 4: 7} and bids(team) == [80]
    row = next(r for r in rows(tmp_path) if r.get("kind") == "post_ask" and r["inputs"]["ref"] == "LAT-09")
    assert row["jev"]["verdict"] == "quick_sale" and row["jev"]["probabilities"] == PROBS
    assert row["inputs"]["price"] == 50 and row["inputs"]["price_candidates"] == {
        "aggressive": 68,
        "fair": 59,
        "quick_sale": 50,
    }
    listing = fn.states[0]["listing"]
    assert listing["candidates"] and listing["default"] in ("aggressive", "fair") and fn.states[0]["cash_floor"] == 270


def test_undecided_or_no_budget_keeps_todays_prices(tmp_path):
    undecided = FakeTeam()
    maker(tmp_path / "u", undecided, FakeJev("undecided", 0.5, reason="below_threshold"), live=True)[0].on_tick(clock())
    assert asks(undecided) == {5: 68, 4: 10} and bids(undecided) == [65]

    late, fn = FakeTeam(), FakeJev("quick_sale")
    maker(tmp_path / "l", late, fn, live=True)[0].on_tick(clock(next_tick_in=5.0))  # 3 s left < 4 s
    assert fn.states == [] and asks(late) == {5: 68, 4: 10} and bids(late) == [65]


def test_never_lists_below_the_sell_floor_whatever_jev_says(tmp_path, monkeypatch):
    from bazaar_agent.agents import maker as maker_module

    below = {"aggressive": 68, "fair": 40, "quick_sale": 30}  # 30 and 40 are below LAT-09's floor 35+10
    monkeypatch.setattr(maker_module, "price_candidates", lambda t, params, rules: below if t.ref == "LAT-09" else {})
    for verdict in ("quick_sale", "fair", "dump_it", "aggressive"):
        team = FakeTeam()
        m, _ = maker(tmp_path / verdict, team, FakeJev(verdict, 0.99), live=True)
        m.on_tick(clock())
        assert asks(team).get(5) == 68, verdict


def test_a_bid_jev_picks_must_keep_cash_above_the_floor(tmp_path):
    team = FakeTeam(me={**ME, "cash": 340})  # 65 leaves 275; 80 would leave 260 < cash_floor 270
    fn = FakeJev("quick_sale", 0.9)
    maker(tmp_path, team, fn, live=True)[0].on_tick(clock())
    assert bids(team) == [65]
    bid_state = next(s for s in fn.states if s["listing"]["side"] == "bid")
    assert (
        "quick_sale" in bid_state["listing"]["not_allowed"] and "quick_sale" not in bid_state["listing"]["candidates"]
    )


def test_a_remembered_label_is_not_repriced_back_and_not_asked_again(tmp_path):
    team = FakeTeam()
    fn = FakeJev("quick_sale", 0.8)
    m, _ = maker(tmp_path, team, fn, live=True)
    m.on_tick(clock())
    calls = len(fn.states)
    team.sent.clear()
    team.offers = [our_ask(5000, 5, "LAT-09", 50), our_ask(5001, 4, "LAT-03", 7), bid(5002, "LAV-09", 80)]
    m.on_tick(clock(tick=TICK + 1))
    assert team.sent == [] and len(fn.states) == calls


# ---------------------------------------------------------------- reprice or hold


def test_a_decided_no_holds_a_stale_offer_and_anything_else_reprices(tmp_path):
    held = FakeTeam(offers=[our_ask(1, 5, "LAT-09", 75)])  # target 68: moved more than 5 %
    m, lines = maker(tmp_path / "hold", held, reprice_fn=FakeJev("no", 0.1), live=True)
    m.on_tick(clock())
    assert ("cancel", 1) not in held.sent and any("hold ask 1 LAT-09 at 75 (target 68)" in line for line in lines)
    hold_row = next(r for r in rows(tmp_path / "hold") if r.get("kind") == "hold_ask")
    assert hold_row["jev"]["verdict"] == "no" and hold_row["chosen"] is True

    for verdict in ("yes", "undecided"):
        team = FakeTeam(offers=[our_ask(1, 5, "LAT-09", 75)])
        maker(tmp_path / verdict, team, reprice_fn=FakeJev(verdict, 0.9), live=True)[0].on_tick(clock())
        assert ("cancel", 1) in team.sent and asks(team)[5] == 68


def test_holding_is_asked_only_when_the_old_price_is_still_legal(tmp_path):
    t = Target("ask", "LAT-09", "rare", 68, 5, 45.0, 42.0, "r")
    assert not hold_is_legal(OpenOffer(1, "ask", "LAT-09", 40, "rastro", 5, 140, 90), t, Guardrails())
    assert hold_is_legal(OpenOffer(1, "ask", "LAT-09", 75, "rastro", 5, 140, 90), t, Guardrails())
    b = Target("bid", "LAV-09", "rare", 65, None, 145.6, 1.0, "r")
    assert not hold_is_legal(OpenOffer(2, "bid", "LAV-09", 90, "rastro", None, 140, 90), b, Guardrails())
    fn = FakeJev("no", 0.1)
    team = FakeTeam(offers=[our_ask(1, 5, "LAT-09", 40)])  # below the floor: reprice, never hold
    maker(tmp_path, team, reprice_fn=fn, live=True)[0].on_tick(clock())
    assert fn.states == [] and ("cancel", 1) in team.sent


# ---------------------------------------------------------------- outcomes for calibration


def test_outcomes_filled_right_expired_wrong(tmp_path):
    journal = JevJournal(tmp_path / "jev")
    fn = FakeJev("quick_sale", 0.8, digest="d-price")
    team = FakeTeam()
    m, lines = maker(tmp_path, team, fn, live=True, journal=journal)
    m.on_tick(clock())  # posts 5000 (LAV-09 bid 80), 5001 (LAT-09 ask 50), 5002 (LAT-03 ask 7)
    team.offers = [our_ask(5001, 5, "LAT-09", 50, expires=TICK + 40)]  # 5000 and 5002 left the board early
    m.on_tick(clock(tick=TICK + 1))  # reposts: the bid at 65 (80 now breaks the spend cap), not judged
    team.offers = []
    m.on_tick(clock(tick=TICK + 41))  # 5001 is past its expiry
    outcomes = sorted(e["outcome"] for e in read_log(tmp_path / "jev") if e["kind"] == "outcome")
    assert outcomes == ["right", "right", "wrong"]
    assert any("offer 5000: jev list_price_choice quick_sale was right" in line for line in lines)
    assert any("offer 5001: jev list_price_choice quick_sale was wrong" in line for line in lines)


def test_an_offer_we_withdrew_is_unknown():
    from bazaar_agent.agents.maker_jev import OfferWatch

    written = []

    class Journal:
        def outcome(self, digest, outcome, question, note=None):
            written.append((digest, outcome, question))

    watch = OfferWatch(Journal())  # type: ignore[arg-type]
    watch.watch(7, JevAdvice("no", 0.1, digest="d-hold"), REPRICE_QUESTION, 140)
    watch.watch(8, JevAdvice("undecided", 0.5, digest="d-none"), REPRICE_QUESTION, 140)  # undecided: not judged
    watch.cancelled(7)
    watch.observe([], 120)
    assert written == [("d-hold", "unknown", REPRICE_QUESTION)]


def test_dry_runs_and_undecided_verdicts_write_no_outcome(tmp_path):
    journal = JevJournal(tmp_path / "jev")
    m, _ = maker(tmp_path, FakeTeam(), FakeJev("quick_sale", 0.8, digest="d"), live=False, journal=journal)
    m.on_tick(clock())
    m.on_tick(clock(tick=TICK + 1))
    assert read_log(tmp_path / "jev") == ()


def test_the_call_budget_caps_jev_questions_per_tick(tmp_path):
    fn = FakeJev("undecided", 0.5, reason="network_error")  # never cached: every target asks again
    team = FakeTeam()
    m = Maker(
        team,
        FakePublic(),
        live=False,
        log=lambda line: None,
        now=lambda: 1000.0,
        jev=MakerJev(fn, config=MakerJevConfig(max_calls_per_tick=2)),
        **parts(tmp_path),
    )
    m.on_tick(clock())
    assert len(fn.states) == 2


def test_a_bug_in_the_jev_layer_keeps_todays_prices(tmp_path, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("bug")

    monkeypatch.setattr(MakerJev, "choose_price", broken)
    monkeypatch.setattr(MakerJev, "should_hold", broken)
    team = FakeTeam(offers=[our_ask(1, 5, "LAT-09", 75)])
    m, lines = maker(tmp_path, team, FakeJev("quick_sale"), FakeJev("no"), live=True)
    m.on_tick(clock())
    assert ("cancel", 1) in team.sent and asks(team) == {5: 68, 4: 10} and bids(team) == [65]
    assert any("jev price failed (RuntimeError)" in line for line in lines)
    assert any("jev reprice failed (RuntimeError)" in line for line in lines)
