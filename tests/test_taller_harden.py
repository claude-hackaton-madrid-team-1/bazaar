"""The Workshop hardened on SA1: /me and our offers read again before a craft, a hold on an accept still settling
that cannot name its copy, one hourly cap for every process (shared ledger), the duel and Market Test guard, and the
card a craft brings credited in its score impact."""

from collections import Counter
from dataclasses import replace

from bazaar_agent import move_impact as mi
from bazaar_agent.agents import taller as tl
from bazaar_agent.guardrails import Action, Guardrails, Ledger, check
from tests.test_taller import CATALOG, LEVELS, SPARES, News, Team, crafts, ctx, me, run_taker

TICK = 100
FREE = [a for a in SPARES["assets"] if a["ref"] != "LAV-07"]  # LAV-01 x3 (#1-3), SAL-01 x2 (#4-5)


# ---------------------------------------------------------------- an accept that cannot name its copy


def test_an_unnamed_accept_still_settling_holds_every_craft_before_any_request(tmp_path):
    def booked(t, ledger):
        ledger.record("accept", TICK - 1, 1.4, 0, "team:77")  # we took a swap last tick: it gives some copy

    team, lines = run_taker(tmp_path, news=News(LEVELS), taller_enabled=True, before=booked)
    assert crafts(team) == [] and "duels" not in team.reads
    assert any("team:77 still settling" in line for line in lines)


def test_unnamed_settling_names_only_accepts_that_cannot_name_their_copy(tmp_path):
    ledger = Ledger(tmp_path / "l.jsonl")
    for item in ("sell:5", "duel:9", "LAV-01", "sobre_barrio"):
        ledger.record("accept", TICK, 1.5, 0, item)
    ledger.record("accept", TICK - 3, 1.3, 0, "team:78")  # older than SETTLING_TICKS: settled
    assert tl.unnamed_settling(ledger, TICK) is None
    ledger.record("accept", TICK - 2, 1.4, 0, "team:77")
    assert "team:77" in (tl.unnamed_settling(ledger, TICK) or "")


def test_the_guardrails_refuse_a_craft_while_such_an_accept_settles():
    hold = "accept team:77 still settling may hand over a copy we cannot name"
    verdict = check(
        Action("taller", "LAV-01,LAV-01,SAL-01", "common"), ctx(taller_hold=hold), Guardrails(taller_enabled=True)
    )
    assert not verdict.allowed and "team:77" in str(verdict)


# ---------------------------------------------------------------- /me and our offers read again


class MakerAsksMidTick(Team):
    """/api/me/offers gains an ask of ours (another process: SAL-01 #4) after the tick's first read."""

    def my_offers(self):
        out = super().my_offers()
        if self.reads.count("my_offers") > 1:
            give = {"cash": 0, "assets": [{"id": 4, "kind": "card", "ref": "SAL-01"}]}
            out["offers"].append({"id": 900, "maker": "t01", "status": "open", "venue": "rastro", "give": give,
                                  "want": {"cash": 9}})  # fmt: skip
        return out


def test_the_craft_is_planned_on_offers_read_again_right_before_it(tmp_path):
    team, _ = run_taker(tmp_path, news=News(LEVELS), team=MakerAsksMidTick(me=me(*FREE)), taller_enabled=True)
    assert crafts(team) == []  # SAL-01 #4 is in an ask now: #5 is our last free copy, no triple left
    assert team.reads.count("me") >= 2 and team.reads.count("my_offers") >= 2


# ---------------------------------------------------------------- one hourly cap for every process


def test_a_craft_another_process_booked_this_hour_stops_the_taker_before_any_request(tmp_path):
    def cli_craft(t, ledger):
        tl.book_craft(ledger, TICK - 5, 1.2, ["LAV-01", "LAV-01", "SAL-01"])  # `bazaar taller --live`

    team, _ = run_taker(tmp_path, news=News(LEVELS), taller_enabled=True, max_taller_per_game_hour=1, before=cli_craft)
    assert crafts(team) == [] and "duels" not in team.reads


def test_a_craft_row_passes_the_shared_ledgers_kind_check_and_adds_no_spend(tmp_path):
    from bazaar_agent.ledger_pg import PgLedger
    from tests.pg_fakes import FakePostgres

    pg = FakePostgres(tmp_path / "shared.db")  # the table's check: kind in ('spend', 'accept', 'listing')
    taker_ledger, cli_ledger = PgLedger(pg.connect, "taker"), PgLedger(pg.connect, "taller")
    tl.book_craft(cli_ledger, TICK, 1.5, ["LAV-01", "LAV-01", "SAL-01"])
    assert tl.crafts_last_hour(taker_ledger, 1.6) == 1 and tl.crafts_last_hour(taker_ledger, 2.6) == 0
    assert taker_ledger.spent_since(1.0) == 0 and taker_ledger.packs_since(1.0) == Counter()


# ---------------------------------------------------------------- the duel and Market Test guard


def test_the_taker_waits_for_the_guards_next_safe_tick_near_a_duel_deadline(tmp_path):
    team = Team(me=me(*FREE))
    team.live_duels = [{"duel": 7, "status": "live", "deadline_tick": TICK + 2}]
    team, lines = run_taker(tmp_path, news=News(LEVELS), team=team, ticks=4, taller_enabled=True)
    assert team.reads.count("duels") == 2  # ticks 100 (blocked until 103) and 103: no request in between
    assert len(crafts(team)) == 1 and any("Workshop waits until tick 103" in line for line in lines)


# ---------------------------------------------------------------- the score impact credits the card received


def test_the_score_impact_credits_the_card_a_craft_brings():
    rules = Guardrails(taller_enabled=True, max_score_loss_per_move=0.001)
    ours = me(*FREE)
    bought = mi.Facts("t01", {a: mi.Origin("team", "t05", 4, 50) for a in (2, 3, 5)})  # team-bought copies
    base = ctx(cards=mi.our_cards(ours), impact=bought, sellable={"LAV-01": 3, "SAL-01": 2})
    plain = Action("taller", "SAL-01,LAV-01,LAV-01", "common", assets=(5, 2, 3))
    assert "max_score_loss_per_move" in str(check(plain, base, rules))  # copies given, nothing received: refused
    gain = tl.received_value(CATALOG, "uncommon")  # 25: what a pull of the result's rarity is worth to us
    assert check(replace(plain, your_value=gain), base, rules).allowed  # 25 received > 9.3 given
    assert tl.received_value({}, "uncommon") is None  # no book: no credit (fail closed)


# ---------------------------------------------------------------- `bazaar taller --live` by hand


def cli_with(monkeypatch, tmp_path, team):
    from types import SimpleNamespace

    from bazaar_agent import cli
    from bazaar_agent.config import Settings
    from tests.agent_fakes import FakePublic
    from tests.test_taller import DEALERS

    ledger = Ledger(tmp_path / "ledger.jsonl")
    rules = Guardrails(taller_enabled=True, max_taller_per_game_hour=1)
    context = replace(ctx(), cards=mi.our_cards(team.me()), tick=TICK, t_hours=1.5)
    monkeypatch.setattr(cli, "_team_me", lambda: (team, team.me()))
    monkeypatch.setattr(
        cli, "_sell_context", lambda client, me, live: (rules, ledger, context, SimpleNamespace(listed=()))
    )
    monkeypatch.setattr(cli, "load_settings", lambda: Settings(data_dir=tmp_path))
    monkeypatch.setattr(cli, "public_client", lambda settings: FakePublic(catalog=CATALOG, dealers=DEALERS))
    return cli, ledger


def test_the_cli_books_its_craft_in_the_shared_ledger_and_then_keeps_the_cap(monkeypatch, tmp_path):
    from typer.testing import CliRunner

    team = Team(me=me(*FREE))
    cli, ledger = cli_with(monkeypatch, tmp_path, team)
    first = CliRunner().invoke(cli.app, ["taller", "5", "2", "3", "--live"])
    assert first.exit_code == 0, first.output
    assert crafts(team) == [("call", "POST", "/api/taller", {"assets": [5, 2, 3]})]
    assert [e["item"] for e in ledger.entries()] == ["taller:SAL-01,LAV-01,LAV-01"]
    second = CliRunner().invoke(cli.app, ["taller", "5", "2", "3", "--live"])
    assert len(crafts(team)) == 1 and "max_taller_per_game_hour 1" in second.output


def test_the_cli_holds_while_an_unnamed_accept_settles(monkeypatch, tmp_path):
    from typer.testing import CliRunner

    team = Team(me=me(*FREE))
    cli, ledger = cli_with(monkeypatch, tmp_path, team)
    ledger.record("accept", TICK, 1.5, 0, "team:77")
    result = CliRunner().invoke(cli.app, ["taller", "5", "2", "3", "--live"])
    assert crafts(team) == [] and "team:77" in result.output
