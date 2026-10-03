"""The schedule playbook: before / during / after instructions per agent, stored once, obeyed by the taker."""

from types import SimpleNamespace

import pytest

from bazaar_agent import playbook as pb
from bazaar_agent.news import NewsItem, schedule_rows

# The real /api/schedule (keyless, Sat 3 Oct 21:13 Madrid, game hour 11.59), trimmed.
SCHEDULE = {
    "upcoming": [
        {"at_hours": 11.65, "action": "duels", "note": "Duels II: price and delivery day",
         "params": {"name": "Duels II", "rounds": 2, "duel_ticks": 16}},
        {"at_hours": 13.0, "action": "bench", "note": "The Market Test", "params": {"traders": 10, "ticks": 16}},
        {"at_hours": 13.365, "action": "day_closes", "note": "Closed until Sunday 09:00", "params": {"day": "sat"}},
        {"at_hours": 16.649999999999995, "action": "set_release", "note": "CHA released", "params": {"set": "CHA"}},
        {"at_hours": 16.65, "action": "round", "note": "Round 3 starts", "params": {"name": "Sunday · Chamberí"}},
        {"at_hours": 16.7, "action": "grant_all", "note": "The Sunday allowance", "params": {"cash": 150}},
        {"at_hours": 9.15, "action": "persona_patch", "note": "Salamanca fever: Pilar pays 25 % over book",
         "params": {"id": "pilar"}},
        {"at_hours": 11.15, "action": "persona_patch", "note": "The fever breaks", "params": {"id": "pilar"}},
    ]
}  # fmt: skip
ROWS = schedule_rows(SCHEDULE)
CTX = {"tick": 1, "missing": ["RET-06", "LAT-09"]}
SECONDS = 30.0


def at(hours, rows=ROWS, ctx=CTX):
    return pb.instructions(rows, hours, SECONDS, ctx)


def said(found, agent, phase):
    return [i for i in found if i.agent == agent and i.phase == phase]


def test_before_a_duel_session_the_taker_opens_no_dealer_thread_and_nobody_deploys():
    found = at(11.65 - 5 * SECONDS / 3600)  # 5 ticks before Duels II
    assert pb.NO_NEW_DEALER_THREAD in {i.constraint for i in said(found, "taker", "before")}
    assert pb.NO_DEPLOY in {i.constraint for i in said(found, "duels", "before")}
    assert all(i.lead_ticks == 5 for i in found if i.action == "duels")


def test_during_a_session_the_duels_keep_the_accept_and_the_window_is_its_length():
    during = at(11.65 + 20 * SECONDS / 3600)  # 2 rounds x 16 ticks: still running at +20
    assert pb.YIELD_ACCEPTS in {i.constraint for i in said(during, "maker", "during")}
    assert not [i for i in at(11.65 + 70 * SECONDS / 3600) if i.action == "duels"]  # long over


def test_a_market_test_keeps_the_broker_up_before_and_during():
    before = at(13.0 - 3 * SECONDS / 3600)
    assert pb.KEEP_BROKER_UP in {i.constraint for i in said(before, "maker", "before")}
    during = at(13.0 + 10 * SECONDS / 3600)
    assert pb.KEEP_BROKER_UP in {i.constraint for i in said(during, "maker", "during")}


def test_sunday_round_release_and_allowance_say_the_ladder_restarts_and_how_to_spend():
    after = at(16.7 + 2 * 15.0 / 3600)
    texts = " | ".join(i.do for i in after)
    assert "the ladder restarts, 3 new scored deals per dealer level" in texts
    assert "+150 P for every team: spend plan, missing page cards first (RET-06, LAT-09)" in texts
    assert pb.PROTECT_NEW_PAGE in {i.constraint for i in after if i.action == "set_release"}
    assert "CHA is out" in texts


def test_the_end_of_a_fever_stops_the_sales_that_depended_on_it():
    after = at(11.15 + 3 * SECONDS / 3600)
    ends = [i for i in after if i.event_id.startswith("persona_patch:11.15")]
    assert ends and all("the fever is over" in i.do for i in ends)
    assert {i.agent for i in ends} == {"maker", "taker"}


def test_a_rumour_is_never_a_constraint_any_agent_obeys():
    teatime = NewsItem("news:9", "radio", "Abuela pays more for uncommon cards until teatime", "", 400, 9.18, False)
    rows = pb.rumour_rows([teatime], 9.5)
    book = pb.Playbook(lambda rows: None, lambda line: None)
    book.update(400, 9.2, SECONDS, [], CTX, rows)
    assert book.for_agent("taker") and "UNVERIFIED" in book.for_agent("taker")[0].do
    assert book.constraints("taker") == frozenset()  # only official events bind
    assert pb.rumour_rows([teatime], 9.0) == []  # not yet aired


def test_each_instruction_is_stored_once_with_its_context_and_survives_the_schedule_dropping_it():
    stored: list = []
    lines: list[str] = []
    book = pb.Playbook(stored.extend, lines.append)
    t = 13.0 - 2 * SECONDS / 3600
    book.update(1, t, SECONDS, [], {"tick": 1, "cash": 28}, ROWS)
    book.update(2, t + SECONDS / 3600, SECONDS, [], {"tick": 2}, ROWS)  # same instructions: not stored again
    keys = [r.detail["event_id"] for r in stored]
    assert len(keys) == len(set(keys)) and any(k.endswith(":maker:before") for k in keys)
    assert (
        stored[0].kind == "schedule" and stored[0].subject == "playbook" and stored[0].detail["context"]["cash"] == 28
    )
    book.update(3, 13.0 + 2 * SECONDS / 3600, SECONDS, [], {}, [])  # /api/schedule no longer lists the bench
    assert pb.NO_NEW_DEALER_THREAD in book.constraints("taker")  # still running: remembered


def test_a_store_that_raises_says_it_again_next_tick():
    calls = {"n": 0}

    def flaky(rows):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("db down")

    book = pb.Playbook(flaky, lambda line: None)
    t = 13.0 - 2 * SECONDS / 3600
    with pytest.raises(RuntimeError):
        book.update(1, t, SECONDS, [], {}, ROWS)
    assert book.update(2, t, SECONDS, [], {}, ROWS)  # nothing was marked said


def test_hostile_rows_never_raise():
    junk = [{"event_id": "x", "at_hours": float("nan")}, {"event_id": "y", "at_hours": "soon"},
            {"event_id": "z", "action": "bench", "at_hours": 1.0, "params": {"ticks": 10**9}}, {}]  # fmt: skip
    assert pb.instructions(junk, 1.0, SECONDS, {}) is not None
    assert pb.instructions(ROWS, 1.0, 0.0, {}) == []


def test_context_names_the_album_the_missing_cards_and_duplicates():
    card = lambda s, page=True: SimpleNamespace(set_code=s, page=page)  # noqa: E731
    market = SimpleNamespace(
        cash=28, held={"LAV-01": 2, "LAV-02": 0, "RET-06": 0}, released=("LAV", "RET"),
        cards={"LAV-01": card("LAV"), "LAV-02": card("LAV"), "RET-06": card("RET"), "CHA-01": card("CHA")},
    )  # fmt: skip
    ctx = pb.context_of(5, 11.6, market, teams=17)
    assert ctx["pages"] == {"LAV": "1/2", "RET": "0/1"} and ctx["missing"] == ["LAV-02", "RET-06"]
    assert ctx["duplicates"] == 1 and ctx["cash"] == 28 and ctx["teams"] == 17


# ---------------------------------------------------------------- the taker obeys (only more careful)


def test_the_taker_opens_no_dealer_thread_while_the_playbook_holds(tmp_path):
    from bazaar_agent.agents.taker import Taker, TakerConfig
    from tests.agent_fakes import FakePublic, FakeTeam, clock, parts

    book = pb.Playbook(lambda rows: None, lambda line: None)
    book.update(1, 13.0 - 2 * SECONDS / 3600, SECONDS, [], {}, ROWS)
    news = SimpleNamespace(playbook=book, matrix=None, upcoming=[], on_tick=lambda *a, **k: [])
    opened = []
    for enabled in (True, False):
        lines: list[str] = []
        team = FakeTeam()
        kw = parts(tmp_path, playbook_enabled=enabled)
        t = Taker(team, FakePublic(), live=True, log=lines.append, now=lambda: 1000.0, sleep=lambda s: None,
                  config=TakerConfig(max_dealer_threads=3), news=news, **kw)  # fmt: skip
        run = SimpleNamespace(snap=SimpleNamespace(clock=clock()))
        opened.append(t._playbook_holds(run, pb.NO_NEW_DEALER_THREAD))  # type: ignore[arg-type]
        assert opened[-1] == any("playbook holds no_new_dealer_thread" in line for line in lines)
    assert opened == [True, False]


def test_the_news_sentinel_builds_the_playbook_from_its_own_schedule_read(tmp_path):
    from bazaar_agent.news import NewsSentinel

    class Public:
        def __init__(self):
            self.calls: list[str] = []

        def call(self, method, path):
            self.calls.append(path)
            return SCHEDULE if path == "/api/schedule" else {}

    public, stored = Public(), []
    sentinel = NewsSentinel(public, stored.extend, lambda line: None, tmp_path)
    now = SimpleNamespace(t_hours=13.0 - 3 * SECONDS / 3600, tick_seconds=SECONDS)
    for tick in (1, 2, 3):
        sentinel.on_tick(tick, [], {}, now)
    assert pb.KEEP_BROKER_UP in sentinel.playbook.constraints("maker")
    assert any(r.subject == "playbook" for r in stored)
    assert public.calls == ["/api/news", "/api/schedule", "/api/levels"]  # no request of its own
