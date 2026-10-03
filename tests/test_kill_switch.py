"""The kill switch HOLDS: while it is on nothing is sent (no bid, accept, post, cancel, close or walk), reads go
on, open offers and threads stay as they are, and agents resume where they were when it goes off. It is read
live: a GUARDRAILS.md edit takes effect on the next tick without a restart."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from bazaar_agent import guardrails as gr
from bazaar_agent.agents.dealer import BidPlan, negotiate
from bazaar_agent.agents.taker import TakerConfig
from tests.agent_fakes import TICK, FakePublic, FakeTeam, bid, clock, our_ask
from tests.test_dealer import FakeDealerClient
from tests.test_maker import NoAccept, maker
from tests.test_taker import at, taker


class Switch:
    """A GUARDRAILS.md copy the code under test reads (`gr.GUARDRAILS_FILE`) and a pause file of our own."""

    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.file = tmp_path / "GUARDRAILS.md"
        self.pause = tmp_path / "PAUSE"
        self.text = gr.GUARDRAILS_FILE.read_text(encoding="utf-8")
        self.trading(True)
        monkeypatch.setattr(gr, "GUARDRAILS_FILE", self.file)

    def trading(self, enabled: bool) -> None:
        line = f"- `trading_enabled` = {'true' if enabled else 'false'}"
        text = self.text.replace("- `trading_enabled` = true", line).replace("- `trading_enabled` = false", line)
        self.file.write_text(text, encoding="utf-8")

    def paused(self, on: bool) -> None:
        if on:
            self.pause.touch()
        else:
            self.pause.unlink(missing_ok=True)


@pytest.fixture
def switch(tmp_path, monkeypatch):
    home = tmp_path / "switch"
    home.mkdir()
    return Switch(home, monkeypatch)


def writes(team, *kinds):
    return [s for s in team.sent if s[0] in kinds]


# ---------------------------------------------------------------- the shared helper and check()


def test_kill_switch_reads_trading_enabled_live_and_the_pause_file(switch):
    rules = gr.Guardrails(pause_file=str(switch.pause))
    assert gr.kill_switch(rules) == ()
    switch.trading(False)
    assert gr.kill_switch(rules) == ("trading_enabled = false",)
    switch.paused(True)
    assert gr.kill_switch(rules) == ("trading_enabled = false", f"pause file {switch.pause} exists")
    switch.trading(True)
    switch.paused(False)
    assert gr.kill_switch(rules) == ()  # false → true resumes without a restart
    switch.file.write_text("- `cash_floor` = lots — a typo", encoding="utf-8")
    assert "invalid" in gr.kill_switch(rules)[0]  # an unreadable rule book holds every write (fail closed)
    switch.file.unlink()
    assert "missing" in gr.kill_switch(rules)[0]


def test_cancel_and_close_thread_are_checked_and_only_the_kill_switch_refuses_them(switch):
    rules = gr.Guardrails(pause_file=str(switch.pause))
    assert gr.action_kind("cancel") == "cancel" and gr.action_kind("close_thread") == "close_thread"
    poor = gr.Context(cash=0, held={}, tick=1, t_hours=0.1, spent_last_hour=999, accepts_this_tick=9)
    for kind in ("cancel", "close_thread"):
        assert gr.check(gr.Action(kind, "7"), poor, rules).allowed  # no cash, cap or quota rule applies
    switch.paused(True)
    me = {"cash": 400, "assets": []}
    live = gr.context_from(me, 1, 0.1, gr.Ledger(switch.file.parent / "l.jsonl"), rules)
    for kind in ("cancel", "close_thread", "bid", "accept_buy"):
        verdict = gr.check(gr.Action(kind, "LAV-03", "common", 5), live, rules)
        assert not verdict.allowed and verdict.halted and "pause file" in str(verdict)


def test_halted_is_set_only_by_the_kill_switch():
    ctx = gr.Context(cash=280, held={}, tick=1, t_hours=0.1)
    floor = gr.check(gr.Action("bid", "LAV-03", "common", 12), ctx, gr.Guardrails())
    assert not floor.allowed and not floor.halted  # the cash floor: the caller may still walk
    off = gr.Guardrails(trading_enabled=False)
    assert gr.check(gr.Action("bid", "LAV-03", "common", 5), ctx, off).halted  # no live read: loaded rules


def test_a_guardrails_edit_counts_on_the_next_context_without_a_restart(switch):
    started_off = gr.Guardrails(trading_enabled=False, pause_file=str(switch.pause))  # loaded while it was off
    me = {"cash": 400, "assets": []}
    ctx = gr.context_from(me, 1, 0.1, gr.Ledger(switch.file.parent / "l.jsonl"), started_off)
    assert gr.check(gr.Action("bid", "LAV-03", "common", 5), ctx, started_off).allowed  # the file says true now


# ---------------------------------------------------------------- the maker


def test_the_maker_under_the_kill_switch_sends_no_cancel_and_no_post_then_resumes(tmp_path, switch):
    # Offer 2 is no longer a target (a cancel), offer 1 moved 90 → 68 (a reprice: cancel + post).
    team = NoAccept(offers=[our_ask(1, 5, "LAT-09", 90), bid(2, "LAV-02", 9)])
    m, lines = maker(tmp_path, team, live=True, pause_file=str(switch.pause))
    switch.paused(True)
    m.on_tick(clock())
    assert team.sent == []
    assert any(f"tick {TICK} maker: kill switch on: holding (no posts, no cancels" in line for line in lines)
    switch.paused(False)
    m.on_tick(clock(tick=TICK + 1))
    assert writes(team, "cancel") == [("cancel", 2), ("cancel", 1)] and writes(team, "list_offer")


def test_flipping_trading_enabled_in_guardrails_md_holds_the_maker_on_the_next_tick(tmp_path, switch):
    team = NoAccept(offers=[our_ask(1, 5, "LAT-09", 90), bid(2, "LAV-02", 9)])
    m, lines = maker(tmp_path, team, live=True, pause_file=str(switch.pause))
    switch.trading(False)  # the maker was started with trading on; nobody restarts it
    m.on_tick(clock())
    assert team.sent == [] and any("kill switch on" in line and "trading_enabled = false" in line for line in lines)
    switch.trading(True)
    m.on_tick(clock(tick=TICK + 1))
    assert writes(team, "cancel") and writes(team, "list_offer")


def test_a_kill_switch_that_goes_on_mid_tick_stops_the_reprice_before_its_cancel(tmp_path, switch, monkeypatch):
    from bazaar_agent.agents import maker as maker_module

    calls = iter([()])  # off when the tick starts, on from the first write on
    monkeypatch.setattr(maker_module, "kill_switch", lambda rules: next(calls, ("trading_enabled = false",)))
    team = NoAccept(offers=[our_ask(1, 5, "LAT-09", 90)])
    m, _ = maker(tmp_path, team, live=True, pause_file=str(switch.pause))
    m.on_tick(clock())
    assert team.sent == []  # the listing is not lost: no cancel without its repost


# ---------------------------------------------------------------- the taker's dealer desk


def _desk(tmp_path, switch, **rules):
    team = FakeTeam()
    t, lines, _ = taker(
        tmp_path,
        team,
        FakePublic(),
        live=True,
        config=TakerConfig(max_dealer_threads=3),
        pause_file=str(switch.pause),
        **rules,
    )
    t.on_tick(clock())  # opens thread 5000 with abuela and bids 18
    assert writes(team, "say") == [("say", 5000, 18)]
    return team, t, lines


def test_the_taker_under_the_kill_switch_holds_its_dealer_threads_then_resumes(tmp_path, switch):
    team, t, lines = _desk(tmp_path, switch, dealer_max_ticks_per_thread=3)
    switch.paused(True)
    sent = len(team.sent)
    for n in range(1, 5):  # a pause longer than the thread's tick limit
        t.on_tick(at(team, TICK + n))
    assert team.sent[sent:] == [] and set(t.convs) == {"abuela"} and t.convs["abuela"].ticks == 1
    assert any("taker: kill switch on: holding" in line for line in lines)
    switch.paused(False)
    t.on_tick(at(team, TICK + 5))
    step = t.convs["abuela"].neg.plan.step
    assert team.sent[sent:] == [("say", 5000, 18 + step)]  # resumes the ladder where it was, no walk


def test_a_final_above_our_max_under_the_kill_switch_is_not_walked(tmp_path, switch):
    team, t, _ = _desk(tmp_path, switch)
    final = {"id": 801, "maker": "abuela", "status": "open", "final": True}
    final |= {"give": {"types": ["card:LAV-08"]}, "want": {"cash": 90}}
    team.thread_payloads[5000] = {"id": 5000, "status": "open", "messages": [], "standing_offers": [final]}
    switch.paused(True)
    t.on_tick(at(team, TICK + 1))
    assert writes(team, "close_thread") == [] and set(t.convs) == {"abuela"}
    switch.paused(False)
    t.on_tick(at(team, TICK + 2))
    assert writes(team, "close_thread") == [("close_thread", 5000)]  # off again: the walk goes out


def test_a_kill_switch_denial_of_a_desk_bid_holds_instead_of_walking(tmp_path, switch, monkeypatch):
    from bazaar_agent.agents import taker as taker_module

    team, t, _ = _desk(tmp_path, switch)
    monkeypatch.setattr(taker_module, "kill_switch", lambda rules: ())  # off when the tick starts
    monkeypatch.setattr(gr, "kill_switch", lambda rules, path=None: ("trading_enabled = false",))  # on at the bid
    t.on_tick(at(team, TICK + 1))
    assert writes(team, "say", "close_thread") == [("say", 5000, 18)] and set(t.convs) == {"abuela"}


def test_a_deal_that_settles_during_the_hold_is_still_booked(tmp_path, switch):
    team, t, _ = _desk(tmp_path, switch)
    t.convs["abuela"].accepted_price = 20
    switch.paused(True)
    team.thread_payloads[5000] = {"id": 5000, "status": "deal", "messages": [], "standing_offers": []}
    t.on_tick(at(team, TICK + 1))
    assert t.convs == {} and t.ledger.spent_since(0) == 20  # reads go on: the settled deal is booked


# ---------------------------------------------------------------- dealer buy (negotiate)


def test_negotiate_holds_a_kill_switch_denial_and_resumes_where_it_was():
    client = FakeDealerClient(asks=[12, 10, 9])
    state = {"until": 0}

    def kill_switch():
        tick = 100 + client.reads // client.reads_per_tick
        return ("pause file .local/PAUSE exists",) if tick < state["until"] else ()

    def guard(move, _tid):
        if move.price == 7 and not state["until"]:  # the pause lands while the second bid is being decided
            state["until"] = 100 + client.reads // client.reads_per_tick + 4
            return "pause file .local/PAUSE exists"
        return None

    out = negotiate(
        client,
        "abuela",
        {"buy": {"card": "LAV-03"}},
        BidPlan(6, 1, 10),
        log=lambda _: None,
        sleep=lambda _: None,
        guard=guard,
        kill_switch=kill_switch,
        max_ticks=6,
    )
    assert not client.closed and client.sent == [6, 7, 8]
    assert (out.status, out.price) == ("deal", 9)  # the held ticks did not count toward max_ticks


def test_negotiate_never_closes_a_thread_on_timeout_while_the_kill_switch_is_on():
    client = FakeDealerClient(asks=[30] * 20)
    out = negotiate(
        client,
        "abuela",
        {"buy": {"card": "LAV-03"}},
        BidPlan(6, 1, 10),
        log=lambda _: None,
        sleep=lambda _: None,
        kill_switch=lambda: ("trading_enabled = false",) if len(client.sent) >= 2 else (),
        max_ticks=2,
    )
    assert (out.status, client.sent, client.closed) == ("held", [6, 7], False)


# ---------------------------------------------------------------- bazaar sell cancel


def test_sell_cancel_is_refused_while_the_kill_switch_is_on(switch, monkeypatch):
    from bazaar_agent import cli

    team = FakeTeam()
    monkeypatch.setattr(cli, "_team_client", lambda: team)
    switch.trading(False)
    result = CliRunner().invoke(cli.app, ["sell", "cancel", "7", "--live"])
    assert result.exit_code == 1 and "kill switch on" in result.output and team.sent == []
    switch.trading(True)
    result = CliRunner().invoke(cli.app, ["sell", "cancel", "7", "--live"])
    assert result.exit_code == 0, result.output
    assert team.sent == [("cancel", 7)]


def test_the_switch_going_on_while_the_desk_writes_its_words_holds_the_bid(tmp_path, switch):
    # The words may come from an LLM and take seconds: the switch is read again just before the send.
    team, t, lines = _desk(tmp_path, switch)

    def words_then_pause(request):
        switch.paused(True)
        return "Subo un poquito, ¿le parece?"

    t.words_fn = words_then_pause
    sent = len(team.sent)
    t.on_tick(at(team, TICK + 1))
    assert team.sent[sent:] == [] and set(t.convs) == {"abuela"}  # no bid, no walk: the thread waits
    assert any("kill switch on: holding bid on thread 5000" in line for line in lines)


def test_negotiate_reads_the_switch_again_after_writing_the_words():
    # The bid's words may come from an LLM: a pause that lands while they are written holds that bid.
    client = FakeDealerClient(asks=[12, 10, 9])
    state: dict[str, int | None] = {"until": None}
    lines: list[str] = []

    def tick() -> int:
        return 100 + client.reads // client.reads_per_tick

    def kill_switch():
        until = state["until"]
        return ("pause file .local/PAUSE exists",) if until is not None and tick() < until else ()

    def words(request):
        if request.price == 7 and state["until"] is None:
            state["until"] = tick() + 2  # the pause lands while the second bid's words are written
        return f"¿{request.price}, señora?"

    out = negotiate(
        client,
        "abuela",
        {"buy": {"card": "LAV-03"}},
        BidPlan(6, 1, 10),
        log=lines.append,
        sleep=lambda _: None,
        kill_switch=kill_switch,
        words_fn=words,
        max_ticks=6,
    )
    assert any("before sending bid: kill switch on: holding" in line for line in lines)
    assert client.sent == [6, 7, 8] and not client.closed and (out.status, out.price) == ("deal", 9)


@pytest.mark.parametrize(
    "text",
    [
        "",  # an editor truncated the file mid-save
        "# Guardrails\n- `cash_floor` = 270 — the floor.\n",  # the trading_enabled line was deleted
    ],
)
def test_a_guardrails_file_without_trading_enabled_holds(switch, text):
    switch.file.write_text(text, encoding="utf-8")
    (stop,) = gr.kill_switch(gr.Guardrails(pause_file=str(switch.pause)))
    assert "holding" in stop


def test_a_guardrails_file_with_a_stray_byte_holds_instead_of_raising(switch):
    switch.file.write_bytes(switch.file.read_bytes() + b"\xff\xfe")
    (stop,) = gr.kill_switch(gr.Guardrails(pause_file=str(switch.pause)))
    assert stop == "GUARDRAILS.md is invalid (UnicodeDecodeError): holding"


def test_negotiate_opens_no_thread_while_the_switch_is_on():
    client = FakeDealerClient(asks=[12])
    opened: list[str] = []
    client.open_thread = lambda dealer, topic: opened.append(dealer) or {"id": 85}
    out = negotiate(
        client,
        "abuela",
        {"buy": {"card": "LAV-03"}},
        BidPlan(6, 1, 10),
        log=lambda _: None,
        sleep=lambda _: None,
        kill_switch=lambda: ("pause file .local/PAUSE exists",),
    )
    assert opened == [] and (out.thread, out.status) == (None, "held")


def test_the_taker_reads_the_switch_again_before_an_accept(tmp_path, switch):
    # Jev and the duel grace may take seconds: a pause that lands meanwhile holds the accept.
    from tests.agent_fakes import ask
    from tests.test_taker import JevAdvice

    def jev_then_pause(state):
        switch.paused(True)
        return JevAdvice("yes", 0.9)

    team = FakeTeam()
    t, lines, _ = taker(
        tmp_path,
        team,
        FakePublic(boards={"rastro": [ask(1, "LAV-02", 10)]}),
        live=True,
        jev=jev_then_pause,
        pause_file=str(switch.pause),
    )
    t.on_tick(clock())
    assert writes(team, "accept") == [] and any("kill switch on: holding" in line for line in lines)
