"""The taker with the live-feed reader (N12): a learned blocker removes a dealer thread, never adds a send."""

from bazaar_agent.agents.taker import Taker, TakerConfig
from bazaar_agent.learn.live import LiveLearner
from bazaar_agent.learn.reader import from_refusal
from bazaar_agent.learn.store import LearningStore
from bazaar_agent.sdk import BazaarError
from tests.agent_fakes import TICK, FakePublic, FakeTeam, clock, parts, rows

US = "t01"


def taker(tmp_path, team, store=None, *, live=True):
    lines: list[str] = []
    learner = LiveLearner(store or LearningStore(), lines.append)
    t = Taker(
        team,
        FakePublic(),
        live=live,
        log=lines.append,
        now=lambda: 1000.0,
        sleep=lambda s: None,
        config=TakerConfig(max_dealer_threads=3),
        learner=learner,
        **parts(tmp_path),
    )
    return t, lines


def opened(team):
    return [s for s in team.sent if s[0] == "open_thread"]


def blocker(code, until=None, item=None, tick=TICK):
    extra = {"until_tick": until} if until else {}
    return from_refusal("abuela", code, "", extra, US, _hour(tick), item)


def _hour(tick):
    from bazaar_agent.learn.live import game_hour

    return game_hour(clock(tick=tick))


def test_without_blockers_the_taker_opens_as_before(tmp_path):
    team = FakeTeam()
    t, _ = taker(tmp_path, team)
    t.on_tick(clock())
    assert opened(team) == [("open_thread", "abuela", {"buy": {"card": "LAV-08"}})]


def test_a_dealer_in_cooloff_is_skipped_with_a_decision_row_until_its_tick(tmp_path):
    store = LearningStore()
    store.record([blocker("cooloff", until=TICK + 2)])
    team = FakeTeam()
    t, lines = taker(tmp_path, team, store)
    t.on_tick(clock())
    assert opened(team) == [] and t.convs == {}
    (skip,) = [r for r in rows(tmp_path) if r.get("kind") == "dealer_skip"]
    assert skip["status"] == "rejected" and "cooloff with us until T102" in skip["reason"]
    assert any("skip abuela for LAV-08: abuela cooloff" in line for line in lines)
    team.now = clock(tick=TICK + 2)
    t.on_tick(team.now)  # free again AT until_tick
    assert opened(team) == [("open_thread", "abuela", {"buy": {"card": "LAV-08"}})]


def test_an_item_sold_out_moves_the_thread_to_the_next_item(tmp_path):
    store = LearningStore()
    store.record([blocker("sold_out", item="LAV-08")])
    team = FakeTeam()
    t, _ = taker(tmp_path, team, store)
    t.on_tick(clock())
    assert opened(team) == [("open_thread", "abuela", {"buy": {"card": "LAV-02"}})]


class RefusingTeam(FakeTeam):
    def open_thread(self, with_, topic=None, venue=None):
        self.sent.append(("open_thread", with_, topic))
        raise BazaarError(
            "cooloff", f"{with_} is not dealing with you until tick {TICK + 5}", 403, {"until_tick": TICK + 5}
        )


def test_a_refused_open_is_learned_and_not_retried_while_it_holds(tmp_path):
    team = RefusingTeam()
    t, lines = taker(tmp_path, team)
    t.on_tick(clock())
    assert len(opened(team)) == 1 and "learned: abuela cooloff with us until T105" in lines
    for tick in (TICK + 1, TICK + 4):
        team.now = clock(tick=tick)
        t.on_tick(team.now)
    assert len(opened(team)) == 1  # no refused retries: the blocker held for ticks 101..104
    team.now = clock(tick=TICK + 5)
    t.on_tick(team.now)
    assert len(opened(team)) == 2


def test_our_thread_closing_in_cooloff_blocks_the_next_open(tmp_path):
    team = FakeTeam()
    t, _ = taker(tmp_path, team)
    t.on_tick(clock())  # opens thread 5000
    team.thread_payloads[5000] = {
        "id": 5000,
        "with": "abuela",
        "status": "cooloff",
        "closed_reason": "cooloff",
        "until_tick": TICK + 20,
        "messages": [],
        "standing_offers": [],
    }
    for tick in (TICK + 1, TICK + 2):  # 101: the desk sees the close; 102: the opener would reopen abuela
        team.now = clock(tick=tick)
        t.on_tick(team.now)
    assert t.convs == {} and len(opened(team)) == 1
    assert [r["tick"] for r in rows(tmp_path) if r.get("kind") == "dealer_skip"] == [TICK + 2]


def test_a_broken_learning_store_changes_nothing(tmp_path):
    class Broken(LearningStore):
        def recall(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            raise RuntimeError("down")

    team = FakeTeam()
    t, lines = taker(tmp_path, team, Broken())
    t.on_tick(clock())
    assert opened(team) == [("open_thread", "abuela", {"buy": {"card": "LAV-08"}})]
    assert "learnings: recall failed (RuntimeError: down); trading as before" in lines


def test_a_dry_run_skips_too_and_sends_nothing(tmp_path):
    store = LearningStore()
    store.record([blocker("persona_quota")])  # a card quota: the whole dealer, until the hour ends
    team = FakeTeam()
    t, _ = taker(tmp_path, team, store, live=False)
    t.on_tick(clock())
    dealer_rows = [r["kind"] for r in rows(tmp_path) if r["kind"] != "pack_open"]  # the fake /me's sealed pack
    assert team.sent == [] and dealer_rows == ["dealer_skip"]


def test_a_blocker_is_recorded_once_and_its_row_stays_private(tmp_path):
    from bazaar_agent.agents.status import public_decision

    store = LearningStore()
    store.record([blocker("cooloff", until=TICK + 30)])
    team = FakeTeam()
    t, _ = taker(tmp_path, team, store)
    for tick in range(TICK, TICK + 4):
        team.now = clock(tick=tick)
        t.on_tick(team.now)
    (skip,) = [r for r in rows(tmp_path) if r.get("kind") == "dealer_skip"]
    shown = public_decision({**skip, "inputs": skip["inputs"]})
    assert shown["inputs"] == {} and shown["kind"] == "dealer_skip"  # no dealer, card or blocker in /state


def test_the_refusal_kept_for_learning_has_no_traceback(tmp_path):
    team = RefusingTeam()
    t, _ = taker(tmp_path, team)
    t.on_tick(clock())
    refused = t.rec.last_error
    assert refused is not None and refused.code == "cooloff" and refused.extra == {"until_tick": TICK + 5}
    assert not isinstance(refused, BaseException)


def test_restart_loads_shared_dealer_quota_before_first_open(tmp_path):
    from bazaar_agent.learn.reader import GameHour

    learned = from_refusal(
        "abuela",
        "persona_quota",
        "at most 10 conversations per hour with abuela",
        {},
        US,
        GameHour(tick=TICK - 1, t_hours=1.25, tick_seconds=15),
        "sobre_barrio",
    )

    class Persisted(LearningStore):
        def _recall_db(self, *args, **kwargs):
            return [learned]  # only the shared database has the row: new process memory starts empty

    team = FakeTeam()
    store = Persisted()
    assert not store.memory
    t, _ = taker(tmp_path, team, store)
    t.on_tick(clock())
    assert not opened(team)
    assert any(r.get("kind") == "dealer_skip" and "quota" in r.get("reason", "") for r in rows(tmp_path))
