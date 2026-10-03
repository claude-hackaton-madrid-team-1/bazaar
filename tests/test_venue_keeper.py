"""Our venue inside the maker: opened once at game hour 6.5 (board, 0 bps), the broker key kept in the vault and
never shown, the bond reserve, idempotence, and the broker matching every tick after that. No network."""

import json

import pytest
from pydantic import SecretStr

from bazaar_agent import venue as vn
from bazaar_agent.agents import venue_keeper as vk
from bazaar_agent.agents.broker import BrokerConfig
from bazaar_agent.agents.market import venues_from
from bazaar_agent.agents.runtime import Snapshot, TickWindow
from bazaar_agent.agents.status import StatusHub
from bazaar_agent.config import Settings
from bazaar_agent.decisions import DecisionLog
from bazaar_agent.guardrails import Guardrails
from bazaar_agent.sdk import BazaarError
from bazaar_agent.ticks import Clock
from tests.agent_fakes import RASTRO, rows
from tests.test_broker_agent import FakeBroker
from tests.test_matcher import bench_buy, bench_sell
from tests.test_venue import FakeConn

KEY = "simbk-" + "K3y0nlyF0rTests"  # a shape, not a real key


class Team:
    """The team routes the keeper uses: open_venue only (it reads /me through the maker's snapshot)."""

    def __init__(self, refuse=None):
        self.refuse = refuse
        self.opened: list[tuple] = []

    def open_venue(self, name, fee_bps=300, fee_per_card=0, rules=None, description=""):
        self.opened.append((name, fee_bps, fee_per_card, rules))
        if self.refuse is not None:
            raise self.refuse
        return {"venue": "v09", "broker_key": KEY, "name": name, "fee_bps": fee_bps, "fee_per_card": fee_per_card}


def ours(status="open", **kw):
    return {"venue": "v09", "owner": "t01", "status": status, "rules": {"mechanism": "board"}, "fee_bps": 0, **kw}


def snap(tick=400, t_hours=6.5, cash=520, venue=None, venues=(RASTRO,), offers=None):
    c = Clock(tick=tick, t_hours=t_hours, tick_seconds=30.0, next_tick_in=25.0)
    me = {"id": "t01", "cash": cash, "assets": [], "venue": venue}
    return Snapshot(c, me, {"offers": offers or []}, {}, [], venues_from({"venues": list(venues)}), [])


def window(open_=True):
    return TickWindow(0, 1e12 if open_ else -1.0)


def keeper(tmp_path, team, *, live=True, store=None, db_down=False, broker=None, lines=None, hub=None, **rules):
    rules = {
        "allow_venue_open": True,
        "cash_floor": 100,
        "venue_bond_reserve": 270,
        "venue_open_after_game_hours": 6.5,
        "pause_file": str(tmp_path / "PAUSE"),
        **rules,
    }
    store = {} if store is None else store
    connect = (lambda: FakeConn(store, fail=db_down)) if store is not False else None
    made: list[SecretStr] = []

    def make_broker(key):
        made.append(key)
        return broker if broker is not None else FakeBroker()

    k = vk.VenueKeeper(
        team,
        settings=Settings(data_dir=tmp_path),
        rules=Guardrails(**rules),
        vault=vn.KeyVault(tmp_path, connect),
        decisions=DecisionLog(tmp_path),
        live=live,
        log=(lines.append if lines is not None else lambda line: None),
        hub=hub,
        broker_config=BrokerConfig(pace_s=0.0),
        make_broker=make_broker,
        stats_dir=tmp_path / "agents",
    )
    k.made = made  # type: ignore[attr-defined]
    return k


def everything_written(tmp_path, lines):
    texts = [p.read_text() for p in tmp_path.rglob("*.jsonl")] + list(lines)
    return "\n".join(texts)


# ---------------------------------------------------------------- when it opens


def test_nothing_happens_before_game_hour_6_5(tmp_path):
    team, store = Team(), {}
    k = keeper(tmp_path, team, store=store)
    k.on_tick(snap(t_hours=6.49).clock, snap(t_hours=6.49), window())
    assert team.opened == [] and store == {} and rows(tmp_path) == []


def test_the_first_tick_at_6_5_opens_a_zero_fee_board_venue_once_and_saves_the_key(tmp_path):
    team, store, broker, lines = Team(), {}, FakeBroker(bench=[bench_sell("b7-0", 30), bench_buy("b7-1", 40)]), []
    k = keeper(tmp_path, team, store=store, broker=broker, lines=lines)
    first = snap()
    k.on_tick(first.clock, first, window())
    assert team.opened == [("Team 1 market", 0, 0, {"mechanism": "board"})]
    assert store == {("", "v09"): (KEY, 400)} and (tmp_path / "broker.env").exists()
    assert broker.sent == [("b7-0", "b7-1", 35)]  # the broker ran in the same tick
    # next tick the public list shows our venue: never a second opening, the broker goes on
    later = snap(tick=401, t_hours=6.51, cash=250, venue={"venue": "v09", "status": "open"}, venues=(RASTRO, ours()))
    k.on_tick(later.clock, later, window())
    assert len(team.opened) == 1
    opens = [d for d in rows(tmp_path) if d.get("kind") == "venue_open"]
    assert len(opens) == 1 and opens[0]["agent"] == "broker" and opens[0]["status"] == "approved"
    executions = rows(tmp_path, "executions.jsonl")
    assert [e["sdk_method"] for e in executions] == ["open_venue", "broker_match"]
    assert executions[0]["response"]["saved"] == ["postgres", "file"] and "broker_key" not in executions[0]["response"]
    assert KEY not in everything_written(tmp_path / "agents", lines)
    assert any("OPENED v09" in line and "postgres + file" in line for line in lines)


def test_a_venue_we_already_run_is_never_opened_again_and_its_key_comes_from_the_vault(tmp_path):
    team, store, broker = (
        Team(),
        {("", "v09"): (KEY, 300)},
        FakeBroker(bench=[bench_sell("b7-0", 30), bench_buy("b7-1", 40)]),
    )
    k = keeper(tmp_path, team, store=store, broker=broker)
    s = snap(venues=(RASTRO, ours()), venue={"venue": "v09", "status": "open"})
    k.on_tick(s.clock, s, window())
    assert team.opened == [] and broker.sent == [("b7-0", "b7-1", 35)]
    assert [key.get_secret_value() for key in k.made] == [KEY]
    s = snap(venues=(RASTRO, ours(status="closing")))  # closing still holds the bond: still ours
    k.on_tick(s.clock, s, window())
    assert team.opened == []


@pytest.mark.parametrize(
    ("me_venue", "listed", "opens"),
    [
        ({"venue": "s01", "status": "open"}, {"venue": "s01", "owner": "t01", "status": "open", "starter": True}, True),
        ({"venue": "v77", "status": "open"}, None, False),  # unknown to the public list: assume ours
        ({"venue": "v09", "status": "closed"}, None, True),
    ],
)
def test_a_starter_stall_is_not_our_venue_but_an_unknown_one_is(tmp_path, me_venue, listed, opens):
    team = Team()
    k = keeper(tmp_path, team)
    s = snap(venue=me_venue, venues=(RASTRO, listed) if listed else (RASTRO,))
    k.on_tick(s.clock, s, window())
    assert bool(team.opened) is opens


def test_the_bond_and_fee_never_take_cash_below_the_floor_and_a_refusal_sends_nothing(tmp_path):
    team = Team()
    k = keeper(tmp_path, team)
    for tick in range(400, 425):
        s = snap(tick=tick, cash=369)
        k.on_tick(s.clock, s, window())
    assert team.opened == []
    opens = [d for d in rows(tmp_path) if d.get("kind") == "venue_open"]
    assert len(opens) == 2  # said once, then once every REMIND_TICKS
    assert all(d["status"] == "rejected" and "cash_floor 100" in d["guardrail"] for d in opens)
    s = snap(tick=425, cash=370)
    k.on_tick(s.clock, s, window())
    assert len(team.opened) == 1


def test_no_opening_while_postgres_cannot_hold_the_key(tmp_path):
    for store, down in ((False, False), ({}, True)):
        team = Team()
        k = keeper(tmp_path / str(down), team, store=store, db_down=down)
        s = snap()
        k.on_tick(s.clock, s, window())
        assert team.opened == [] and k.retry_tick == 400 + vk.RETRY_TICKS
        assert "Postgres cannot hold the broker key" in rows(tmp_path / str(down))[0]["guardrail"]


def test_venue_exists_stops_for_good_and_a_network_error_waits_before_trying_again(tmp_path):
    team = Team(refuse=BazaarError("venue_exists", "you already run v03", 400))
    k = keeper(tmp_path / "a", team)
    for tick in (400, 401, 450):
        s = snap(tick=tick)
        k.on_tick(s.clock, s, window())
    assert len(team.opened) == 1 and k.final == "venue_exists"
    team = Team(refuse=BazaarError("network", "POST /api/venues: timed out", 0))
    k = keeper(tmp_path / "b", team)
    for tick in (400, 401, 409, 410):
        s = snap(tick=tick)
        k.on_tick(s.clock, s, window())
    assert len(team.opened) == 2  # tick 400, then tick 410 (a timed-out open may have landed: the list says)
    assert [e["error_code"] for e in rows(tmp_path / "b", "executions.jsonl")] == ["network", "network"]


def test_a_dry_run_opens_nothing_and_says_so_every_twenty_ticks(tmp_path):
    team, lines = Team(), []
    k = keeper(tmp_path, team, live=False, lines=lines)
    for tick in range(400, 441):
        s = snap(tick=tick)
        k.on_tick(s.clock, s, window())
    assert team.opened == []
    opens = rows(tmp_path)
    assert len(opens) == 3 and all(d["dry_run"] and d["chosen"] for d in opens)
    assert any("WOULD open our venue 'Team 1 market' (board, 0 bps)" in line for line in lines)


def test_switch_off_or_closed_window_opens_nothing(tmp_path):
    team = Team()
    keeper(tmp_path, team, allow_venue_open=False).on_tick(snap().clock, snap(), window())
    keeper(tmp_path, team).on_tick(snap().clock, snap(), window(open_=False))
    assert team.opened == []


# ---------------------------------------------------------------- the key and the broker afterwards


def test_a_key_that_could_not_be_saved_is_kept_in_memory_for_the_broker(tmp_path, monkeypatch):
    team, broker, lines = Team(), FakeBroker(bench=[bench_sell("b7-0", 30), bench_buy("b7-1", 40)]), []
    monkeypatch.setattr(vn.KeyVault, "save", lambda self, venue, key, tick: ())
    k = keeper(tmp_path, team, broker=broker, lines=lines)
    k.on_tick(snap().clock, snap(), window())
    assert broker.sent and [key.get_secret_value() for key in k.made] == [KEY]
    assert any("NOWHERE" in line for line in lines) and KEY not in "\n".join(lines)


def test_a_venue_without_its_key_says_so_and_matches_nothing(tmp_path):
    team, lines = Team(), []
    k = keeper(tmp_path, team, lines=lines)
    for tick in (400, 401, 420):
        s = snap(tick=tick, venues=(RASTRO, ours()))
        k.on_tick(s.clock, s, window())
    assert team.opened == [] and k.made == []
    assert sum("NO broker key" in line for line in lines) == 2


def test_the_broker_still_matches_the_bench_when_the_maker_could_not_read_our_offers(tmp_path):
    broker = FakeBroker(bench=[bench_sell("b7-0", 30), bench_buy("b7-1", 40)])
    k = keeper(tmp_path, Team(), store={("", "v09"): (KEY, 300)}, broker=broker)
    k.opened = vn.Opened("v09", SecretStr(KEY), ("postgres",))
    k.on_tick(snap().clock, None, window())
    assert broker.sent == [("b7-0", "b7-1", 35)]


def test_nothing_inside_the_keeper_can_break_the_maker_tick(tmp_path):
    class Boom(Team):
        def open_venue(self, *a, **kw):
            raise RuntimeError("unexpected")

    lines = []
    keeper(tmp_path, Boom(), lines=lines).on_tick(snap().clock, snap(), window())
    assert lines == ["tick 400 venue: RuntimeError; skipped this tick"]


def test_the_public_status_shows_that_it_opened_and_matched_never_the_key_or_the_book(tmp_path):
    hub = StatusHub("maker", True)
    broker = FakeBroker(bench=[bench_sell("b7-0", 30), bench_buy("b7-1", 40)])
    k = keeper(tmp_path, Team(), broker=broker, hub=hub)
    k.on_tick(snap().clock, snap(), window())
    state = json.dumps(hub.state()) + "\n".join(hub.replay())
    assert KEY not in state and "b7-0" not in state and '"price": 35' not in state
    kinds = [d["kind"] for d in hub.state()["decisions"]]
    assert kinds == ["venue_open", "broker_match"]
    assert all(d["inputs"] == {} and d["move"] == {} for d in hub.state()["decisions"])


# ---------------------------------------------------------------- inside the maker's tick


class Market:
    def __init__(self):
        self.calls: list[tuple] = []

    def on_tick(self, clock, snap, window):
        self.calls.append((clock.tick, snap is not None, window.open()))


def test_the_maker_runs_our_venue_first_every_tick_even_when_its_own_reads_fail(tmp_path):
    from bazaar_agent.agents.maker import Maker
    from tests.agent_fakes import FakePublic, FakeTeam, clock, parts

    class DownTeam(FakeTeam):
        def me(self):
            raise BazaarError("unavailable", "down", 503)

    for team, read in ((FakeTeam(), True), (DownTeam(), False)):
        market = Market()
        m = Maker(team, FakePublic(), live=False, log=lambda line: None, market=market, **parts(tmp_path))
        m.on_tick(clock())
        assert market.calls == [(100, read, True)]


def test_a_venue_closed_by_hand_stops_the_broker_and_is_never_reopened_by_this_process(tmp_path):
    team, broker = Team(), FakeBroker(bench=[bench_sell("b7-0", 30), bench_buy("b7-1", 40)])
    k = keeper(tmp_path, team, broker=broker)
    k.on_tick(snap().clock, snap(), window())
    assert len(team.opened) == 1 and len(broker.sent) == 1
    lagging = snap(tick=402)  # the lists do not show it yet: still ours
    assert k._venue(lagging.clock, lagging) == "v09"
    gone = snap(tick=410, cash=600)  # closed by hand: not in the lists for longer than the lag
    k.on_tick(gone.clock, gone, window())
    assert k._venue(gone.clock, gone) is None and len(team.opened) == 1


# ---------------------------------------------------------------- once across processes and restarts


def test_a_restarted_maker_never_reopens_after_a_venue_was_opened_on_this_target(tmp_path):
    store = {("", "v09"): (KEY, 300)}  # we opened v09 earlier; it was closed (or suspended) since
    for status in ("closed", "suspended"):
        team, lines = Team(), []
        k = keeper(tmp_path / status, team, store=store, lines=lines)
        for tick in (400, 401, 450):
            s = snap(tick=tick, venue={"venue": "v09", "status": status})
            k.on_tick(s.clock, s, window())
        assert team.opened == [] and k.final == "opened_before"
        assert sum("opened on this target before" in line for line in lines) == 1


def test_two_makers_in_the_same_tick_open_one_venue_between_them(tmp_path):
    store, first, second = {}, Team(), Team()
    a = keeper(tmp_path / "a", first, store=store)
    b = keeper(tmp_path / "b", second, store=store)
    store[("", "_claim")] = ("", 400)  # a's claim is in flight when b reads (a deploy overlap)
    s = snap()
    b.on_tick(s.clock, s, window())
    assert second.opened == [] and "another process holds the opening claim" in rows(tmp_path / "b")[0]["guardrail"]
    del store[("", "_claim")]
    a.on_tick(s.clock, s, window())
    later = snap(tick=410)
    b.on_tick(later.clock, later, window())  # retries after RETRY_TICKS: a venue was opened since
    assert len(first.opened) == 1 and second.opened == [] and b.final == "opened_before"


def test_a_stale_claim_is_taken_over_and_a_refusal_gives_the_claim_back(tmp_path):
    store = {("", "_claim"): ("", 300)}  # a process that died mid-opening 100 ticks ago
    team = Team(refuse=BazaarError("locked", "level 2 needed", 403))
    k = keeper(tmp_path, team, store=store)
    k.on_tick(snap().clock, snap(), window())
    assert len(team.opened) == 1 and store == {}  # took the stale claim, was refused, gave it back


def test_the_bond_is_judged_on_cash_our_open_bids_do_not_already_promise(tmp_path):
    from tests.agent_fakes import bid

    team = Team()
    k = keeper(tmp_path, team)
    s = snap(cash=380, offers=[bid(50, "LAV-09", 200, venue="rastro", maker="t01")])
    k.on_tick(s.clock, s, window())
    assert team.opened == []
    assert "cash 180 - venue bond and fee 270 < cash_floor 100" in rows(tmp_path)[0]["guardrail"]


def test_the_broker_key_is_cut_out_of_spans_and_rows_by_value_whatever_its_shape(tmp_path):
    from bazaar_agent import telemetry as tm

    odd = "Zq" + "9" * 20  # no known prefix: only the value can catch it
    vault = vn.KeyVault(tmp_path, lambda: FakeConn({}))
    vault.save("v09", odd, 400)
    try:
        assert odd not in tm.scrub(f"book read with {odd}")
    finally:
        tm._RT.secrets = tuple(s for s in tm._RT.secrets if s != odd)


def test_an_unsent_opening_stays_off_the_public_status(tmp_path):
    hub = StatusHub("maker", True)
    k = keeper(tmp_path, Team(), hub=hub)
    k.on_tick(snap(cash=300).clock, snap(cash=300), window())  # refused by the floor: nothing sent
    assert hub.state()["decisions"] == [] and rows(tmp_path)[0]["status"] == "rejected"


@pytest.mark.parametrize(("error", "kept"), [("http_502", True), ("bad_response", True), ("locked", False)])
def test_a_claim_is_kept_when_the_opening_may_have_landed(tmp_path, error, kept):
    status = {"http_502": 502, "bad_response": 0, "locked": 403}[error]
    store = {}
    k = keeper(tmp_path, Team(refuse=BazaarError(error, "", status)), store=store)
    k.on_tick(snap().clock, snap(), window())
    assert (("", "_claim") in store) is kept and k.held_claim is kept


def test_a_venue_we_run_without_its_key_counts_as_opened_and_is_never_followed_by_another(tmp_path):
    store, lines = {}, []
    k = keeper(tmp_path, Team(), store=store, lines=lines)
    early = snap(t_hours=6.0, venue={"venue": "s07", "status": "open"})  # /me alone names it: maybe the stall
    k.on_tick(early.clock, early, window())
    assert store == {}  # never marked: a wrong mark would stop every future opening
    s = snap(venues=(RASTRO, ours()))  # an opening whose answer was lost: the venue is there, no key
    k.on_tick(s.clock, s, window())
    assert store == {("", "v09"): ("", 400)} and k.made == []
    team = Team()
    fresh = keeper(tmp_path / "restart", team, store=store)  # it closed later; a restarted maker
    gone = snap(tick=500, venue={"venue": "v09", "status": "closed"})
    fresh.on_tick(gone.clock, gone, window())
    assert team.opened == [] and fresh.final == "opened_before"


def test_a_process_that_claims_after_another_saved_its_venue_backs_off(tmp_path, monkeypatch):
    store, team = {}, Team()
    k = keeper(tmp_path, team, store=store)
    answers = iter([False, True])  # before the claim: nothing opened; after it: another process just saved
    monkeypatch.setattr(vn.KeyVault, "opened_before", lambda self: next(answers))
    k.on_tick(snap().clock, snap(), window())
    assert team.opened == [] and k.final == "opened_before" and store == {}


def test_a_refused_opening_never_shows_its_error_code_on_the_public_status(tmp_path):
    hub = StatusHub("maker", True)
    k = keeper(tmp_path, Team(refuse=BazaarError("insufficient_cash", "", 400)), hub=hub)
    k.on_tick(snap().clock, snap(), window())
    assert "insufficient_cash" not in "\n".join(hub.replay())


def test_we_run_a_venue_but_me_still_shows_the_stall_key_says_so(tmp_path):
    lines = []
    k = keeper(tmp_path, Team(), store={("", "v09"): (KEY, 300)}, lines=lines)
    s = snap(venues=(RASTRO, ours()), venue={"venue": "v09", "status": "open"})
    s = Snapshot(s.clock, {**s.me, "starter_broker_key": "bk_" + "Stale0ne"}, s.offers, {}, [], s.venues, [])
    k.on_tick(s.clock, s, window())
    assert any("set venue_bond_reserve = 0" in line for line in lines)


def test_no_answer_on_the_second_check_gives_the_claim_back_and_tries_again(tmp_path, monkeypatch):
    store, team = {}, Team()
    k = keeper(tmp_path, team, store=store)
    answers = iter([False, None, False, False])  # Postgres blinks right after our claim, then answers
    monkeypatch.setattr(vn.KeyVault, "opened_before", lambda self: next(answers))
    k.on_tick(snap().clock, snap(), window())
    assert team.opened == [] and k.final is None and ("", "_claim") not in store
    later = snap(tick=410)
    k.on_tick(later.clock, later, window())
    assert len(team.opened) == 1


def test_a_refused_opening_never_reaches_the_public_events_at_all(tmp_path):
    hub = StatusHub("maker", True)
    k = keeper(tmp_path, Team(refuse=BazaarError("locked", "", 403)), hub=hub)
    k.on_tick(snap().clock, snap(), window())
    assert not any("open_venue" in event for event in hub.replay())


def test_a_408_may_have_opened_the_venue_so_the_claim_is_kept(tmp_path):
    store = {}
    k = keeper(tmp_path, Team(refuse=BazaarError("http_408", "", 408)), store=store)
    k.on_tick(snap().clock, snap(), window())
    assert ("", "_claim") in store and k.held_claim


def test_venue_mechanism_auto_opens_an_auto_venue_and_runs_no_broker(tmp_path):
    team, broker, lines = Team(), FakeBroker(bench=[bench_sell("b7-0", 30), bench_buy("b7-1", 40)]), []
    k = keeper(tmp_path, team, broker=broker, lines=lines, venue_mechanism="auto")
    first = snap()
    k.on_tick(first.clock, first, window())
    assert team.opened == [("Team 1 market", 0, 0, {"mechanism": "auto"})]
    assert broker.sent == [] and k.made == []
    assert any("is auto (venue_mechanism)" in line for line in lines)
