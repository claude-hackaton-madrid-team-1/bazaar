"""N17 team desk (fakes, no network): openings, the concession ladder, walks, inbound threads, accepts,
budgets and every kill switch."""

from pathlib import Path

import pytest

from bazaar_agent.agents.market import venues_from
from bazaar_agent.agents.runtime import Recorder
from bazaar_agent.agents.team_desk import TOPIC, DeskView, SwapAccept, TeamDesk, _Plan
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
        t_hours=1.5,
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
    assert {r["team"] for r in rows} == {"t05"}  # t09's swap would give our only LAT-09: the desk never would
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
        assert set(shown["inputs"]) <= {"thread", "venue", "fee"} and "give" not in flat and "LAV-02" not in flat


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


def test_a_dry_run_and_a_spent_tick_send_nothing(tmp_path):
    dry = Team()
    d, lines = desk(tmp_path / "dry", dry, live=False)
    d.converse(view(), set())
    assert dry.sent == [] and any("open a swap thread with t05" in line for line in lines)  # logged, not sent
    late = Team()
    d2, _ = desk(tmp_path / "late", late)
    d2.converse(view(window=False), set())
    assert late.sent == []  # past the tick's send window: dropped, never sent late


def test_trading_disabled_in_guardrails_holds_the_desk(tmp_path, monkeypatch):
    from bazaar_agent import guardrails as gr

    monkeypatch.setattr(gr, "_file_stop", lambda path: "trading_enabled = false")
    team = Team()
    d, _ = desk(tmp_path, team, trading_enabled=False)
    d.converse(view(), set())
    assert team.sent == []


def test_after_a_walk_the_team_rests_and_a_stuck_accepted_deal_frees_its_slot(tmp_path):
    from bazaar_agent.agents.team_desk import REST_TICKS

    team = Team()
    d, _ = desk(tmp_path, team)
    d.plan_ttl = 10**6  # keep the injected plan: the rest, not an empty plan, must be what stops a reopening
    d.converse(view(), set())
    quiet = thread(messages=[{"sender": US, "tick": TICK}])
    for tick in range(TICK + 1, TICK + 4):
        d.proposals(view([quiet], tick=tick))
        d.converse(view([quiet], tick=tick), set())
    assert team.sent[-1] == ("close_thread", 42)
    team.sent.clear()
    for tick in range(TICK + 4, TICK + 3 + REST_TICKS):  # the same plan, the team rests: no reopening
        d.proposals(view(tick=tick))
        d.converse(view(tick=tick), set())
    assert team.sent == []
    d.converse(view(tick=TICK + 3 + REST_TICKS), set())
    assert team.sent[0] == ("open_thread", THEM, TOPIC, "rastro")
    stuck = Team()
    d2, _ = desk(tmp_path / "stuck", stuck)
    d2.converse(view(), set())
    d2.talks[42].accepted = True  # we (or they) accepted, and the deal never settled
    open_ = thread(messages=[{"sender": US, "tick": TICK}])
    for tick in range(TICK + 1, TICK + 5):
        d2.proposals(view([open_], tick=tick))
        d2.converse(view([open_], tick=tick), set())
    assert ("close_thread", 42) in stuck.sent


# ---------------------------------------------------------------- review round 1 (#123)


def ledger_desk(tmp_path, team, **rules):
    from bazaar_agent.guardrails import Ledger

    d, lines = desk(tmp_path, team, **rules)
    d.ledger = Ledger(tmp_path / "ledger.jsonl")
    d.plan_ttl = 10**6
    return d, lines


def test_the_cash_we_add_is_booked_as_spend_when_they_take_our_offer(tmp_path):
    # pr-reviewer + security-auditor #123 P1: the cash leg of OUR offer escaped max_spend_per_game_hour. It is
    # booked when the offer is POSTED (as a bid is), so no take can slip past it (security r3 P1).
    team = Team()
    d, _ = ledger_desk(tmp_path, team)
    d.converse(view(), set())  # anchor: our LAT-03 + 1 P for their LAV-02 (offer 702)
    assert d.ledger.spent_since(0) == 1 and d.ledger.count_in_tick("listing", TICK) == 1  # booked at the post
    taken = thread(messages=[{"sender": US, "tick": TICK}, {"sender": THEM, "tick": TICK + 1, "text": "Deal."}])
    ours = {"id": 702, "maker": US, "to": THEM, "thread": 42, "status": "accepted", "venue": "rastro"}
    taken["standing_offers"] = [
        ours | {"give": {"assets": [{"id": 3, "ref": "LAT-03"}], "cash": 1}, "want": {"cards": ["LAV-02"]}}
    ]
    d.proposals(view([taken], tick=TICK + 1))
    d.converse(view([taken], tick=TICK + 1), set())
    assert d.ledger.spent_since(0) == 1 and d.talks[42].accepted and not says(team)[1:]
    d.converse(view([], tick=TICK + 2), set())  # it settled: the thread is gone, nothing booked twice
    assert d.ledger.spent_since(0) == 1 and d.deals[THEM] == 1


def test_a_deal_we_never_saw_accepted_is_booked_when_the_thread_ends(tmp_path):
    team = Team(thread_payloads={42: {"id": 42, "status": "deal", "messages": [], "standing_offers": []}})
    d, _ = ledger_desk(tmp_path, team)
    d.converse(view(), set())
    d.proposals(view([], tick=TICK + 1))
    d.converse(view([], tick=TICK + 1), set())  # the thread left the open list: it ended in a deal
    assert d.ledger.spent_since(0) == 1 and d.deals[THEM] == 1


def test_a_refused_cancel_never_leaves_two_standing_offers(tmp_path):
    from bazaar_agent.sdk import BazaarError

    class NoCancel(Team):
        def cancel(self, offer_id):
            self.sent.append(("cancel", offer_id))
            raise BazaarError("rate_limited", "slow down", 429)

    team = NoCancel()
    d, _ = desk(tmp_path, team)
    d.converse(view(), set())
    reply = thread(messages=[{"sender": US, "tick": TICK}, {"sender": THEM, "tick": TICK + 1, "text": "más"}])
    reply["standing_offers"] = [{**their_offer(oid=702), "maker": US, "to": THEM}]
    team.sent.clear()
    d.proposals(view([reply], tick=TICK + 1))
    d.converse(view([reply], tick=TICK + 1), set())
    assert team.sent == [("cancel", 702)]  # no new offer while the old one may still stand


def test_after_a_restart_our_own_thread_is_picked_up_where_it_was(tmp_path):
    team = Team()
    d, _ = desk(tmp_path, team)  # a fresh desk: no talks in memory
    ours = {"id": 702, "maker": US, "to": THEM, "thread": 42, "status": "open", "venue": "rastro"}
    ours |= {"give": {"assets": [{"id": 3}], "cash": 1}, "want": {"cards": ["LAV-02"]}}
    mid = thread(
        messages=[{"sender": US, "tick": TICK - 2, "offer": ours}, {"sender": THEM, "tick": TICK - 1, "text": "?"}]
    )
    mid["standing_offers"] = [ours]
    d.proposals(view([mid]))
    d.converse(view([mid], in_use=6), set())
    step1 = offer_terms(trade(), cash_at(trade(), 1, Ladder()))
    assert team.sent == [("cancel", 702), ("say", 42, step1)]  # the old offer first, then the next step


def test_a_rival_that_keeps_writing_is_walked_at_our_last_price(tmp_path):
    team = Team()
    d, _ = desk(tmp_path, team)
    d.plan_ttl = 10**6
    d.converse(view(), set())
    for tick in range(TICK + 1, TICK + 12):
        chatty = thread(messages=[{"sender": US, "tick": TICK}, {"sender": THEM, "tick": tick, "text": "hmm"}])
        d.proposals(view([chatty], tick=tick))
        d.converse(view([chatty], tick=tick), set())
        if ("close_thread", 42) in team.sent:
            break
    assert ("close_thread", 42) in team.sent and len(says(team)) == Ladder().steps  # 3 proposals, then a walk


def test_only_threads_on_the_house_venue_are_answered(tmp_path):
    team = Team()
    d, _ = desk(tmp_path, team)
    elsewhere = {**thread(tid=60, opened_by=THEM, offers=[their_offer(cash_out=1)]), "venue": "v02"}
    assert d.proposals(view([elsewhere])) == []  # a rival's venue earns its owner market-making points
    for tick in (TICK, TICK + 1, TICK + 2, TICK + 3):
        d.proposals(view([elsewhere], tick=tick))
        d.converse(view([elsewhere], tick=tick, in_use=6), set())
    assert ("close_thread", 60) in team.sent and not says(team)


def test_our_own_offer_in_the_thread_does_not_hide_the_duplicate_but_an_ask_elsewhere_does(tmp_path):
    team = Team()
    d, _ = desk(tmp_path, team)
    d.converse(view(), set())
    in_thread = {"id": 702, "maker": US, "to": THEM, "thread": 42, "status": "open", "give": {"assets": [{"id": 3}]}}
    counter = thread(offers=[their_offer(cash_out=1)])
    v = DeskView(**{**view([counter], tick=TICK + 1).__dict__, "offers": [in_thread]})
    assert len(d.proposals(v)) == 1  # #3 is offered in this very thread: still a free duplicate for this swap
    ask4 = {"id": 9, "maker": US, "to": None, "status": "open", "give": {"assets": [{"id": 4}]}, "want": {"cash": 5}}
    names4 = thread(offers=[{**their_offer(cash_out=1), "want": {"assets": [4], "cash": 1}}])
    v2 = DeskView(**{**view([names4], tick=TICK + 1).__dict__, "offers": [ask4]})
    assert d.proposals(v2) == []  # #4 is in our ask on the board: never handed over in a swap


def test_turning_the_desk_off_withdraws_our_threads(tmp_path):
    team = Team()
    d, _ = desk(tmp_path, team)
    d.converse(view(), set())
    d.env = {"BAZAAR_TEAM_THREADS": "0"}
    d.converse(view([thread()], tick=TICK + 1), set())
    assert team.sent[-1] == ("close_thread", 42) and d.talks == {}  # closing cancels our offer there


def test_a_malformed_side_is_unreadable_never_a_crash():
    from bazaar_agent.swaps import read_offer

    for bad in ({"cards": "LAV-02"}, {"assets": 3}, {"types": {"card": "LAV-02"}}):
        assert read_offer({**their_offer(), "want": bad}, US) is None


def test_a_team_desk_error_never_costs_the_taker_its_tick(tmp_path):
    from bazaar_agent.agents.taker import Taker, TakerConfig
    from tests.agent_fakes import FakePublic, ask, clock, parts

    team = Team()
    lines: list[str] = []
    t = Taker(
        team,
        FakePublic(boards={"rastro": [ask(77, "LAV-02", 5)]}),
        live=True,
        log=lines.append,
        now=lambda: 1000.0,
        sleep=lambda s: None,
        config=TakerConfig(max_dealer_threads=0),
        **parts(tmp_path, team_threads_enabled=True),
    )

    def boom(view):
        raise RuntimeError("bad payload")

    t.team_desk.proposals = boom  # type: ignore[method-assign]
    t.on_tick(clock())
    assert ("accept", 77, None) in team.sent  # the board buy still happened
    assert any("team desk: proposals failed (RuntimeError: bad payload)" in line for line in lines)


def test_a_ledger_outage_inside_the_desk_still_stops_the_taker_tick(tmp_path):
    from bazaar_agent.agents.taker import Taker, TakerConfig
    from bazaar_agent.ledger_pg import LedgerUnavailable
    from tests.agent_fakes import FakePublic, clock, parts

    lines: list[str] = []
    t = Taker(
        Team(),
        FakePublic(),
        live=True,
        log=lines.append,
        now=lambda: 1000.0,
        sleep=lambda s: None,
        config=TakerConfig(max_dealer_threads=0),
        **parts(tmp_path, team_threads_enabled=True),
    )

    def down(view):
        raise LedgerUnavailable("ledger write failed (OperationalError)")

    t.team_desk.proposals = down  # type: ignore[method-assign]
    t.on_tick(clock())
    assert any("no write this tick (fail closed)" in line for line in lines)  # not swallowed by the desk guard


def test_after_a_restart_a_desk_turned_off_withdraws_and_refunds_what_it_withdrew(tmp_path):
    # security-auditor #123 r2-r4: turning the desk off is a restart on Railway, so the desk has no memory of
    # its threads. Spend was booked when each offer was POSTED (by the previous process); off, the desk closes
    # the thread holding an OPEN offer of ours and gives its spend back once a read shows that offer dead.
    from bazaar_agent.guardrails import Ledger

    closed = {"id": 42, "status": "closed", "messages": [], "standing_offers": [{"id": 702, "status": "cancelled"}]}
    team = Team(thread_payloads={42: closed})
    d, _ = desk(tmp_path, team, env={"BAZAAR_TEAM_THREADS": "0"})
    d.ledger = Ledger(tmp_path / "ledger.jsonl")
    d.ledger.record("spend", TICK - 2, 1.45, 1, "LAV-02")  # offer 702, posted before the restart
    d.ledger.record("spend", TICK - 2, 1.45, 4, "LAV-02")  # offer 705, posted before the restart
    base = {"maker": US, "to": THEM, "venue": "rastro", "want": {"cards": ["LAV-02"]}, "created_tick": TICK - 2}
    standing = base | {"id": 702, "thread": 42, "status": "open", "give": {"assets": [{"id": 3}], "cash": 1}}
    taken = base | {"id": 705, "thread": 43, "status": "accepted", "give": {"assets": [{"id": 4}], "cash": 4}}
    v = DeskView(**{**view([thread(), thread(tid=43)]).__dict__, "offers": [standing, taken]})
    d.proposals(v)
    d.converse(v, set())
    assert ("close_thread", 42) in team.sent and ("close_thread", 43) not in team.sent  # 43 settles: a deal
    assert d.ledger.spent_since(0) == 5  # nothing given back before a read shows the offer dead
    v2 = DeskView(**{**view([thread(tid=43)], tick=TICK + 1).__dict__, "offers": [taken]})
    d.proposals(v2)
    d.converse(v2, set())
    d.converse(v2, set())  # read again: nothing refunded twice
    assert d.ledger.spent_since(0) == 4


def test_an_adopted_thread_remembers_the_cash_of_our_standing_offer(tmp_path):
    team = Team()
    d, _ = desk(tmp_path, team)
    ours = {"id": 702, "maker": US, "to": THEM, "thread": 42, "status": "open", "venue": "rastro"}
    ours |= {"give": {"assets": [{"id": 3}], "cash": 5}, "want": {"cards": ["LAV-02"]}}
    quiet = thread(messages=[{"sender": US, "tick": TICK - 1, "offer": ours}])
    quiet["standing_offers"] = [ours]
    d.proposals(view([quiet]))
    d.converse(view([quiet], in_use=6), set())
    assert d.talks[42].cash == -5 and d.talks[42].offer_id == 702


def test_a_thread_whose_end_cannot_be_read_is_kept_until_it_can(tmp_path):
    from bazaar_agent.guardrails import Ledger
    from bazaar_agent.sdk import BazaarError

    class Flaky(Team):
        reads = 0
        ours: int | None = None

        def thread(self, tid):
            Flaky.reads += 1
            if Flaky.reads == 1:
                raise BazaarError("rate_limited", "slow down", 429)
            gone = [{"id": Flaky.ours, "status": "cancelled"}]
            return {"id": tid, "status": "closed", "messages": [], "standing_offers": gone}

    team = Flaky()
    d, _ = desk(tmp_path, team)
    d.ledger = Ledger(tmp_path / "ledger.jsonl")
    d.converse(view(), set())  # our anchor adds 1 P, booked at the post
    Flaky.ours = d.talks[42].offer_id
    d._plan, d.plan_ttl = _Plan(TICK, (), {}), 10**6  # nothing else to open: thread 42 is the only one
    d.converse(view([], tick=TICK + 1), set())  # gone from the open list; its status read is refused
    assert 42 in d.talks and d.ledger.spent_since(0) == 1
    d.converse(view([], tick=TICK + 2), set())  # read again: closed, our offer cancelled: the spend comes back
    assert 42 not in d.talks and d.ledger.spent_since(0) == 0


def test_a_thread_with_no_venue_is_not_answered(tmp_path):
    d, _ = desk(tmp_path, Team())
    d.converse(view(), set())
    nameless = {**thread(offers=[their_offer(cash_out=1)]), "venue": None}
    assert d.proposals(view([nameless], tick=TICK + 1)) == []


# ---------------------------------------------------------------- review round 2 (#123)


def test_a_pending_deal_that_never_settles_is_walked_whatever_they_write(tmp_path):
    team = Team()
    d, _ = desk(tmp_path, team)
    d.plan_ttl = 10**6
    d.converse(view(), set())
    d.talks[42].accepted, d.talks[42].sent_tick = True, TICK
    for tick in range(TICK + 1, TICK + 8):
        chatty = thread(messages=[{"sender": THEM, "tick": tick, "text": "un momento"}])
        d.proposals(view([chatty], tick=tick))
        d.converse(view([chatty], tick=tick), set())
    assert ("close_thread", 42) in team.sent


def test_an_adopted_thread_whose_offer_was_taken_gets_no_new_proposal(tmp_path):
    from bazaar_agent.guardrails import Ledger

    team = Team()
    d, _ = desk(tmp_path, team)  # after a restart: no memory (the take's spend was booked at its post)
    d.ledger = Ledger(tmp_path / "ledger.jsonl")
    taken = {"id": 702, "maker": US, "to": THEM, "thread": 42, "status": "accepted", "venue": "rastro"}
    taken |= {"give": {"assets": [{"id": 3}], "cash": 1}, "want": {"cards": ["LAV-02"]}}
    pending = thread(messages=[{"sender": US, "tick": TICK - 1, "offer": taken}], offers=[taken])
    counter = their_offer(oid=900, cash_out=1)  # their earlier counter still stands: never a second deal
    pending["standing_offers"] = [taken, {**counter, "want": {"assets": [4], "cash": 1}}]
    assert d.proposals(view([pending])) == []
    d.converse(view([pending], in_use=6), set())
    assert not says(team) and ("close_thread", 42) not in team.sent and d.ledger.spent_since(0) == 0


def test_a_take_in_the_tick_the_desk_goes_off_is_left_to_settle(tmp_path):
    from bazaar_agent.guardrails import Ledger

    team = Team()
    d, _ = desk(tmp_path, team, env={"BAZAAR_TEAM_THREADS": "0"})
    d.ledger = Ledger(tmp_path / "ledger.jsonl")
    taken = {"id": 702, "maker": US, "to": THEM, "thread": 42, "status": "accepted", "venue": "rastro"}
    taken |= {"give": {"assets": [{"id": 3}], "cash": 1}, "want": {"cards": ["LAV-02"]}, "created_tick": TICK}
    open_ = {**taken, "id": 703, "thread": 43, "status": "open"}
    v = view([thread(offers=[taken]), thread(tid=43)])  # the take shows in the thread only, not in our offers
    v = DeskView(**{**v.__dict__, "offers": [open_]})
    d.proposals(v)
    d.converse(v, set())
    assert team.sent == [("close_thread", 43)] and 703 in d.to_check and 702 not in d.to_check


def test_the_desk_waits_when_the_ticks_listings_are_used(tmp_path):
    from bazaar_agent.guardrails import Ledger

    team = Team()
    d, lines = desk(tmp_path, team)
    d.ledger = Ledger(tmp_path / "ledger.jsonl")
    for _ in range(12):
        d.ledger.record("listing", TICK, 1.5, 0, "maker")
    d.converse(view(), set())
    assert not says(team) and any("this tick's 12 listings are used" in line for line in lines)


def test_a_ledger_outage_while_planning_is_not_swallowed(tmp_path):
    from bazaar_agent.ledger_pg import LedgerUnavailable

    d, _ = desk(tmp_path, Team())
    d._plan = None

    def down(thread):
        raise LedgerUnavailable("ledger read failed")

    v = DeskView(**{**view().__dict__, "ctx": down})
    with pytest.raises(LedgerUnavailable):
        d._trades(v)


# ---------------------------------------------------------------- review round 3 (#123): book at the post


def test_a_concession_refunds_the_old_cash_and_books_the_new(tmp_path):
    from bazaar_agent.guardrails import Ledger

    team = Team()
    d, _ = desk(tmp_path, team)
    d.ledger = Ledger(tmp_path / "ledger.jsonl")
    d.converse(view(), set())  # anchor: + 1 P
    reply = thread(messages=[{"sender": US, "tick": TICK}, {"sender": THEM, "tick": TICK + 1, "text": "más"}])
    d.proposals(view([reply], tick=TICK + 1))
    d.converse(view([reply], tick=TICK + 1), set())  # step 1: + 3 P, the old offer cancelled first
    assert cash_at(trade(), 1, Ladder()) == -3 and d.ledger.spent_since(0) == 3


def test_a_refused_send_gives_its_spend_back(tmp_path):
    from bazaar_agent.guardrails import Ledger
    from bazaar_agent.sdk import BazaarError

    class Refuses(Team):
        def say(self, tid, text="", price=None, offer=None, topic=None):
            self.sent.append(("say", tid, offer))
            raise BazaarError("too_many_offers", "at most 30 open offers", 400)

    d, _ = desk(tmp_path, Refuses())
    d.ledger = Ledger(tmp_path / "ledger.jsonl")
    d.converse(view(), set())
    assert d.ledger.spent_since(0) == 0 and d.talks[42].step == 0  # booked before the send, refunded after


def test_taking_a_counter_cancels_our_own_offer_in_that_thread_first(tmp_path):
    # security-auditor #123 r3 P1: our offer stood while we took their counter, so both copies could leave.
    from bazaar_agent.agents.taker import Taker, TakerConfig
    from bazaar_agent.sdk import BazaarError
    from tests.agent_fakes import FakePublic, clock, parts

    ours = {"id": 702, "maker": US, "to": THEM, "thread": 42, "status": "open", "venue": "rastro"}
    ours |= {"give": {"assets": [{"id": 3}], "cash": 1}, "want": {"cards": ["LAV-02"]}, "created_tick": TICK - 1}
    counter = {**their_offer(cash_out=1), "want": {"assets": [4], "cash": 1}}  # names our OTHER copy

    def taker(tmp, team):
        t = Taker(
            team,
            FakePublic(),
            live=True,
            log=lambda _: None,
            now=lambda: 1000.0,
            sleep=lambda s: None,
            config=TakerConfig(max_dealer_threads=0),
            **parts(tmp, team_threads_enabled=True),
        )
        t.team_desk.env = {}
        t.team_desk._plan = _Plan(TICK, (trade(),), {"LAV-02": 16.0})
        return t

    team = Team(threads=[thread(offers=[ours, counter])], offers=[ours])
    taker(tmp_path / "ok", team).on_tick(clock())
    assert team.sent[:2] == [("cancel", 702), ("accept", 900, None)]

    class NoCancel(Team):
        def cancel(self, offer_id):
            self.sent.append(("cancel", offer_id))
            raise BazaarError("rate_limited", "slow down", 429)

    stuck = NoCancel(threads=[thread(offers=[ours, counter])], offers=[ours])
    taker(tmp_path / "stuck", stuck).on_tick(clock())
    assert ("cancel", 702) in stuck.sent and not [s for s in stuck.sent if s[0] == "accept"]


def test_an_offer_of_ours_left_in_a_thread_whose_deal_settled_is_cancelled(tmp_path):
    from bazaar_agent.guardrails import Ledger

    team = Team()
    d, _ = desk(tmp_path, team)
    d.ledger = Ledger(tmp_path / "ledger.jsonl")
    d.converse(view(), set())
    d.talks[42].accepted = True  # their counter was taken
    left = {"id": 702, "maker": US, "to": THEM, "thread": 42, "status": "open", "created_tick": TICK}
    left |= {"give": {"assets": [{"id": 3}], "cash": 1}, "want": {"cards": ["LAV-02"]}}
    v = DeskView(**{**view([], tick=TICK + 1).__dict__, "offers": [left]})
    d.converse(v, set())
    assert team.sent[-1] == ("cancel", 702) and d.ledger.spent_since(0) == 0


def test_a_team_thread_swap_is_not_counted_again_as_thread_cash():
    from bazaar_agent.agents.seller import open_commitments

    swap = {"id": 1, "maker": US, "to": THEM, "thread": 42, "status": "open", "give": {"cash": 4, "assets": []}}
    dealer = {"id": 2, "maker": US, "to": "abuela", "thread": 7, "status": "open", "give": {"cash": 9}}
    c = open_commitments([swap, dealer], US)
    assert (c.cash, c.thread_cash) == (13, 9)  # both promise cash; only the dealer bid waits to be booked


# ---------------------------------------------------------------- review round 4 (#123): refunds follow the offer


def test_a_rival_that_takes_our_offer_then_closes_the_thread_gets_no_refund_from_us(tmp_path):
    # security-auditor #123 r4 P1: a close cancels only OPEN offers; an accepted one still settles. The spend
    # comes back only when a read shows our offer dead, never because the thread reads closed.
    from bazaar_agent.guardrails import Ledger

    team = Team()
    d, _ = desk(tmp_path, team)
    d.ledger = Ledger(tmp_path / "ledger.jsonl")
    d.converse(view(), set())  # anchor: + 1 P booked
    oid = d.talks[42].offer_id
    team.thread_payloads[42] = {"id": 42, "status": "closed", "standing_offers": [{"id": oid, "status": "settled"}]}
    d._plan, d.plan_ttl = _Plan(TICK, (), {}), 10**6
    for tick in range(TICK + 1, TICK + 14):
        d.proposals(view([], tick=tick))
        d.converse(view([], tick=tick), set())
    assert d.ledger.spent_since(0) == 1 and d.to_check == {}


def test_a_cancel_answered_settled_keeps_the_spend(tmp_path):
    from bazaar_agent.guardrails import Ledger

    class LateCancel(Team):
        def cancel(self, offer_id):
            self.sent.append(("cancel", offer_id))
            return {"id": offer_id, "status": "settled"}  # it settled before our cancel landed

    team = LateCancel()
    d, _ = desk(tmp_path, team)
    d.ledger = Ledger(tmp_path / "ledger.jsonl")
    d.converse(view(), set())  # anchor: + 1 P
    reply = thread(messages=[{"sender": US, "tick": TICK}, {"sender": THEM, "tick": TICK + 1, "text": "más"}])
    d.proposals(view([reply], tick=TICK + 1))
    d.converse(view([reply], tick=TICK + 1), set())  # the concession's cancel answers settled: + 3 P, no refund
    assert d.ledger.spent_since(0) == 1 + 3


def test_a_team_accept_shows_only_its_thread_and_fee_on_the_public_view():
    from bazaar_agent.agents.status import public_decision
    from bazaar_agent.agents.taker import swap_proposal
    from bazaar_agent.swaps import SwapVerdict, read_offer

    offer = read_offer(their_offer(cash_out=1), US)
    assert offer is not None
    a = SwapAccept(42, offer, trade(), SwapVerdict(True, 10.8, 9.0, "fair"), 3, None)
    row = {"kind": "team_accept", "status": "approved", "dry_run": False, "inputs": swap_proposal(a).inputs}
    assert public_decision(row)["inputs"] == {"thread": 42, "fee": 3}
