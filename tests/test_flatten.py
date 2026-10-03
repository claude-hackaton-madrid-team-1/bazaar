"""`bazaar flatten`: the operator's explicit cancel-everything (issue #3), the one write that goes out while the
kill switch holds. Offers by default, threads only with --threads, one paced pass that stops on a 429."""

import pytest
from typer.testing import CliRunner

from bazaar_agent.agents.flatten import Item, flatten, offers_to_cancel, threads_to_close
from bazaar_agent.agents.runtime import Recorder
from bazaar_agent.decisions import DecisionLog
from bazaar_agent.guardrails import Ledger
from bazaar_agent.sdk import BazaarError
from tests.agent_fakes import TICK, FakeTeam, ask, bid, clock, our_ask, rows
from tests.test_kill_switch import Switch
from tests.test_maker import NoAccept, maker

US = "t01"
OFFERS = [
    our_ask(1, 5, "LAT-09", 68),
    bid(2, "LAV-02", 9, venue="v02"),
    bid(3, "LAV-08", 20, thread=85),  # our bid inside a dealer thread: it goes with the thread
    ask(4, "LAV-03", 7, maker="t05", to=US),  # another team's offer addressed to us: not ours
    {**bid(6, "LAV-04", 5), "status": "filled"},
]
THREADS = [{"id": 85, "with": "abuela", "status": "open"}, {"id": 86, "with": "t07", "status": "closed"}]


class Team(FakeTeam):
    def __init__(self, refuse=None, **kw):
        super().__init__(me={"id": US, "cash": 400, "assets": []}, offers=OFFERS, threads=THREADS, **kw)
        self.refuse = refuse or {}  # offer id -> BazaarError

    def cancel(self, offer_id):
        if offer_id in self.refuse:
            raise self.refuse[offer_id]
        return super().cancel(offer_id)


def test_only_our_open_offers_outside_threads_are_cancelled_and_threads_are_listed_apart():
    items = offers_to_cancel({"offers": OFFERS}, US)
    assert [(i.kind, i.id) for i in items] == [("cancel", 1), ("cancel", 2)]
    assert items[1].refund == ("LAV-02", 9, 90) and items[0].refund is None  # card, cash, created tick
    assert [(i.kind, i.id) for i in threads_to_close({"threads": THREADS})] == [("close_thread", 85)]


def _run(tmp_path, team, items, *, live=True, stops=(), sleeps=None):
    decisions = DecisionLog(tmp_path)
    decisions.begin_tick(TICK)
    rec = Recorder("flatten", decisions, live, lambda line: None)
    ledger = Ledger(tmp_path / "ledger.jsonl")
    ledger.record("spend", TICK - 5, 1.4, 9, "LAV-02")  # the bid's cash, counted when it was posted
    out = flatten(
        team,
        items,
        rec=rec,
        tick=TICK,
        t_hours=1.5,
        ledger=ledger,
        live=live,
        kill_switch=stops,
        sleep=(sleeps.append if sleeps is not None else lambda s: None),
    )
    return out, ledger


def test_live_cancels_every_open_offer_paced_and_refunds_a_bids_spend(tmp_path):
    team, sleeps = Team(), []
    out, ledger = _run(tmp_path, team, offers_to_cancel({"offers": OFFERS}, US), sleeps=sleeps)
    assert team.sent == [("cancel", 1), ("cancel", 2)] and [i.id for i in out.done] == [1, 2]
    assert ledger.spent_since(0) == 0 and sleeps == [0.3]  # paced under the key's 5 req/s
    assert {r["sdk_method"] for r in rows(tmp_path, "executions.jsonl")} == {"cancel"}


def test_a_flattened_bid_is_refunded_in_the_hour_it_was_spent(tmp_path):
    # Bid 2 was posted at tick 90 (10 ticks of 60 s before h1.5): its refund is booked there, not now, one
    # more slowest tick back so it is never dated after its spend (`refund_row`).
    _, ledger = _run(tmp_path, Team(), offers_to_cancel({"offers": OFFERS}, US))
    (refund,) = [e for e in ledger.entries() if e["price"] < 0]
    assert (refund["tick"], round(refund["t_hours"], 4), refund["price"]) == (90, round(1.5 - 11 / 60, 4), -9)


def test_a_rate_limit_stops_the_pass_and_reports_what_is_left(tmp_path):
    items = [Item("cancel", 1, "a"), Item("cancel", 2, "b"), Item("cancel", 7, "gone"), Item("cancel", 9, "c")]
    team = Team(
        refuse={
            7: BazaarError("not_found", "already gone", 404),
            9: BazaarError("rate_limited", "slow down", 429),
        }
    )
    out, _ = _run(tmp_path, team, items + [Item("cancel", 10, "d")])
    assert team.sent == [("cancel", 1), ("cancel", 2)]  # 10 was never tried: no retry loop on a 429
    assert out.failed == [(items[2], "not_found")] and [i.id for i in out.left] == [9, 10]
    assert out.stopped == "rate_limited"


# ---------------------------------------------------------------- the command


@pytest.fixture
def flatten_cli(monkeypatch, tmp_path):
    import psycopg

    from bazaar_agent import cli, db
    from bazaar_agent.config import Settings

    def down(*args, **kwargs):
        raise psycopg.OperationalError("no database in unit tests")

    team = Team(now=clock())
    monkeypatch.setattr(cli, "load_settings", lambda: Settings(data_dir=tmp_path))
    monkeypatch.setattr(cli, "team_client", lambda settings: team)
    monkeypatch.setattr(db, "connect", down)
    monkeypatch.setattr(db, "connect_ready", down)
    monkeypatch.setattr("bazaar_agent.agents.flatten.PACE_S", 0.0)
    return team, cli


def test_a_dry_run_sends_nothing_and_records_the_would_cancels(flatten_cli, tmp_path):
    team, cli = flatten_cli
    result = CliRunner().invoke(cli.app, ["flatten", "--threads"])
    assert result.exit_code == 0, result.output
    assert team.sent == [] and "dry run 2 offer(s) to cancel, 1 thread(s) to close" in result.output
    assert [(r["kind"], r["dry_run"]) for r in rows(tmp_path)] == [
        ("flatten_cancel", True),
        ("flatten_cancel", True),
        ("flatten_close_thread", True),
    ]


def test_threads_are_closed_only_with_the_flag(flatten_cli):
    team, cli = flatten_cli
    assert CliRunner().invoke(cli.app, ["flatten", "--live"]).exit_code == 0
    assert team.sent == [("cancel", 1), ("cancel", 2)]
    assert CliRunner().invoke(cli.app, ["flatten", "--live", "--threads"]).exit_code == 0
    assert team.sent[2:] == [("cancel", 1), ("cancel", 2), ("close_thread", 85)]


def test_flatten_goes_out_under_the_kill_switch_while_the_maker_holds(flatten_cli, tmp_path, monkeypatch):
    team, cli = flatten_cli
    (tmp_path / "switch").mkdir()
    switch = Switch(tmp_path / "switch", monkeypatch)
    switch.trading(False)
    holding = NoAccept(offers=[our_ask(1, 5, "LAT-09", 90), bid(2, "LAV-02", 9)])
    m, _ = maker(tmp_path / "maker", holding, live=True)
    m.on_tick(clock())
    assert holding.sent == []  # the agent loop sends nothing
    result = CliRunner().invoke(cli.app, ["flatten", "--live"])
    assert result.exit_code == 0, result.output
    assert team.sent == [("cancel", 1), ("cancel", 2)] and "kill switch on" in result.output
    assert {r["guardrail"] for r in rows(tmp_path) if r.get("kind") == "flatten_cancel"} == {
        "kill switch on (trading_enabled = false): operator flatten goes out"
    }


def test_a_partial_flatten_never_exits_as_a_success(flatten_cli):
    team, cli = flatten_cli
    team.refuse = {1: BazaarError("asset_locked", "settling", 400)}
    result = CliRunner().invoke(cli.app, ["flatten", "--live"])
    assert result.exit_code == 1 and team.sent == [("cancel", 2)]  # the rest of the pass still went out
    assert "refused cancel 1" in result.output and "1 refused; check" in result.output


def test_the_command_reports_what_a_rate_limit_left(flatten_cli):
    team, cli = flatten_cli
    team.refuse = {2: BazaarError("rate_limited", "slow down", 429)}
    result = CliRunner().invoke(cli.app, ["flatten", "--live"])
    assert result.exit_code == 1 and team.sent == [("cancel", 1)]
    assert "stopped by rate_limited: 1 left; run `bazaar flatten --live` again next tick" in result.output
