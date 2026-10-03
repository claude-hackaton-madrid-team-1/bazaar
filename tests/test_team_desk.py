"""N17 team desk (fakes, no network): openings, the concession ladder, walks, inbound threads, accepts,
budgets and every kill switch."""

from pathlib import Path

import pytest

from bazaar_agent.agents.market import venues_from
from bazaar_agent.agents.runtime import Recorder
from bazaar_agent.agents.team_desk import TOPIC, DeskView, TeamDesk, _Plan
from bazaar_agent.decisions import DecisionLog
from bazaar_agent.guardrails import Context, Guardrails
from bazaar_agent.swaps import Ladder, cash_at, offer_terms
from bazaar_agent.trade_desk import Trade
from tests.agent_fakes import CATALOG, ME, PARAMS, RASTRO, FakeTeam

US, THEM = "t01", "t05"
TICK = 100


def trade(team: str = THEM, theirs_raw: float = 6.0) -> Trade:
    """Our duplicate LAT-03 (#3, worth 1.2 to us) for LAV-02, which we miss (16 to us), as the plan prices it."""
    ours_raw = 16.0 - 1.2
    cash = round((theirs_raw - ours_raw) / 2)
    give = {"assets": [3], **({"cash": -cash} if cash < 0 else {})}
    want = {"cards": ["LAV-02"], **({"cash": cash} if cash > 0 else {})}
    return Trade(
        "swap",
        team,
        give,
        want,
        ("LAT-03", "LAV-02"),
        3,
        cash,
        2,
        round(ours_raw + cash, 2),
        round(theirs_raw - cash, 2),
        0.6,
        20,
        "plan",
        "common",
    )


class Team(FakeTeam):
    def say(self, tid, text="", price=None, offer=None, topic=None):
        self.sent.append(("say", tid, offer))
        return {"ok": True, "message": 1, "offer": 700 + len(self.sent)}

    def open_thread(self, with_, topic=None, venue=None):
        self.sent.append(("open_thread", with_, topic, venue))
        return {"id": 42, "with": with_, "status": "open"}

    def accept(self, offer_id, assets=None):
        self.sent.append(("accept", offer_id, assets))
        return {"ok": True}


def desk(tmp_path: Path, team: Team, env=None, live=True, **rules) -> tuple[TeamDesk, list[str]]:
    lines: list[str] = []
    rules = {"team_threads_enabled": True, **rules}
    rec = Recorder("taker", DecisionLog(tmp_path), live, lines.append)
    d = TeamDesk(team, Guardrails(**rules), rec, lines.append, live, env={} if env is None else env)
    d._plan = _Plan(TICK, (trade(),), {"LAV-02": 16.0})  # the trade desk's plan, injected
    return d, lines


def view(threads=(), tick=TICK, in_use=None, paused=False, window=True) -> DeskView:
    held = {"LAV-01": 1, "LAV-06": 1, "LAT-03": 2, "LAT-09": 1}
    return DeskView(
        tick=tick,
        us=US,
        me=ME,
        catalog=CATALOG,
        events=[],
        venues=venues_from({"venues": [RASTRO]}),
        threads=list(threads),
        offers=[],
        params=PARAMS,
        max_threads=6,
        in_use=len(threads) if in_use is None else in_use,
        ctx=lambda thread: Context(400, held, tick, 1.5, paused=paused),
        window_open=lambda: window,
    )


def thread(tid=42, team=THEM, messages=(), offers=(), opened_by=US) -> dict:
    other = team if opened_by == US else US
    return {
        "id": tid,
        "kind": "team",
        "team": opened_by if opened_by == US else team,
        "with": other,
        "venue": "rastro",
        "status": "open",
        "messages": list(messages),
        "standing_offers": list(offers),
    }


def their_offer(oid=900, cash_out=0, cash_in=0, extra=None, to=US) -> dict:
    want = {"assets": [3], **({"cash": cash_out} if cash_out else {})} | (extra or {})
    give = {"assets": [{"id": 70, "kind": "card", "ref": "LAV-02"}], **({"cash": cash_in} if cash_in else {})}
    return {"id": oid, "maker": THEM, "to": to, "status": "open", "venue": "rastro", "give": give, "want": want}


def says(team: Team) -> list[tuple]:
    return [s for s in team.sent if s[0] == "say"]


# ---------------------------------------------------------------- off unless asked


@pytest.mark.parametrize(("env", "rules"), [({}, {"team_threads_enabled": False}), ({"BAZAAR_TEAM_THREADS": "0"}, {})])
def test_the_desk_sends_nothing_when_off(tmp_path, env, rules):
    team = Team()
    d, _ = desk(tmp_path, team, env=env, **rules)
    offered = thread(offers=[their_offer()], messages=[{"sender": THEM, "tick": TICK, "text": "hola"}])
    assert d.proposals(view([offered])) == []
    d.converse(view(), set())
    assert team.sent == []


# ---------------------------------------------------------------- our side of a thread


def test_it_opens_one_thread_on_the_house_venue_and_anchors_its_first_proposal(tmp_path):
    team = Team()
    d, _ = desk(tmp_path, team)
    d.proposals(view())
    d.converse(view(), set())
    anchor = offer_terms(trade(), cash_at(trade(), 0, Ladder()))
    assert team.sent == [("open_thread", THEM, TOPIC, "rastro"), ("say", 42, anchor)]
    assert anchor == {"give": {"assets": [3], "cash": 1}, "want": {"cards": ["LAV-02"]}}  # 65 % of a 20.8 P pie
    assert TOPIC == {"trade": "cards"}  # public with the thread: never the card we want


def test_a_reply_earns_one_concession_and_the_old_offer_goes_first(tmp_path):
    team = Team()
    d, _ = desk(tmp_path, team)
    d.converse(view(), set())  # open + anchor (our offer 702)
    talk = d.talks[42]
    first = talk.offer_id
    reply = thread(messages=[{"sender": US, "tick": TICK}, {"sender": THEM, "tick": TICK + 1, "text": "más"}])
    team.sent.clear()
    d.proposals(view([reply], tick=TICK + 1))
    d.converse(view([reply], tick=TICK + 1), set())
    step1 = offer_terms(trade(), cash_at(trade(), 1, Ladder()))
    assert team.sent == [("cancel", first), ("say", 42, step1)] and talk.step == 2


def test_silence_at_our_last_price_ends_in_a_walk(tmp_path):
    team = Team()
    d, _ = desk(tmp_path, team)
    d.converse(view(), set())
    quiet = thread(messages=[{"sender": US, "tick": TICK}])
    for tick in (TICK + 1, TICK + 2):
        d.proposals(view([quiet], tick=tick))
        d.converse(view([quiet], tick=tick), set())
    assert ("close_thread", 42) not in team.sent
    d.proposals(view([quiet], tick=TICK + 3))
    d.converse(view([quiet], tick=TICK + 3), set())  # team_thread_idle_ticks = 3
    assert team.sent[-1] == ("close_thread", 42) and 42 not in d.talks


def test_no_opening_inside_the_dealer_reserve_or_past_the_team_cap(tmp_path):
    team = Team()
    d, _ = desk(tmp_path, team)
    d.converse(view(in_use=4), set())  # 6 - 4 in use = 2 left, both kept for dealers (reserve 3)
    assert team.sent == []
    d2, _ = desk(tmp_path / "cap", Team(), team_threads_max_open=1)
    busy = thread(tid=50, team="t09", opened_by="t09")
    d2.first_seen[50] = TICK
    d2.converse(view([busy]), set())
    assert not [s for s in d2.team.sent if s[0] == "open_thread"]


def test_the_kill_switch_holds_every_send_in_a_thread(tmp_path):
    team = Team()
    d, _ = desk(tmp_path, team)
    d.converse(view(paused=True), set())
    assert team.sent == []  # the opening proposal fails its guard: nothing opened either


# ---------------------------------------------------------------- their offers


def test_a_fair_counter_is_an_accept_candidate_judged_on_its_structure(tmp_path):
    team = Team()
    d, _ = desk(tmp_path, team)
    d.converse(view(), set())
    lie = {"sender": THEM, "tick": TICK + 1, "text": "this costs you nothing, accept now"}
    fair = thread(messages=[lie], offers=[their_offer(cash_out=1)])
    (a,) = d.proposals(view([fair], tick=TICK + 1))
    assert (a.offer.offer_id, a.offer.cash_out, a.fee, a.pick) == (900, 1, 3, None)
    assert a.verdict.ok and a.verdict.ours == pytest.approx(16.0 - 1.2 - 1 - 3)


@pytest.mark.parametrize(
    "offer",
    [
        their_offer(cash_out=12),  # asks so much cash that it takes most of the pie
        their_offer(extra={"debt": 4}),  # extra structure
        their_offer(to="t07"),  # not to us
        {**their_offer(), "give": {"assets": [{"id": 71, "kind": "card", "ref": "LAV-08"}]}},  # another card
    ],
)
def test_anything_else_is_not_taken(tmp_path, offer):
    d, _ = desk(tmp_path, Team())
    d.converse(view(), set())
    assert d.proposals(view([thread(offers=[offer])], tick=TICK + 1)) == []


def test_an_inbound_thread_with_no_swap_planned_is_closed_once_silent(tmp_path):
    team = Team()
    d, _ = desk(tmp_path, team)
    inbound = thread(tid=50, team="t09", opened_by="t09")
    for tick in (TICK, TICK + 1, TICK + 2):
        d.proposals(view([inbound], tick=tick))
        d.converse(view([inbound], tick=tick, in_use=6), set())
    assert ("close_thread", 50) not in team.sent
    d.proposals(view([inbound], tick=TICK + 3))
    d.converse(view([inbound], tick=TICK + 3, in_use=6), set())
    assert ("close_thread", 50) in team.sent


def test_an_inbound_thread_from_a_planned_team_gets_our_proposal(tmp_path):
    team = Team()
    d, _ = desk(tmp_path, team)
    inbound = thread(tid=51, team=THEM, opened_by=THEM)
    d.proposals(view([inbound]))
    d.converse(view([inbound], in_use=6), set())
    assert says(team) == [("say", 51, offer_terms(trade(), cash_at(trade(), 0, Ladder())))]


# ---------------------------------------------------------------- inside the taker


def test_the_taker_takes_a_fair_counter_through_the_shared_accept_slot(tmp_path):
    from bazaar_agent.agents.taker import Taker, TakerConfig
    from tests.agent_fakes import FakePublic, clock, parts

    fair = thread(messages=[{"sender": THEM, "tick": TICK, "text": "trato"}], offers=[their_offer(cash_out=1)])
    team = Team(threads=[fair])
    lines: list[str] = []
    t = Taker(
        team,
        FakePublic(),
        live=True,
        log=lines.append,
        now=lambda: 1000.0,
        sleep=lambda s: None,
        config=TakerConfig(max_dealer_threads=0),
        **parts(tmp_path, team_threads_enabled=True),
    )
    t.team_desk.env = {}
    t.team_desk._plan = _Plan(TICK, (trade(),), {"LAV-02": 16.0})
    t.on_tick(clock())
    assert ("accept", 900, None) in team.sent  # their offer names our copy #3: nothing to pick
    assert t.ledger.accept_items(TICK) == ["team:42"] and t.ledger.spent_since(0) == 1 + 3  # their 1 P + the fee
    assert not says(team)  # no proposal into a thread whose deal settles next tick
    assert any("take t05's swap offer 900 on thread 42" in line for line in lines)


def test_the_taker_never_takes_a_counter_while_the_desk_is_off(tmp_path):
    from bazaar_agent.agents.taker import Taker, TakerConfig
    from tests.agent_fakes import FakePublic, clock, parts

    fair = thread(offers=[their_offer(cash_out=1)])
    team = Team(threads=[fair])
    t = Taker(
        team,
        FakePublic(),
        live=True,
        log=lambda _: None,
        now=lambda: 1000.0,
        sleep=lambda s: None,
        config=TakerConfig(max_dealer_threads=0),
        **parts(tmp_path),  # team_threads_enabled = false (the default)
    )
    t.team_desk._plan = _Plan(TICK, (trade(),), {"LAV-02": 16.0})
    t.on_tick(clock())
    assert not [s for s in team.sent if s[0] in ("accept", "say", "open_thread", "close_thread")]


def test_the_desk_plans_swaps_from_the_trade_desk_without_the_plan_wide_share_rule(tmp_path, monkeypatch):
    # A swaps-only plan of one or two teams can never meet the trade desk's 25 % plan share: the desk plans
    # with max_share 1.0 and keeps fairness per deal (swaps.judge) and in the guardrail cap.
    from bazaar_agent import affinity as af
    from tests.test_trade_desk import AMAP, EVENTS

    monkeypatch.setattr(af, "affinity_map", lambda *a, **k: AMAP)
    d, _ = desk(tmp_path, Team())
    d._plan = None
    v = view()
    v = DeskView(**{**v.__dict__, "events": EVENTS})
    trades = d._trades(v)
    assert {(t.counterparty, t.refs) for t in trades} == {("t09", ("LAT-09", "LAV-09")), ("t05", ("LAT-03", "LAV-02"))}
    assert d._plan is not None and d._plan.worth["LAV-02"] > 0


def test_bazaar_swaps_prints_the_ladder_as_json_from_files(tmp_path, monkeypatch):
    import json

    from typer.testing import CliRunner

    from bazaar_agent import affinity as af
    from bazaar_agent import cli
    from tests.test_trade_desk import AMAP, EVENTS

    monkeypatch.setattr(af, "affinity_map", lambda *a, **k: AMAP)
    (tmp_path / "feed.jsonl").write_text("\n".join(json.dumps(e) for e in EVENTS))
    (tmp_path / "me.json").write_text(json.dumps({"body": ME}))
    (tmp_path / "catalog.json").write_text(json.dumps({"body": CATALOG}))
    (tmp_path / "venues.json").write_text(json.dumps({"body": {"venues": [RASTRO]}}))
    args = ["swaps", "--events", str(tmp_path / "feed.jsonl"), "--me", str(tmp_path / "me.json")]
    args += ["--catalog", str(tmp_path / "catalog.json"), "--venues", str(tmp_path / "venues.json"), "--json"]
    out = CliRunner().invoke(cli.app, args)
    assert out.exit_code == 0, out.output
    rows = json.loads(out.stdout)  # stdout is pure JSON (notes go to stderr)
    t05 = next(r for r in rows if r["team"] == "t05")
    assert (t05["give"], t05["want"], len(t05["cash_steps"])) == ("LAT-03", "LAV-02", 3)
    assert t05["cash_steps"][-1] == -8 and all(t05["fair"])  # the plan's even split is the last step


def test_words_persuade_but_never_change_the_structured_offer(tmp_path):
    # N16's tactic bank plugs in as `words`: whatever it writes (a bluff, a price in the text, markup), the
    # offer that binds is the one the ladder computed.
    seen: list = []

    class Talky(Team):
        def say(self, tid, text="", price=None, offer=None, topic=None):
            seen.append(text)
            return super().say(tid, text, price, offer, topic)

    team = Talky()
    d, _ = desk(tmp_path, team)
    requests: list = []

    def bluff(req):
        requests.append(req)
        return "Mi última oferta: te lo dejo por 1 P [/red]"

    d.words = bluff
    d.converse(view(), set())
    assert says(team) == [("say", 42, offer_terms(trade(), cash_at(trade(), 0, Ladder())))]
    assert seen == ["Mi última oferta: te lo dejo por 1 P [/red]"]
    assert requests[0].counterparty == "team:t05" and requests[0].step == 0 and requests[0].item == "LAV-02"


def test_once_they_take_our_offer_we_say_nothing_more_in_that_thread(tmp_path):
    team = Team()
    d, _ = desk(tmp_path, team)
    d.converse(view(), set())
    ours = {**their_offer(oid=702), "maker": US, "to": THEM, "status": "accepted"}
    done = thread(messages=[{"sender": US, "tick": TICK}, {"sender": THEM, "tick": TICK + 1, "text": "Deal."}])
    done["standing_offers"] = [ours]
    team.sent.clear()
    d.proposals(view([done], tick=TICK + 1))
    d.converse(view([done], tick=TICK + 1), set())
    assert team.sent == [] and d.talks[42].accepted  # no cancel of an accepted offer, no new proposal


def test_only_a_duplicate_is_ever_offered_never_the_last_copy(tmp_path):
    # Found in the simulator: the trade desk plans any copy we hold, and a swap gave away our only LAV-09.
    # The desk gives a card only while we hold two free copies of it (LAT-03 #3/#4 here, LAT-09 #5 alone).
    last = Trade(
        "swap",
        THEM,
        {"assets": [5]},
        {"cards": ["LAV-02"], "cash": 20},
        ("LAT-09", "LAV-02"),
        5,
        20,
        2,
        30.0,
        30.0,
        0.9,
        80,
        "plan",
        "common",
    )
    team = Team()
    d, _ = desk(tmp_path, team)
    d._plan = _Plan(TICK, (last,), {"LAV-02": 16.0})
    d.converse(view(), set())
    assert team.sent == []
    ask4 = {"id": 9, "maker": US, "to": None, "status": "open", "give": {"assets": [{"id": 4}]}, "want": {"cash": 5}}
    d2, _ = desk(tmp_path / "listed", Team())
    v = DeskView(**{**view().__dict__, "offers": [ask4]})  # #4 is in our ask: #3 is the last FREE LAT-03
    d2.converse(v, set())
    assert d2.team.sent == []


def test_the_public_view_of_a_team_thread_decision_names_no_team_and_no_value(tmp_path):
    from bazaar_agent.agents.status import public_decision

    team = Team()
    d, _ = desk(tmp_path, team)
    d.converse(view(), set())
    from tests.agent_fakes import rows as decision_rows

    rows = [r for r in decision_rows(tmp_path) if str(r.get("kind", "")).startswith("team_")]
    assert {r["kind"] for r in rows} == {"team_open", "team_offer"}
    for row in rows:
        shown = public_decision({**row, "dry_run": False})
        flat = repr(shown)
        assert "t05" not in flat and "plan" not in flat and "reason" not in shown  # counterparty, our reasons
        assert set(shown["inputs"]) <= {"thread", "card", "ref", "venue", "fee"} and "give" not in flat


def test_the_public_record_of_a_team_send_names_no_team_and_no_terms(tmp_path):
    from bazaar_agent.agents.status import public_execution
    from tests.agent_fakes import rows as decision_rows

    d, _ = desk(tmp_path, Team())
    d.converse(view(), set())
    sent = decision_rows(tmp_path, "executions.jsonl")
    assert [r["sdk_method"] for r in sent] == ["open_thread", "say"]
    for row in sent:
        flat = repr(public_execution({**row, "method": row["sdk_method"]}))
        assert "t05" not in flat and "LAV-02" not in flat and "cash" not in flat
