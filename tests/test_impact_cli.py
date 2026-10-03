"""`bazaar impact`: the guard's score impact estimate of one move, through the CLI with fakes (no network, no
Postgres). The incident it is fitted to: SAL-07 (asset 438, bought from t02 at tick 320 for 23, completing Salamanca,
your_value 118.6) sold to dealer Pilar for 29 at tick 947."""

from __future__ import annotations

import json
from typing import Any

import psycopg
import pytest
import typer
from typer.testing import CliRunner

from bazaar_agent import approvals, impact_cli
from bazaar_agent import guardrails as gr
from bazaar_agent import move_impact as mi
from bazaar_agent.config import ConfigError
from bazaar_agent.sdk import BazaarError

RULES = gr.Guardrails(max_score_loss_per_move=0.2, score_per_neg_point_fallback=0.053, dealer_ladder_score=0.05)
SETTLEMENT_408 = {  # the real tape payload: SAL-07 from t02 at tick 320
    "settlement": 408,
    "tick": 320,
    "parties": ["t02", "t01"],
    "price": 23,
    "items": [{"id": 438, "ref": "SAL-07", "frm": "t02", "to": "t01"}],
}
SAL_COMPLETE = {"pages": [{"set": "SAL", "have": 10, "of": 10, "complete": True}]}

app = typer.Typer()
app.command("impact")(impact_cli.impact)


@app.callback()
def _bazaar() -> None:
    """Keeps `impact` a subcommand, as in `bazaar impact`."""


def card(asset: int, ref: str, value: float, rarity: str = "common") -> dict[str, Any]:
    return {"id": asset, "kind": "card", "ref": ref, "rarity": rarity, "your_value": value}


def me(*assets: dict[str, Any], album: dict[str, Any] | None = None, tick: Any = 947) -> dict[str, Any]:
    return {"id": "t01", "tick": tick, "assets": list(assets), "album": album or {"pages": []}}


INCIDENT_ME = me(card(438, "SAL-07", 118.6, "uncommon"), album=SAL_COMPLETE)
INCIDENT_FACTS = mi.Facts("t01", mi.origins([SETTLEMENT_408], "t01"), ())
NO_ORIGINS = mi.Facts("t01", {}, ())
STARTING_LAV03 = me(card(3, "LAV-03", 15.0), card(5, "LAV-03", 15.0))  # t01 was dealt asset ids 1-15


class Reads:
    """Everything `impact` reads: GET /api/me, GET /api/me/value and the guard's facts. Nothing else exists here,
    so the command cannot send anything else."""

    def __init__(
        self,
        me_payload: Any,
        facts: mi.Facts | None = None,
        values: dict[str, Any] | None = None,
        me_error: Exception | None = None,
        value_error: Exception | None = None,
        facts_error: Exception | None = None,
    ) -> None:
        self.me_payload, self.facts_payload, self.values = me_payload, facts, values or {}
        self.me_error, self.value_error, self.facts_error = me_error, value_error, facts_error
        self.sent: list[str] = []
        self.facts_ticks: list[int] = []

    def me(self) -> Any:
        self.sent.append("GET /api/me")
        if self.me_error is not None:
            raise self.me_error
        return self.me_payload

    def value(self, ref: str) -> Any:
        self.sent.append(f"GET /api/me/value?card={ref}")
        if self.value_error is not None:
            raise self.value_error
        return self.values[ref]

    def facts(self, tick: int) -> mi.Facts | None:
        self.facts_ticks.append(tick)
        if self.facts_error is not None:
            raise self.facts_error
        return self.facts_payload


@pytest.fixture
def run(monkeypatch):
    def invoke(reads: Reads, *args: str, rules: gr.Guardrails = RULES) -> Any:
        monkeypatch.setattr(impact_cli, "_game_readers", lambda: (reads.me, reads.value))
        monkeypatch.setattr(impact_cli, "postgres_facts", reads.facts)
        monkeypatch.setattr(impact_cli, "_rules", lambda: rules)
        return CliRunner().invoke(app, ["impact", *args])

    return invoke


def as_json(result: Any) -> dict[str, Any]:
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)  # stdout only: the notes go to stderr


# ---------------------------------------------------------------- the incident


def test_the_sal07_incident_replayed_through_the_cli_is_refused(run):
    reads = Reads(INCIDENT_ME, facts=INCIDENT_FACTS)
    out = as_json(run(reads, "sell", "SAL-07", "29", "--to", "pilar", "--json"))
    assert out["score_delta"] == pytest.approx(-4.7, abs=0.5)
    assert out["neg_points_delta"] == pytest.approx(29 - 118.6)
    assert out["k"] == 0.053 and out["copy_origin"] == "team" and out["copy_from"] == "t02"
    assert out["breaks_complete_page"] is True and out["team_trade"] is False and out["facts_read"] is True
    assert out["verdict"] == "refuse"
    assert out["approve"] == "uv run bazaar approve SAL-07 --sell --min 29"
    assert (out["card"], out["asset"], out["value"], out["counterparty"]) == ("SAL-07", 438, 118.6, "pilar")
    assert reads.sent == ["GET /api/me"] and reads.facts_ticks == [947]  # one game read, the tick from /me


def test_the_incident_in_words_names_the_origin_the_page_and_the_approval(run):
    result = run(Reads(INCIDENT_ME, facts=INCIDENT_FACTS), "sell", "SAL-07", "29", "--to", "pilar")
    assert result.exit_code == 0, result.output
    text = result.stdout
    assert "sell SAL-07 at 29 to pilar · a dealer deal" in text
    assert "score -4.70 · neg_points -89.6 · k 0.053 (fallback) · dealer ladder +0.05" in text
    assert "asset 438 · your_value 118.6 · bought from t02 for 23 at tick 320" in text
    assert "BREAKS A COMPLETE PAGE" in text
    assert "the guard would refuse this (score impact -4.70 < -0.2 = -max_score_loss_per_move)" in text
    assert "approve it first with `uv run bazaar approve SAL-07 --sell --min 29`" in text
    assert result.stderr == ""  # the facts were read: nothing to note


def test_a_swap_prices_the_copy_we_give_at_what_we_receive(run):
    out = as_json(run(Reads(INCIDENT_ME, facts=INCIDENT_FACTS), "swap", "SAL-07", "40", "--to", "T05", "--json"))
    assert out["side"] == "swap" and out["counterparty"] == "t05" and out["team_trade"] is True
    assert out["neg_points_delta"] == pytest.approx(40 - 118.6)
    assert out["score_delta"] == pytest.approx(-78.6 * 0.053, abs=1e-3)  # as_state() rounds to 3 decimals
    assert out["verdict"] == "refuse" and out["approve"] == "uv run bazaar approve SAL-07 --sell --min 40"


# ---------------------------------------------------------------- what the guard lets through


def test_a_starting_stock_duplicate_sold_to_a_dealer_above_its_value_is_not_refused(run):
    out = as_json(run(Reads(STARTING_LAV03, facts=NO_ORIGINS), "sell", "LAV-03", "20", "--to", "abuela", "--json"))
    assert out["copy_origin"] == "start" and out["origin_assumed"] is False
    assert out["neg_points_delta"] == 0 and out["score_delta"] == pytest.approx(0.05)  # the dealer ladder only
    assert out["breaks_complete_page"] is False and out["verdict"] == "within" and out["approve"] is None


def test_the_asset_names_the_copy_and_the_default_is_the_copy_that_costs_us_most(run):
    two = me(card(438, "SAL-07", 40.0, "uncommon"), card(500, "SAL-07", 40.0, "uncommon"), album=SAL_COMPLETE)
    reads = Reads(two, facts=INCIDENT_FACTS)  # 438 came from t02; 500 from a pack (no settlement, not our start)
    worst = as_json(run(reads, "sell", "SAL-07", "29", "--to", "pilar", "--json"))
    assert (worst["asset"], worst["copy_origin"], worst["verdict"]) == (438, "team", "refuse")
    assert worst["breaks_complete_page"] is False  # a second copy keeps the page
    pack = as_json(run(reads, "sell", "SAL-07", "29", "--to", "pilar", "--asset", "500", "--json"))
    assert (pack["asset"], pack["copy_origin"], pack["verdict"]) == (500, "pack", "within")
    missing = run(reads, "sell", "SAL-07", "29", "--to", "pilar", "--asset", "999", "--json")
    assert as_json(missing)["verdict"] == "refuse"  # no value for that copy: the guard fails closed
    assert "asset 999 is not one of our SAL-07 copies in /api/me (438, 500)" in missing.stderr


def test_a_card_we_do_not_hold_cannot_be_priced_and_the_guard_would_refuse_it(run):
    result = run(Reads(INCIDENT_ME, facts=INCIDENT_FACTS), "sell", "lav-09", "70", "--to", "pilar")
    assert result.exit_code == 0, result.output
    assert "we hold no LAV-09 in /api/me" in result.stderr
    assert "estimate: cannot be estimated (no value for the copy)" in result.stdout
    assert "the guard would refuse this (score impact cannot be estimated)" in result.stdout


def test_with_the_rule_off_the_estimate_prints_and_nothing_is_refused(run):
    off = gr.Guardrails(max_score_loss_per_move=0)
    reads = Reads(INCIDENT_ME, facts=INCIDENT_FACTS)
    out = as_json(run(reads, "sell", "SAL-07", "29", "--to", "pilar", "--json", rules=off))
    assert out["verdict"] == "off" and out["score_delta"] == pytest.approx(-4.70, abs=0.01)
    text = run(reads, "sell", "SAL-07", "29", "--to", "pilar", rules=off).stdout
    assert "max_score_loss_per_move is 0 (off): the guard does not check the score impact" in text


@pytest.mark.score_impact
@pytest.mark.parametrize(
    ("me_payload", "facts", "args", "action", "verdict"),
    [
        (INCIDENT_ME, INCIDENT_FACTS, ["sell", "SAL-07", "29", "--to", "pilar"], ("dealer_sell", 29, None), "refuse"),
        (STARTING_LAV03, NO_ORIGINS, ["sell", "LAV-03", "20", "--to", "abuela"], ("dealer_sell", 20, None), "within"),
        (STARTING_LAV03, NO_ORIGINS, ["sell", "LAV-03", "5", "--to", "abuela"], ("dealer_sell", 5, None), "within"),
        (STARTING_LAV03, NO_ORIGINS, ["sell", "LAV-03", "5", "--to", "t05"], ("accept_sell", 5, "t05"), "refuse"),
        (STARTING_LAV03, None, ["sell", "LAV-03", "5", "--to", "abuela"], ("dealer_sell", 5, None), "refuse"),
        (INCIDENT_ME, INCIDENT_FACTS, ["swap", "SAL-07", "40", "--to", "t05"], ("accept_sell", 40, "t05"), "refuse"),
        (INCIDENT_ME, INCIDENT_FACTS, ["sell", "SAL-07", "120"], ("sell", 120, gr.ANY_TEAM), "within"),
    ],
)
def test_the_cli_verdict_is_the_verdict_of_the_guard_itself(run, me_payload, facts, args, action, verdict):
    """`guardrails._impact_violations` on the same move: the CLI never says "within" where the guard refuses."""
    kind, price, counterparty = action
    cards = mi.our_cards(me_payload)
    held = cards.of(args[1])
    sale = gr.Action(kind, args[1], held[0].rarity, price, held[0].your_value, counterparty=counterparty)
    ctx = gr.Context(cash=500, held={}, tick=947, t_hours=10.0, approvals=approvals.EMPTY, cards=cards, impact=facts)
    asked: list[dict[str, Any]] = []
    old = approvals.install(approvals.ApprovalBoard(None, write=asked.append))  # no database, requests in a list
    try:
        guard_refuses = bool(gr._impact_violations(sale, ctx, RULES))
    finally:
        approvals._BOARD.pop("board", None)
        if old is not None:
            approvals.install(old)
    out = as_json(run(Reads(me_payload, facts=facts), *args, "--json"))
    assert out["verdict"] == verdict and guard_refuses is (verdict == "refuse")
    assert len(asked) == guard_refuses  # the guard asks a human once for what it refuses


# ---------------------------------------------------------------- facts the guard could not read


def test_unreadable_facts_still_estimate_at_the_worst_case_and_say_so_on_stderr(run):
    down = psycopg.OperationalError('connection to "postgresql://bazaar:hunter2@db.internal:5432/railway" failed')
    reads = Reads(STARTING_LAV03, facts_error=down)
    result = run(reads, "sell", "LAV-03", "5", "--to", "abuela", "--json")
    out = as_json(result)
    assert out["facts_read"] is False and out["copy_origin"] == "unknown" and out["origin_assumed"] is True
    assert out["neg_points_delta"] == -10.0 and out["score_delta"] == pytest.approx(-10 * 0.053 + 0.05)
    assert out["verdict"] == "refuse"
    assert "facts unreadable (OperationalError): every copy counted as bought from a team, k at its fallback" in (
        result.stderr
    )
    assert "hunter2" not in result.output and "railway" not in result.output  # the type only, never the error text
    human = run(reads, "sell", "LAV-03", "5", "--to", "abuela")
    assert "score -0.48" in human.stdout and "priced as a copy bought from a team (the worst case)" in human.stdout
    read = as_json(run(Reads(STARTING_LAV03, facts=NO_ORIGINS), "sell", "LAV-03", "5", "--to", "abuela", "--json"))
    assert read["copy_origin"] == "start" and read["verdict"] == "within"  # read, it is starting stock


@pytest.mark.parametrize(
    ("facts", "why"),
    [(None, "no /me snapshot in Postgres names us yet"), (mi.Facts("t09", {}, ()), "the newest snapshot is t09's")],
)
def test_facts_that_name_nobody_or_another_team_count_as_unread(run, facts, why):
    result = run(Reads(INCIDENT_ME, facts=facts), "sell", "SAL-07", "29", "--to", "pilar", "--json")
    out = as_json(result)
    assert out["facts_read"] is False and out["origin_assumed"] is True and out["verdict"] == "refuse"
    assert "facts unreadable" in result.stderr and why in result.stderr


def test_the_facts_window_ends_at_the_tick_in_me_unless_tick_is_given(run):
    reads = Reads(INCIDENT_ME, facts=INCIDENT_FACTS)
    as_json(run(reads, "sell", "SAL-07", "29", "--json"))
    as_json(run(reads, "sell", "SAL-07", "29", "--json", "--tick", "900"))
    assert reads.facts_ticks == [947, 900]
    no_tick = Reads(me(card(438, "SAL-07", 118.6), tick=True), facts=INCIDENT_FACTS)
    result = run(no_tick, "sell", "SAL-07", "29", "--json")
    assert as_json(result)["facts_read"] is False and no_tick.facts_ticks == []
    assert "no tick in /api/me: pass --tick" in result.stderr


# ---------------------------------------------------------------- buys


def test_a_buy_from_a_team_under_our_value_is_a_gain_and_only_informational(run):
    reads = Reads(me(), facts=NO_ORIGINS, values={"LAV-10": {"card": "LAV-10", "your_value": 80.0}})
    out = as_json(run(reads, "buy", "LAV-10", "60", "--from", "t05", "--json"))
    assert out["neg_points_delta"] == 20.0 and out["score_delta"] == pytest.approx(20 * 0.053)
    assert out["team_trade"] is True and out["value"] == 80.0
    assert out["verdict"] == "informational" and out["approve"] is None
    assert reads.sent == ["GET /api/me", "GET /api/me/value?card=LAV-10"]  # two reads: the most a run sends
    text = run(reads, "buy", "LAV-10", "60", "--from", "t05").stdout
    assert "buy LAV-10 at 60 from t05 · a team trade" in text
    assert "one more LAV-10 is worth 80 to us (GET /api/me/value)" in text
    assert "informational: the guard checks sales only" in text


@pytest.mark.parametrize(
    ("answer", "error", "why"),
    [
        (None, BazaarError("rate_limited", "slow down", 429), "rate_limited"),
        ({"card": "LAV-10", "your_value": "lots"}, None, "ValidationError"),
        ({"card": "LAV-09", "your_value": 80.0}, None, "the answer names LAV-09"),
    ],
)
def test_a_buy_whose_value_cannot_be_read_stops_with_exit_1(run, answer, error, why):
    reads = Reads(me(), facts=NO_ORIGINS, values={"LAV-10": answer}, value_error=error)
    result = run(reads, "buy", "LAV-10", "60", "--from", "t05", "--json")
    assert result.exit_code == 1 and result.stdout == ""
    assert "value of LAV-10 unknown" in result.stderr and why in result.stderr


# ---------------------------------------------------------------- hostile text and bad input


def test_hostile_markup_from_a_counterparty_or_the_tape_prints_as_text(run):
    hostile = "[/red][bold]pilar[link=https://x.example]\\"
    taped = mi.Facts("t01", {438: mi.Origin("dealer", "[/]evil[/bold]", 5, 10)}, ())
    result = run(Reads(INCIDENT_ME, facts=taped), "sell", "SAL-07", "29", "--to", hostile)
    assert result.exit_code == 0, result.output
    assert "to [/red][bold]pilar[link=https://x.example]" in result.stdout
    assert "bought from [/]evil[/bold] for 5 at tick 10" in result.stdout
    out = as_json(run(Reads(INCIDENT_ME, facts=taped), "sell", "SAL-07", "29", "--to", hostile, "--json"))
    assert out["counterparty"] == hostile and out["team_trade"] is False
    bad = run(Reads(INCIDENT_ME), "sell", "[/red]", "29")
    assert bad.exit_code == 2 and "not a card ref: [/red]" in bad.stderr


@pytest.mark.parametrize(
    "args",
    [
        ["sel", "SAL-07", "29"],
        ["sell", "SAL07", "29"],
        ["sell", "SAL-07", "nan"],
        ["sell", "SAL-07", "0.5"],
        ["sell", "SAL-07", "29", "--from", "t05"],
        ["buy", "SAL-07", "29", "--to", "t05"],
        ["buy", "SAL-07", "29", "--asset", "438"],
    ],
)
def test_bad_input_stops_with_exit_2_before_any_read(run, args):
    reads = Reads(INCIDENT_ME, facts=INCIDENT_FACTS)
    result = run(reads, *args)
    assert result.exit_code == 2, result.output
    assert reads.sent == [] and reads.facts_ticks == []


@pytest.mark.parametrize(
    "reads",
    [Reads(None, me_error=BazaarError("bad_key", "wrong key", 401)), Reads(["not", "an", "object"])],
)
def test_an_unreadable_me_stops_with_exit_1_before_the_facts(run, reads):
    result = run(reads, "sell", "SAL-07", "29")
    assert result.exit_code == 1 and "/api/me" in result.stderr and reads.facts_ticks == []


# ---------------------------------------------------------------- the real readers (faked at their edges)


def test_the_game_readers_share_one_client_with_no_write_tracker_and_no_resend(monkeypatch):
    made: list[dict[str, Any]] = []

    class Client:  # GET /api/me and GET /api/me/value only: anything else would be an AttributeError
        def me(self) -> dict[str, Any]:
            return INCIDENT_ME

        def value(self, ref: str) -> dict[str, Any]:
            return {"card": ref, "your_value": 80.0}

    def team_client(settings: Any, **options: Any) -> Client:
        made.append(options)
        return Client()

    monkeypatch.setattr(impact_cli, "load_settings", lambda: object())
    monkeypatch.setattr(impact_cli, "team_client", team_client)
    me_read, value_read = impact_cli._game_readers()
    assert made == []  # no client before the first read
    assert me_read() == INCIDENT_ME and value_read("LAV-10")["your_value"] == 80.0
    assert made == [{"track": False, "retries": 0}]


def test_a_missing_team_key_stops_with_exit_1(monkeypatch, capsys):
    def no_key(settings: Any, **options: Any) -> Any:
        raise ConfigError("BAZAAR_KEY is not set: add it to .env (the key on the team slip).")

    monkeypatch.setattr(impact_cli, "load_settings", lambda: object())
    monkeypatch.setattr(impact_cli, "team_client", no_key)
    me_read, _ = impact_cli._game_readers()
    with pytest.raises(typer.Exit) as stop:
        me_read()
    assert stop.value.exit_code == 1 and "BAZAAR_KEY is not set" in capsys.readouterr().err


def test_postgres_facts_are_read_in_a_read_only_transaction(monkeypatch):
    seen: dict[str, Any] = {}

    class Conn:
        read_only = False

        def __init__(self) -> None:
            self.sql: list[str] = []

        def __enter__(self) -> Conn:
            return self

        def __exit__(self, *exc: Any) -> None:
            seen["closed"] = True

        def execute(self, sql: str) -> None:
            self.sql.append(sql)

    conn = Conn()

    def connect(**options: Any) -> Conn:
        seen.update(options)
        return conn

    def read_facts(c: Conn, tick: int) -> mi.Facts:
        seen.update(read_only=c.read_only, tick=tick, sql=list(c.sql))
        return INCIDENT_FACTS

    monkeypatch.setattr(impact_cli.pgconn, "connect", connect)
    monkeypatch.setattr(impact_cli.impact_board, "read_facts", read_facts)
    assert impact_cli.postgres_facts(947) is INCIDENT_FACTS
    assert seen["app"] == "bazaar-impact-cli" and seen["connect_timeout_s"] == 3
    assert seen["read_only"] is True and seen["tick"] == 947 and seen["closed"] is True
    assert seen["sql"] == [f"set statement_timeout = {impact_cli.STATEMENT_TIMEOUT_MS}"]


def test_the_rules_come_from_guardrails_md_and_an_invalid_file_stops_with_exit_1(monkeypatch):
    assert impact_cli._rules().score_per_neg_point_fallback > 0

    def invalid() -> Any:
        raise gr.GuardrailsError("GUARDRAILS.md:3: rule `x` is defined twice")

    monkeypatch.setattr(impact_cli, "load_guardrails", invalid)
    with pytest.raises(typer.Exit) as stop:
        impact_cli._rules()
    assert stop.value.exit_code == 1
