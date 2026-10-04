"""Sales owns team conversations without duplicating Taker's dealer/board loop."""

from bazaar_agent.agents.sales import Sales
from bazaar_agent.agents.taker import Taker
from tests.agent_fakes import FakePublic, FakeTeam, ask, bid, clock, parts, rows
from tests.test_team_desk import Team, thread


def sales(tmp_path, team=None, **rules):
    kw = parts(tmp_path, team_threads_enabled=True, team_threads_max_open=0, **rules)
    agent = Sales(
        team or FakeTeam(), FakePublic(), live=True, log=lambda _: None, now=lambda: 1000.0, sleep=lambda _: None, **kw
    )
    return agent, kw


def test_sales_accepts_private_cash_sale_with_existing_executor(tmp_path):
    payload = thread(offers=[bid(902, "LAT-03", 10, maker="t05")], opened_by="t05")
    team = Team(threads=[payload], thread_payloads={42: payload})
    agent, _ = sales(tmp_path, team)
    agent.on_tick(clock())
    assert ("accept", 902, [4]) in team.sent
    assert any(r.get("agent") == "sales" for r in rows(tmp_path))


def test_sales_never_calls_dealer_or_board_loop(tmp_path, monkeypatch):
    agent, _ = sales(tmp_path)

    def forbidden(*args, **kwargs):
        raise AssertionError("SALES must not run dealer, board, pack, or crafting work")

    for name in ("_open", "_desk_moves", "_converse", "_board_offers", "_workshop", "_open_pack"):
        monkeypatch.setattr(agent, name, forbidden)
    monkeypatch.setattr(agent.public, "dealers", forbidden)
    monkeypatch.setattr(agent.public, "board", forbidden)
    agent.on_tick(clock())
    assert not agent.team.sent


def test_two_processes_cannot_own_team_desk_same_tick(tmp_path):
    first, kw = sales(tmp_path)
    second = Taker(FakeTeam(), FakePublic(), live=True, log=lambda _: None, **kw)
    assert first._claim_team_desk(clock())
    assert not second._claim_team_desk(clock())
    assert first._claim_team_desk(clock())
    assert second._claim_team_desk(clock(tick=101))


def test_disabled_taker_does_not_read_or_close_sales_threads(tmp_path, monkeypatch):
    payload = thread(offers=[ask(901, "LAV-02", 10, maker="t05", to="t01")], opened_by="t05")
    team = Team(threads=[payload], thread_payloads={42: payload})
    kw = parts(tmp_path, team_threads_enabled=True)
    from bazaar_agent.agents.taker import TakerConfig

    agent = Taker(team, FakePublic(), live=True, log=lambda _: None, config=TakerConfig(max_dealer_threads=0), **kw)
    monkeypatch.setenv("BAZAAR_TEAM_THREADS", "0")
    agent.on_tick(clock())
    assert "thread 42" not in team.reads
    assert not any(s[0] in {"say", "walk", "accept"} for s in team.sent)


def test_sales_dry_run_does_not_claim_live_writer(tmp_path):
    first, kw = sales(tmp_path)
    first.live = False
    assert first._claim_team_desk(clock())
    second = Taker(FakeTeam(), FakePublic(), live=True, log=lambda _: None, **kw)
    assert second._claim_team_desk(clock())


def test_swap_words_cannot_send_after_pause(tmp_path):
    from tests.test_team_desk import desk, says, view

    team = Team()
    pause = tmp_path / "PAUSE"
    actor, _ = desk(tmp_path, team, pause_file=str(pause))

    def words(_):
        pause.touch()
        return "Hablemos de un intercambio."

    actor.words = words
    actor.converse(view(), set())
    assert pause.exists()
    assert not says(team)


def test_swap_expiring_after_reservation_releases_unsent_cash_and_copy(tmp_path, monkeypatch):
    from dataclasses import replace

    from bazaar_agent.agents import publication
    from bazaar_agent.agents.seller import open_commitments
    from bazaar_agent.guardrails import Ledger
    from tests.test_team_desk import desk, says, view

    team = Team()
    actor, _ = desk(tmp_path, team)
    actor.ledger = Ledger(tmp_path / "ledger.jsonl")
    active = [True]
    reserve = publication.reserve

    def expires(*args, **kwargs):
        token = reserve(*args, **kwargs)
        active[0] = False
        return token

    monkeypatch.setattr(publication, "reserve", expires)
    from bazaar_agent.official_values import OfficialValues

    v = replace(view(), window_open=lambda: active[0])
    ctx = replace(v.ctx(None), values=OfficialValues(lambda ref: {"card": ref, "your_value": 20}))
    v = replace(v, ctx=lambda _: ctx)
    actor.converse(v, set())
    assert not active[0]  # actually reached the pre-send reservation
    assert not says(team)
    assert actor.ledger.spent_since(0) == 0
    pending = publication.with_pending(actor.ledger, team._me, [], v.us, v.tick, v.t_hours)
    assert not open_commitments(pending, v.us).listed


def test_representative_sales_outreach_budget_is_measured_and_not_fleet_ceiling(tmp_path):
    from bazaar_agent import rate_budget as rb
    from tests.test_sales_outreach import setup

    actor, team, _, v, matrix = setup(tmp_path)
    tally = rb.CallTally()
    actor.team = tally.wrap(team, "team")
    actor.on_tick(v, matrix)
    assert tally.total("team") == 5  # fresh me/offers/threads, open, say
    assert rb.sales_outreach().team == 4 + tally.total("team")  # clock and snapshot
    assert rb.budget_table(15, [*rb.steady_plan(), rb.sales_outreach()]).rps("team") == 2.8
    assert rb.budget_table(15, [*rb.saturday_plan(tick_seconds=15), rb.sales_outreach()]).rps("team") > 5


def test_cli_uses_explicit_otel_service_name(monkeypatch):
    from typer.testing import CliRunner

    from bazaar_agent import cli

    names = []
    monkeypatch.setenv("OTEL_SERVICE_NAME", "bazaar-sales")
    monkeypatch.setattr(cli.tm, "init_tracing", lambda name: names.append(name) or False)
    result = CliRunner().invoke(cli.app, ["rules"])
    assert result.exit_code == 0, result.output
    assert names == ["bazaar-sales"]


def test_only_acknowledged_team_words_are_buffered(tmp_path):
    from bazaar_agent.sdk import BazaarError
    from tests.test_team_desk import desk, view

    team = Team()
    actor, _ = desk(tmp_path, team)
    acknowledged = []
    actor.sent_words = lambda *args: acknowledged.append(args)
    actor.converse(view(), set())
    assert len(acknowledged) == 1
    tid, other, us, tick, mid, text, terms = acknowledged[0]
    assert (tid, other, us, tick, mid) == (42, "t05", "t01", 100, 1)
    assert text and terms["give"]["assets"] == [3]

    second, _ = desk(tmp_path / "unknown", Team())
    second.sent_words = lambda *args: acknowledged.append(args)

    def lost(*args, **kwargs):
        raise BazaarError("network", "lost response", 0)

    second.team.say = lost
    second.converse(view(), set())
    assert len(acknowledged) == 1
