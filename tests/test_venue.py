"""Our venue, build only: the writes refused by `allow_venue_open`, the cash floor and the kill switch, dry
runs, and the broker key (saved 0600, never printed, only sent to the host it belongs to). No network."""

import stat

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from bazaar_agent import cli
from bazaar_agent import guardrails as gr
from bazaar_agent import telemetry as tm
from bazaar_agent import venue as vn
from bazaar_agent.config import BROKER_ENV_FILE, ConfigError, Settings, load_settings

REAL_KEY = "bk_" + "A1b2C3d4E5f6G7h8"  # shapes only: no real key appears in this repo
SIM_KEY = "simbk-" + "Z9y8X7w6V5u4"
BUILD_ONLY = "- `allow_venue_open` = false — build only"


class FakeTeam:
    """The team client's venue routes. Every write is recorded; open returns a broker key once."""

    def __init__(self, cash=600):
        self.cash = cash
        self.sent: list[tuple] = []

    def me(self):
        return {"id": "t01", "cash": self.cash, "assets": []}

    def clock(self):
        return {"tick": 150, "t_hours": 2.5}

    def open_venue(self, name, fee_bps=300, fee_per_card=0, rules=None, description=""):
        self.sent.append(("open_venue", name, fee_bps, fee_per_card, rules))
        return {"venue": "v07", "broker_key": SIM_KEY, "name": name}

    def close_venue(self, venue):
        self.sent.append(("close_venue", venue))
        return {"ok": True}

    def set_fee(self, venue, fee_bps, fee_per_card=None):
        self.sent.append(("set_fee", venue, fee_bps, fee_per_card))
        return {"ok": True}


class FakeBroker:
    def __init__(self):
        self.sent: list[tuple] = []

    def announce(self, text):
        self.sent.append(("announce", text))
        return {"ok": True}


def rules(tmp_path, **kw):
    return gr.Guardrails(pause_file=str(tmp_path / "PAUSE"), **kw)


def ctx(cash=600, paused=False):
    return gr.Context(cash=cash, held={}, tick=150, t_hours=2.5, paused=paused)


SPEC = vn.VenueSpec(name="Team 1 market")


# ---------------------------------------------------------------- the writes


def test_open_is_a_dry_run_by_default_and_sends_nothing(tmp_path):
    team = FakeTeam()
    outcome, saved = vn.open_venue(
        team, SPEC, rules(tmp_path, allow_venue_open=True), live=False, settings=Settings(data_dir=tmp_path)
    )
    assert (outcome.sent, saved, team.sent) == (False, None, [])
    assert outcome.message.startswith("dry run: would open venue 'Team 1 market' (board")


def test_allow_venue_open_false_blocks_open_even_with_live(tmp_path):
    team = FakeTeam()
    outcome, saved = vn.open_venue(team, SPEC, rules(tmp_path), live=True, settings=Settings(data_dir=tmp_path))
    assert (outcome.sent, saved, team.sent) == (False, None, [])
    assert "LIVE: guardrails refuse" in outcome.message and "allow_venue_open = false" in outcome.message
    assert not (tmp_path / BROKER_ENV_FILE).exists()


def test_open_with_live_respects_the_cash_floor(tmp_path):
    team = FakeTeam(cash=500)
    outcome, _ = vn.open_venue(
        team, SPEC, rules(tmp_path, allow_venue_open=True), live=True, settings=Settings(data_dir=tmp_path)
    )
    assert team.sent == [] and "cash_floor" in str(outcome.verdict)


def test_a_live_allowed_open_sends_a_board_venue_and_saves_the_key_0600_never_returning_it(tmp_path):
    team = FakeTeam()
    outcome, saved = vn.open_venue(
        team,
        vn.VenueSpec(name="Team 1 market", fee_bps=0, fee_per_card=0),
        rules(tmp_path, allow_venue_open=True),
        live=True,
        settings=Settings(data_dir=tmp_path),
    )
    assert team.sent == [("open_venue", "Team 1 market", 0, 0, {"mechanism": "board"})]
    assert outcome.sent and saved == tmp_path / BROKER_ENV_FILE
    assert SIM_KEY not in str(outcome) and outcome.response == {
        "venue": "v07",
        "name": "Team 1 market",
        "broker_key": "[saved]",
    }
    assert stat.S_IMODE(saved.stat().st_mode) == 0o600
    assert SIM_KEY in saved.read_text() and "v07" in saved.read_text()


def test_load_settings_reads_the_saved_broker_key_and_never_shows_it(tmp_path, monkeypatch):
    vn.save_broker_key(tmp_path, "v07", SIM_KEY)
    monkeypatch.setenv("BAZAAR_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("BAZAAR_BROKER_KEY", raising=False)
    monkeypatch.delenv("BAZAAR_VENUE", raising=False)
    settings = load_settings(env_file=tmp_path / "no.env")
    assert settings.venue_id == "v07" and settings.require_broker_key() == SIM_KEY
    assert SIM_KEY not in repr(settings) and SIM_KEY not in str(settings)
    monkeypatch.setenv("BAZAAR_BROKER_KEY", REAL_KEY)  # the environment (Railway) wins
    assert load_settings(env_file=tmp_path / "no.env").require_broker_key() == REAL_KEY


def test_a_missing_broker_key_names_the_variable():
    with pytest.raises(ConfigError, match="BAZAAR_BROKER_KEY is not set"):
        Settings().require_broker_key()


def test_close_fee_and_announce_are_dry_runs_and_live_needs_the_switch(tmp_path):
    team, broker = FakeTeam(), FakeBroker()
    off, on = rules(tmp_path), rules(tmp_path, allow_venue_open=True)
    assert not vn.close_venue(team, "v07", on, live=False).sent
    assert not vn.set_fee(team, "v07", vn.FeeSpec(fee_bps=50), on, live=False).sent
    assert not vn.announce(broker, {"tick": 1}, vn.Announcement(text="0 % fees"), on, live=False).sent
    assert not vn.set_fee(team, "v07", vn.FeeSpec(fee_bps=50), off, live=True).sent
    assert not vn.announce(broker, {"tick": 1}, vn.Announcement(text="hi"), off, live=True).sent
    assert team.sent == [] and broker.sent == []
    assert vn.close_venue(team, "v07", off, live=True).sent  # closing a venue never waits for the switch
    assert vn.set_fee(team, "v07", vn.FeeSpec(fee_bps=50, fee_per_card=1), on, live=True).sent
    assert vn.announce(broker, {"tick": 1}, vn.Announcement(text="hi"), on, live=True).sent
    assert team.sent == [("close_venue", "v07"), ("set_fee", "v07", 50, 1)] and broker.sent == [("announce", "hi")]


def test_fees_are_capped_at_ten_percent_and_five_per_card():
    vn.VenueSpec(name="x", fee_bps=1000, fee_per_card=5)
    for bad in ({"fee_bps": 1001}, {"fee_per_card": 6}, {"fee_bps": -1}, {"mechanism": "dark_pool"}):
        with pytest.raises(ValidationError):
            vn.VenueSpec(name="x", **bad)
    with pytest.raises(ValidationError):
        vn.FeeSpec(fee_bps=2000)
    with pytest.raises(ValidationError):
        vn.VenueSpec(name="x" * 41)


# ---------------------------------------------------------------- the key goes only where it belongs


def test_a_broker_key_only_goes_to_its_own_host():
    vn.check_broker_key_for_url("https://bazaar.causaprima.ai", REAL_KEY)
    vn.check_broker_key_for_url("http://127.0.0.1:8000", SIM_KEY)
    for url, key in (
        ("https://bazaar.causaprima.ai", SIM_KEY),
        ("http://bazaar.causaprima.ai", REAL_KEY),
        ("https://bazaar-sim-production.up.railway.app", REAL_KEY),
    ):
        with pytest.raises(ConfigError) as e:
            vn.check_broker_key_for_url(url, key)
        assert key not in str(e.value)


def test_broker_key_shapes_are_scrubbed_from_logs_and_spans():
    assert REAL_KEY not in tm.scrub(f"book read with {REAL_KEY}")
    assert SIM_KEY not in tm.scrub(f"key={SIM_KEY}")


# ---------------------------------------------------------------- the CLI


def test_cli_venue_open_live_is_refused_while_build_only(tmp_path, monkeypatch):
    team = FakeTeam()
    monkeypatch.setattr(cli, "load_settings", lambda: Settings(data_dir=tmp_path))
    monkeypatch.setattr(cli, "_team_client", lambda: team)
    monkeypatch.setattr(cli, "_rules", lambda: gr.parse_guardrails(BUILD_ONLY))  # not the committed file's state
    result = CliRunner().invoke(cli.app, ["venue", "open", "--live", "--fee-bps", "0"])
    assert result.exit_code == 1, result.output
    assert "allow_venue_open = false" in " ".join(result.output.split())
    assert team.sent == []


def test_cli_venue_fee_rejects_a_fee_above_the_cap_before_anything_is_sent(tmp_path, monkeypatch):
    team = FakeTeam()
    monkeypatch.setattr(cli, "load_settings", lambda: Settings(data_dir=tmp_path, venue_id="v07"))
    monkeypatch.setattr(cli, "_team_client", lambda: team)
    result = CliRunner().invoke(cli.app, ["venue", "fee", "1500", "--live"])
    assert result.exit_code == 1 and "invalid" in result.output and team.sent == []
