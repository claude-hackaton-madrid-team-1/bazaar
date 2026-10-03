"""Our venue, build only: the writes refused by `allow_venue_open`, the cash floor and the kill switch, dry
runs, and the broker key (saved 0600, never printed, only sent to the host it belongs to). No network."""

import stat
from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError
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
        return {"tick": 150, "t_hours": 7.0}

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
    return gr.Context(cash=cash, held={}, tick=150, t_hours=7.0, paused=paused)


SPEC = vn.VenueSpec(name="Team 1 market")


def vault(data_dir, connect=None):
    return vn.KeyVault(data_dir, connect)


class FakeConn:
    """psycopg's surface the vault uses (execute(...).fetchone(), autocommit, closed, close()), over a dict
    {(target, venue): (key, tick)} that stands for the `venue_broker_keys` table."""

    def __init__(self, store, fail=False):
        self.store, self.fail, self.closed, self.autocommit = store, fail, False, False

    def execute(self, sql, params=()):
        if self.fail:
            raise RuntimeError("database down")
        self.last = None
        if sql.startswith("create table"):
            return self
        if sql.startswith("select count(*) from venue_broker_keys where target = %s and venue <> %s"):
            target, claim = params
            self.last = (sum(1 for t, v in self.store if t == target and v != claim),)
        elif sql.startswith("select count(*)"):
            self.last = (sum(1 for t, _ in self.store if t == params[0]),)
        elif sql.startswith("insert into venue_broker_keys") and "do nothing" in sql:  # a venue without its key
            target, venue, tick = params
            self.store.setdefault((target, venue), ("", tick))
        elif sql.startswith("insert into venue_broker_keys") and "returning venue" in sql:  # the claim
            target, claim, owner, tick, stale, _ = params
            held = self.store.get((target, claim))
            if held is None or held[1] < stale or held[0] == owner:
                self.store[(target, claim)] = (owner, tick)
                self.last = (claim,)
        elif sql.startswith("insert into venue_broker_keys"):
            target, venue, key, tick = params
            self.store[(target, venue)] = (key, tick)
        elif sql.startswith("delete from venue_broker_keys"):
            target, venue, owner = params
            if self.store.get((target, venue), ("", 0))[0] == owner:
                del self.store[(target, venue)]
        elif sql.startswith("select venue, broker_key"):
            target, claim, wanted, _ = params
            rows = [(v, k) for (t, v), (k, _) in self.store.items() if t == target and v != claim and k]
            rows = [r for r in rows if wanted in (None, r[0])]
            self.last = rows[-1] if rows else None
        else:
            raise AssertionError(f"unexpected SQL {sql}")
        return self

    def fetchone(self):
        return self.last

    def close(self):
        self.closed = True


# ---------------------------------------------------------------- the writes


def test_open_is_a_dry_run_by_default_and_sends_nothing(tmp_path):
    team = FakeTeam()
    outcome, saved = vn.open_venue(
        team, SPEC, rules(tmp_path, allow_venue_open=True), live=False, vault=vault(tmp_path)
    )
    assert (outcome.sent, saved, team.sent) == (False, None, [])
    assert outcome.message.startswith("dry run: would open venue 'Team 1 market' (board")


def test_allow_venue_open_false_blocks_open_even_with_live(tmp_path):
    team = FakeTeam()
    outcome, saved = vn.open_venue(team, SPEC, rules(tmp_path), live=True, vault=vault(tmp_path))
    assert (outcome.sent, saved, team.sent) == (False, None, [])
    assert "LIVE: guardrails refuse" in outcome.message and "allow_venue_open = false" in outcome.message
    assert not (tmp_path / BROKER_ENV_FILE).exists()


def test_open_with_live_respects_the_cash_floor(tmp_path):
    team = FakeTeam(cash=369)
    outcome, _ = vn.open_venue(
        team, SPEC, rules(tmp_path, allow_venue_open=True, cash_floor=100), live=True, vault=vault(tmp_path)
    )
    assert team.sent == [] and "cash_floor" in str(outcome.verdict)


def test_a_live_allowed_open_sends_a_board_venue_and_saves_the_key_0600_never_returning_it(tmp_path):
    team, store = FakeTeam(), {}
    outcome, opened = vn.open_venue(
        team,
        vn.VenueSpec(name="Team 1 market", fee_bps=0, fee_per_card=0),
        rules(tmp_path, allow_venue_open=True),
        live=True,
        vault=vault(tmp_path, lambda: FakeConn(store)),
        durable=True,
    )
    assert team.sent == [("open_venue", "Team 1 market", 0, 0, {"mechanism": "board"})]
    assert outcome.sent and opened is not None and opened.venue == "v07" and opened.saved == ("postgres", "file")
    assert (
        SIM_KEY not in str(outcome)
        and SIM_KEY not in repr(opened)
        and outcome.response
        == {
            "venue": "v07",
            "name": "Team 1 market",
            "broker_key": "[saved]",
        }
    )
    assert store == {("", "v07"): (SIM_KEY, 150)}  # the claim row is gone once the key is saved
    saved = tmp_path / BROKER_ENV_FILE
    assert stat.S_IMODE(saved.stat().st_mode) == 0o600
    assert SIM_KEY in saved.read_text() and "v07" in saved.read_text()


def test_nothing_is_opened_when_the_broker_key_could_not_be_saved(tmp_path):
    team, blocker = FakeTeam(), tmp_path / "a-file"
    blocker.write_text("not a directory")
    with pytest.raises(ConfigError, match="not writable"):
        vn.open_venue(team, SPEC, rules(tmp_path, allow_venue_open=True), live=True, vault=vault(blocker / "x"))
    assert team.sent == []


def test_a_durable_open_needs_postgres_first_and_sends_nothing_without_it(tmp_path):
    team = FakeTeam()
    for broken in (vault(tmp_path), vault(tmp_path, lambda: FakeConn({}, fail=True))):
        with pytest.raises(ConfigError, match="Postgres cannot hold the broker key"):
            vn.open_venue(team, SPEC, rules(tmp_path, allow_venue_open=True), live=True, vault=broken, durable=True)
    assert team.sent == []


def test_the_vault_reads_postgres_first_then_the_file_then_the_environment(tmp_path):
    store = {}
    v = vault(tmp_path, lambda: FakeConn(store))
    assert v.load() is None
    vn.save_broker_key(tmp_path, "v07", SIM_KEY)
    found = v.load("v07")
    assert found is not None and (found.venue, found.where) == ("v07", "file") and SIM_KEY not in repr(found)
    assert v.save("v08", "simbk-" + "Q1w2E3r4T5y6", 200) == ("postgres", "file")
    found = v.load("v08")
    assert found is not None and found.where == "postgres" and found.key.get_secret_value().endswith("T5y6")
    assert v.load("v99") is None  # a key for another venue is never handed out
    env = vn.KeyVault(tmp_path / "empty", None, SecretStr(REAL_KEY), "v10")
    assert env.load("v10").where == "environment" and env.load("v11") is None
    down = vault(tmp_path / "empty2", lambda: FakeConn({}, fail=True))
    assert down.load("v07") is None and down.save("v07", SIM_KEY, 1) == ("file",)


def test_the_vault_table_is_the_schema_s_venue_broker_keys(tmp_path):
    from importlib.resources import files

    schema = files("bazaar_agent").joinpath("sql/schema.sql").read_text(encoding="utf-8")
    assert vn.VENUE_KEYS_DDL + ";" in schema


def test_saving_the_key_follows_no_symlink_planted_in_the_data_dir(tmp_path):
    victim = tmp_path / "victim.txt"
    victim.write_text("keep me")
    data = tmp_path / "shared"
    data.mkdir()
    for name in (f".{BROKER_ENV_FILE}.probe", "broker.tmp", BROKER_ENV_FILE):
        (data / name).symlink_to(victim)
    outcome, opened = vn.open_venue(
        FakeTeam(), SPEC, rules(tmp_path, allow_venue_open=True), live=True, vault=vault(data)
    )
    saved = data / BROKER_ENV_FILE
    assert outcome.sent and opened is not None and opened.saved == ("file",) and victim.read_text() == "keep me"
    assert not saved.is_symlink() and stat.S_IMODE(saved.stat().st_mode) == 0o600
    assert SIM_KEY in saved.read_text()


def test_a_save_that_still_fails_after_the_open_keeps_the_key_in_memory_and_never_shows_it(tmp_path, monkeypatch):
    def broken(*args):
        raise PermissionError("disk went read-only")

    monkeypatch.setattr(vn, "save_broker_key", broken)
    monkeypatch.setattr(vn, "check_key_file_writable", lambda data_dir: None)
    outcome, opened = vn.open_venue(
        FakeTeam(), SPEC, rules(tmp_path, allow_venue_open=True), live=True, vault=vault(tmp_path)
    )
    assert opened is not None and opened.saved == () and opened.key.get_secret_value() == SIM_KEY
    assert "NOWHERE" in outcome.message and SIM_KEY not in outcome.message


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


def test_one_postgres_blip_never_locks_the_vault_out_for_good(tmp_path):
    """Round-2 review P1: a call skipped by the backoff must not re-arm it, or the h6.5 opening never comes."""
    store, attempts = {}, []

    def flaky():
        attempts.append(1)
        return FakeConn(store, fail=len(attempts) == 1)  # only the first connect's statement fails

    v = vault(tmp_path, flaky)
    clock = [0.0]
    v.now = lambda: clock[0]
    assert v.ready(durable=True) is not None  # down once
    clock[0] = vn.DB_RETRY_S - 1
    assert v.ready(durable=True) is not None and len(attempts) == 1  # skipped: no new connect, no re-arm
    clock[0] = vn.DB_RETRY_S + 1
    assert v.ready(durable=True) is None and len(attempts) == 2  # back after the backoff
    assert v.claim(400) and v.opened_before() is False


def test_a_release_inside_the_backoff_still_gives_the_claim_back(tmp_path):
    """Round-4 review P2: the second check failing starts the backoff; the release right after must not be
    skipped, or our own claim holds our next tries off for 20 ticks."""
    store = {}
    v = vault(tmp_path, lambda: FakeConn(store))
    assert v.claim(400)
    v._skip_until = v.now() + vn.DB_RETRY_S  # just failed
    v.release()
    assert store == {}


def test_a_process_gives_back_only_its_own_claim_and_renews_it():
    """Security review round 4, P2/P3: a release (or a save) never deletes another process's live claim, and
    our own claim never holds our next try off."""
    store = {}
    a = vault(Path("/nonexistent/a"), lambda: FakeConn(store))
    b = vault(Path("/nonexistent/b"), lambda: FakeConn(store))
    assert a.claim(400) and not b.claim(401)
    b.release()
    assert ("", "_claim") in store and store[("", "_claim")][0] == a.owner  # b cannot drop a's claim
    assert a.claim(410)  # ours: renewed, not blocked
    assert a.owner.startswith("claim-") and not a.owner.startswith(("bk_", "simbk-"))
