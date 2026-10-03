"""N14a in the taker: the per-dealer plan from recall, Chato's final above our cap, and today's rows with the
lift off. Fakes only, no network."""

from copy import deepcopy

from bazaar_agent.agents.runtime import MarketFeed
from bazaar_agent.agents.taker import Taker, TakerConfig
from bazaar_agent.learn.evolve import Ladder, LadderPolicy
from tests.agent_fakes import TICK, FakePublic, FakeTeam, clock, parts, rows
from tests.test_intel import opened, settle
from tests.test_strategy import ME

CHATO = {
    "id": "chato",
    "status": "active",
    "level": 2,
    "menu": {"sells": [{"rarity": "uncommon", "sets": "released", "list_price": 30}]},
}
# Friday's shape: Chato filled uncommons at 28 and 29 (another team's threads).
EVENTS = [
    opened(1, 228, "t05", {"buy": {"card": "LAV-06"}}, tick=1, dealer="chato"),
    settle(2, 1, "chato", "t05", "LAV-06", 29, tick=2, kind="card", persona="chato"),
    opened(3, 253, "t03", {"buy": {"card": "LAT-06"}}, tick=3, dealer="chato"),
    settle(4, 2, "chato", "t03", "LAT-06", 28, tick=4, kind="card", persona="chato"),
]
CHATO_ME = {**ME, "unlocked": ["chato"]}


class FakeLearner:
    """The outcome learner's public face for the taker: its policies and its last pass's curves."""

    def __init__(self, policies=None):
        self.policies = policies or {}
        self.last = None

    def maybe_run(self, tick, us):
        return False


def taker(tmp_path, team, *, lift=0.0, learner=None, lessons=None, dealers=None):
    lines: list[str] = []
    kw = {**parts(tmp_path, dealer_final_lift=lift), "feed": MarketFeed(lambda n: deepcopy(EVENTS))}
    t = Taker(
        team,
        FakePublic(dealers=dealers or [CHATO], events=EVENTS),
        live=True,
        log=lines.append,
        now=lambda: 1000.0,
        sleep=lambda s: None,
        config=TakerConfig(max_dealer_threads=3),
        outcome_learner=learner,
        lessons=lessons,
        **kw,
    )
    return t, lines, kw["ledger"]


def final(oid, price, ref="LAV-08"):
    offer = {"id": oid, "maker": "chato", "status": "open", "final": True, "give": {"types": [f"card:{ref}"]}}
    return {**offer, "want": {"cash": price}}


def test_with_the_lift_off_chato_stays_out_of_reach_as_today(tmp_path):
    team = FakeTeam(me=CHATO_ME)
    t, lines, _ = taker(tmp_path, team)
    t.on_tick(clock())
    assert not [s for s in team.sent if s[0] == "open_thread"]  # his fills 28-29 sit above our cap 26


def test_the_lift_opens_chato_on_the_patience_play_and_takes_his_final_above_the_cap(tmp_path):
    team = FakeTeam(me=CHATO_ME)
    t, _, ledger = taker(tmp_path, team, lift=0.15)
    t.on_tick(clock())
    assert ("open_thread", "chato", {"buy": {"card": "LAV-08"}}) in team.sent
    assert [s for s in team.sent if s[0] == "say"] == [("say", 5000, 18)]  # 9 distinct bids: the final comes first
    (row,) = [r for r in rows(tmp_path) if r.get("kind") == "dealer_open"]
    assert row["inputs"]["final_max"] == 29 and row["inputs"]["plan"] == "18→26 step 1"
    assert row["inputs"]["changed_by"] == [
        "default patience for chato (5 bids): ladder 26→26 step 1 → 18→26 step 1",
        "dealer_final_lift 0.15: take a final up to 29 after 4 bids (our bids stay at or under 26)",
    ]
    for k in range(1, 4):  # he holds his opening 33; bids 19, 20, 21: a lifted final is taken only after 4 of ours
        held = {**final(700 + k, 33), "final": False}
        team.thread_payloads[5000] = {"id": 5000, "status": "open", "messages": [], "standing_offers": [held]}
        team.now = clock(tick=TICK + k)
        t.on_tick(team.now)
    assert [s[2] for s in team.sent if s[0] == "say"] == [18, 19, 20, 21]
    team.thread_payloads[5000] = {"id": 5000, "status": "open", "messages": [], "standing_offers": [final(801, 29)]}
    team.now = clock(tick=TICK + 4)
    t.on_tick(team.now)
    assert team.sent[-1] == ("accept", 801) and ledger.accept_items(TICK + 4) == ["LAV-08"]
    (accept,) = [r for r in rows(tmp_path) if r.get("kind") == "dealer_accept"]
    assert accept["guardrail"] == "allowed" and accept["inputs"]["changed_by"] == row["inputs"]["changed_by"]


def test_a_final_above_final_max_walks_and_our_bids_never_pass_the_cap(tmp_path):
    team = FakeTeam(me=CHATO_ME)
    t, _, _ = taker(tmp_path, team, lift=0.15)
    t.on_tick(clock())
    team.thread_payloads[5000] = {"id": 5000, "status": "open", "messages": [], "standing_offers": [final(801, 30)]}
    team.now = clock(tick=TICK + 1)
    t.on_tick(team.now)
    assert team.sent[-1] == ("close_thread", 5000) and ("accept", 801) not in team.sent
    assert all(s[2] <= 26 for s in team.sent if s[0] == "say")


def test_a_learned_policy_and_the_recalled_lessons_are_logged_on_the_open_and_every_bid(tmp_path):
    policy = LadderPolicy("chato", "card:uncommon", None, "skip: 0 of 2 under the cap 26", (28, 29), 2, 6.0, 150)
    asked: list[tuple] = []

    def lessons(text, *, subjects=None, subject_kind=None, tick=None):
        asked.append((text, subjects, tick))
        return [{"quoted_lesson": "every chato uncommon fill is 28-32, above our top bid 24"}]

    team = FakeTeam(me=CHATO_ME)
    learner = FakeLearner({("chato", "card:uncommon"): policy})
    t, _, _ = taker(tmp_path, team, lift=0.15, learner=learner, lessons=lessons)
    t.on_tick(clock())
    (row,) = [r for r in rows(tmp_path) if r.get("kind") == "dealer_open"]
    assert row["inputs"]["changed_by"][0] == (
        "learning policy chato card:uncommon @t150: skip lifted: 2 of 2 fills at or under the final cap 29"
    )
    assert row["inputs"]["learned"] == [policy.text()]
    assert row["inputs"]["recalled"] == ["every chato uncommon fill is 28-32, above our top bid 24"]
    assert asked == [("open a thread with chato to buy LAV-08 (uncommon)", ("chato",), TICK)]
    assert row["inputs"]["plan"] == "18→26 step 1"  # the policy's patience (6) + 3 bids before the top
    (bid,) = [r for r in rows(tmp_path) if r.get("kind") == "dealer_bid"]
    assert bid["inputs"]["changed_by"] == row["inputs"]["changed_by"]
    assert bid["inputs"]["recalled"] == row["inputs"]["recalled"]  # every bid row carries what was recalled


def test_a_learned_skip_still_skips_with_the_lift_off(tmp_path):
    policy = LadderPolicy("chato", "card:uncommon", None, "skip: 0 of 2 under the cap 26", (28, 29), 2, 6.0, 150)
    team = FakeTeam(me=CHATO_ME)
    t, _, _ = taker(tmp_path, team, learner=FakeLearner({("chato", "card:uncommon"): policy}))
    t.on_tick(clock())
    assert not [s for s in team.sent if s[0] == "open_thread"]


def test_an_abuela_policy_changes_the_ladder_and_names_itself_as_before(tmp_path):
    from tests.agent_fakes import parts as base_parts

    policy = LadderPolicy("abuela", "card:uncommon", Ladder(17, 1, 21), "best replayed share", (17, 22), 40, 5.0, 140)
    team = FakeTeam()
    lines: list[str] = []
    t = Taker(
        team,
        FakePublic(),
        live=True,
        log=lines.append,
        now=lambda: 1000.0,
        sleep=lambda s: None,
        config=TakerConfig(max_dealer_threads=3),
        outcome_learner=FakeLearner({("abuela", "card:uncommon"): policy}),
        **base_parts(tmp_path),
    )
    t.on_tick(clock())
    (row,) = [r for r in rows(tmp_path) if r.get("kind") == "dealer_open"]
    assert row["inputs"]["plan"] == "17→21 step 1" and row["inputs"]["final_max"] is None
    assert row["inputs"]["changed_by"] == [
        "learning policy abuela card:uncommon @t140: ladder 18→22 step 1 → 17→21 step 1"
    ]


# ---------------------------------------------------------------- review #158 P1: thread slots are not cash


TWO = [
    {
        "id": "abuela",
        "status": "active",
        "level": 1,
        "menu": {"sells": [{"rarity": "uncommon", "sets": "released", "list_price": 25}]},
    },
    {
        "id": "otra",
        "status": "active",
        "level": 1,
        "menu": {"sells": [{"rarity": "common", "sets": "released", "list_price": 10}]},
    },
]


def two_dealer_taker(tmp_path, team, threads=1):
    kw = {**parts(tmp_path), "feed": MarketFeed(lambda n: [])}
    return Taker(
        team,
        FakePublic(dealers=TWO, events=[]),
        live=True,
        log=lambda line: None,
        now=lambda: 1000.0,
        sleep=lambda s: None,
        config=TakerConfig(max_dealer_threads=threads),
        **kw,
    )


def test_max_dealer_threads_still_caps_the_opens_with_two_free_dealers(tmp_path):
    team = FakeTeam(me={**ME, "unlocked": ["abuela", "otra"]})
    two_dealer_taker(tmp_path, team, threads=1).on_tick(clock())
    assert len([s for s in team.sent if s[0] == "open_thread"]) == 1


def test_the_team_thread_cap_still_caps_the_opens(tmp_path):
    busy = [{"id": 900 + i, "with": f"t{i:02d}", "status": "open"} for i in range(5)]  # 5 of 6 team threads
    team = FakeTeam(me={**ME, "unlocked": ["abuela", "otra"]}, threads=busy)
    two_dealer_taker(tmp_path, team, threads=3).on_tick(clock(max_open_threads_per_team=6))
    assert len([s for s in team.sent if s[0] == "open_thread"]) == 1


def test_a_cash_skip_is_recorded_once_while_the_cash_moves(tmp_path):
    team = FakeTeam(me={**ME, "unlocked": ["chato"], "cash": 300})  # room 30: Chato's fills (~28.5) fit, 30 > 29 cap
    t, _, _ = taker(tmp_path, team, lift=0.15)
    team._me["cash"] = 290  # room 20: below his fills
    t.on_tick(clock())
    team._me["cash"] = 285  # room 15
    team.now = clock(tick=TICK + 1)
    t.on_tick(team.now)
    skips = [r for r in rows(tmp_path) if r.get("kind") == "dealer_skip"]
    assert len(skips) == 1 and skips[0]["reason"].startswith("cash: what we may still commit is below chato")


def test_a_lifted_final_named_after_one_bid_is_not_taken(tmp_path):
    # security audit #158 P1-2: a dealer's `final: true` alone must not raise our ceiling
    team = FakeTeam(me=CHATO_ME)
    t, _, _ = taker(tmp_path, team, lift=0.25)
    t.on_tick(clock())  # opens and bids 18
    team.thread_payloads[5000] = {"id": 5000, "status": "open", "messages": [], "standing_offers": [final(801, 32)]}
    team.now = clock(tick=TICK + 1)
    t.on_tick(team.now)
    assert ("accept", 801) not in team.sent and team.sent[-1] == ("close_thread", 5000)
    (walk,) = [r for r in rows(tmp_path) if r.get("kind") == "dealer_walk"]
    assert "a lifted final needs 4" in walk["reason"]


def test_two_commitments_in_one_tick_never_take_cash_below_the_floor(tmp_path):
    # security audit #158 P2-1: every check of a tick saw the cash read at its start. A dealer that takes our
    # bid settles in the same tick, so two bids (or an accept and a bid) must fit the cash above the floor.
    team = FakeTeam(me={**ME, "unlocked": ["abuela", "otra"], "cash": 300})  # 30 above the floor
    two_dealer_taker(tmp_path, team, threads=3).on_tick(clock())
    assert len([s for s in team.sent if s[0] == "open_thread"]) == 2
    assert [s for s in team.sent if s[0] == "say"] == [("say", 5000, 25)]  # 25 + 10 > 30: the second is refused
    (walk,) = [r for r in rows(tmp_path) if r.get("kind") == "dealer_walk"]
    assert "cash_floor" in walk["reason"]
    roomy = FakeTeam(me={**ME, "unlocked": ["abuela", "otra"], "cash": 310})  # 40 above: both fit
    two_dealer_taker(tmp_path / "roomy", roomy, threads=3).on_tick(clock())
    assert [s for s in roomy.sent if s[0] == "say"] == [("say", 5000, 25), ("say", 5001, 10)]


def test_a_card_another_process_is_negotiating_gets_no_second_thread(tmp_path):
    # security #158 r2 P3-B: the cards of every open thread of ours are busy, not only this process's
    other = {"id": 900, "with": "abuela", "status": "open", "topic": {"buy": {"card": "LAV-08"}}}
    team = FakeTeam(me=CHATO_ME, threads=[other])
    abuela = {"id": "abuela", "status": "active", "level": 1, "menu": {"sells": []}}  # a dealer we may not buy from
    t, _, _ = taker(tmp_path, team, lift=0.15, dealers=[abuela, CHATO])
    t.on_tick(clock())
    assert not [s for s in team.sent if s[0] == "open_thread" and s[2] == {"buy": {"card": "LAV-08"}}]


def test_a_rival_team_thread_never_marks_a_card_busy(tmp_path):
    # review #158 round 3 P2: a thread another team opened with us carries a topic that team wrote
    rival = {"id": 901, "team": "t05", "with": "t01", "status": "open", "topic": {"buy": {"card": "LAV-08"}}}
    team = FakeTeam(me=CHATO_ME, threads=[rival])
    t, _, _ = taker(tmp_path, team, lift=0.15)
    t.on_tick(clock())
    assert ("open_thread", "chato", {"buy": {"card": "LAV-08"}}) in team.sent  # as if the rival thread were not there
