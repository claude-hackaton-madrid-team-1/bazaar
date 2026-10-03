"""Our own market (RULES.md "Your own market", #11): open, close, re-fee and announce our venue, and the
broker key that comes with it. Build only until GUARDRAILS.md says `allow_venue_open = true`.

Every write here is a dry run unless the caller passes `live=True` (`--live`), and even then it goes
through `guardrails.check()` first: the kill switch and the pause file stop all of them,
`allow_venue_open = false` stops opening, fee changes and announcements, and the 250 P bond + 20 P
opening fee may never take cash below `cash_floor`.

The broker key is returned once, by the opening call. It is a secret like the team key: never printed,
logged, published or committed. `KeyVault` keeps it where the maker finds it again after a restart or a
Railway redeploy: the shared Postgres `venue_keys` table (the simulator has its own database), and
`<data_dir>/broker.env` (mode 0600, read back by `config.load_settings` as `BAZAAR_BROKER_KEY`). No public
route reads that table. A real broker key is only sent to the real game, a simulator key (`simbk-...`)
only to another host.
"""

from __future__ import annotations

import contextlib
import os
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from bazaar_agent.config import BROKER_ENV_FILE, REPO_ROOT, ConfigError, Settings, read_env_file
from bazaar_agent.guardrails import VENUE_COST, Action, Context, Guardrails, Verdict, check, runs_venue

GAME_HOST = "bazaar.causaprima.ai"
SIM_BROKER_PREFIX = "simbk-"
MAX_FEE_BPS = 1000  # 10 %
MAX_FEE_PER_CARD = 5
ANNOUNCE_MAX_CHARS = 280


class VenueSpec(BaseModel):
    """What `POST /api/venues` gets. A `board` venue by default: only there can our broker act (on `auto`
    the engine crosses every pair first, and matching like the free stall earns half the bench points)."""

    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=40)
    fee_bps: int = Field(default=0, ge=0, le=MAX_FEE_BPS)
    fee_per_card: int = Field(default=0, ge=0, le=MAX_FEE_PER_CARD)
    mechanism: Literal["board", "auto"] = "board"
    description: str = Field(default="", max_length=280)


class FeeSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fee_bps: int = Field(ge=0, le=MAX_FEE_BPS)
    fee_per_card: int | None = Field(default=None, ge=0, le=MAX_FEE_PER_CARD)


class Announcement(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=ANNOUNCE_MAX_CHARS)


# ---------------------------------------------------------------- the broker key


def is_game_host(url: str) -> bool:
    return (urlsplit(url).hostname or "").lower() == GAME_HOST


def check_broker_key_for_url(url: str, key: str) -> None:
    """A simulator broker key only to a simulator, the real one only to the real game (over https).
    Messages never carry the key."""
    simulated = key.startswith(SIM_BROKER_PREFIX)
    if is_game_host(url) and (simulated or urlsplit(url).scheme != "https"):
        raise ConfigError(f"the broker key is a simulator key or BAZAAR_URL is not https://{GAME_HOST}: not sent")
    if not is_game_host(url) and not simulated:
        host = urlsplit(url).hostname or url
        raise ConfigError(f"BAZAAR_URL ({host}) is not the real game: only a simulator broker key is sent there")


def broker_client(settings: Settings, key: SecretStr | None = None) -> Any:
    """Our venue's broker connection (header X-Broker-Key), after the key/host guard."""
    from bazaar_agent.sdk import Broker

    secret = key.get_secret_value() if key is not None else settings.require_broker_key()
    check_broker_key_for_url(settings.bazaar_url, secret)
    return Broker(settings.bazaar_url, secret, retries=2)


def save_broker_key(data_dir: Path, venue: str, key: str) -> Path:
    """Write `<data_dir>/broker.env` (0600, replaced atomically): the key a live open returned, once."""
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / BROKER_ENV_FILE
    # mkstemp: a new, uniquely named 0600 file (O_EXCL), so no symlink planted in a shared dir is followed
    fd, tmp = tempfile.mkstemp(dir=data_dir, prefix=f".{BROKER_ENV_FILE}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write("# Written by `bazaar venue open --live`. Secret: never commit, print or share.\n")
            handle.write(f"BAZAAR_VENUE={venue}\nBAZAAR_BROKER_KEY={key}\n")
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return path


def check_key_file_writable(data_dir: Path) -> None:
    """Before a live open: the key comes back only once, so prove it can be saved before asking for it."""
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        fd, probe = tempfile.mkstemp(dir=data_dir, prefix=f".{BROKER_ENV_FILE}.probe.")  # unique: follows no link
        os.close(fd)
        os.unlink(probe)
    except OSError as e:
        raise ConfigError(f"{data_dir} is not writable ({type(e).__name__}): the broker key could not be saved") from e


# ---------------------------------------------------------------- the key vault

# The same statement as in sql/schema.sql (a test keeps them equal): the vault creates its table itself, so
# a key is never lost to a database the schema was not applied to yet.
VENUE_KEYS_DDL = (
    "create table if not exists venue_keys (venue text primary key, broker_key text not null, "
    "opened_tick int, created_at timestamptz not null default now())"
)


@dataclass(frozen=True)
class StoredVenue:
    venue: str
    key: SecretStr  # shown as '**********' by repr and str
    where: str  # "postgres", "file" or "environment"


class KeyVault:
    """Where our broker key lives between restarts: the shared Postgres `venue_keys` table first (a Railway
    redeploy keeps it), then `<data_dir>/broker.env`, then BAZAAR_BROKER_KEY / BAZAAR_VENUE. Errors name
    only their type: a psycopg message could quote a parameter."""

    def __init__(
        self,
        data_dir: Path,
        connect: Callable[[], Any] | None = None,
        env_key: SecretStr | None = None,
        env_venue: str | None = None,
    ) -> None:
        self.data_dir, self._connect = data_dir, connect
        self._env = (env_key, env_venue)
        self._conn: Any = None

    @classmethod
    def from_settings(cls, settings: Settings, connect: Callable[[], Any] | None = None) -> KeyVault:
        return cls(settings.data_dir, connect, settings.broker_key, settings.venue_id)

    def _db(self) -> Any:
        if self._connect is None:
            raise ConfigError("no database configured for the broker key")
        if self._conn is None or self._conn.closed:
            self._conn = self._connect()
            self._conn.autocommit = True
            self._conn.execute(VENUE_KEYS_DDL)
        return self._conn

    def _drop(self) -> None:
        conn, self._conn = self._conn, None
        if conn is not None:
            with contextlib.suppress(Exception):  # already gone
                conn.close()

    def ready(self, *, durable: bool) -> str | None:
        """None when a key returned now can be saved (`durable`: in Postgres, which a redeploy keeps), else why not."""
        if durable:
            try:
                self._db().execute("select count(*) from venue_keys").fetchone()
            except Exception as e:
                self._drop()
                return f"Postgres cannot hold the broker key ({type(e).__name__})"
            return None
        try:
            check_key_file_writable(self.data_dir)
        except ConfigError as e:
            return str(e)
        return None

    def save(self, venue: str, key: str, tick: int) -> tuple[str, ...]:
        """Write the key everywhere it can go; where it went (empty: nowhere, say so without the key)."""
        saved: list[str] = []
        if self._connect is not None:
            try:
                self._db().execute(
                    "insert into venue_keys (venue, broker_key, opened_tick) values (%s, %s, %s) on conflict (venue) "
                    "do update set broker_key = excluded.broker_key, opened_tick = excluded.opened_tick",
                    (venue, key, tick),
                )
                saved.append("postgres")
            except Exception:
                self._drop()
        try:
            save_broker_key(self.data_dir, venue, key)
            saved.append("file")
        except OSError:
            pass
        return tuple(saved)

    def load(self, venue: str | None = None) -> StoredVenue | None:
        """The key for `venue` (or the newest one when None): Postgres, then the file, then the environment."""
        if self._connect is not None:
            try:
                row = (
                    self._db()
                    .execute(
                        "select venue, broker_key from venue_keys where %s::text is null or venue = %s "
                        "order by created_at desc limit 1",
                        (venue, venue),
                    )
                    .fetchone()
                )
                if row is not None:
                    return StoredVenue(str(row[0]), SecretStr(str(row[1])), "postgres")
            except Exception:
                self._drop()
        saved = read_env_file(self.data_dir / BROKER_ENV_FILE)
        if saved.get("BAZAAR_BROKER_KEY") and saved.get("BAZAAR_VENUE") and venue in (None, saved["BAZAAR_VENUE"]):
            return StoredVenue(saved["BAZAAR_VENUE"], SecretStr(saved["BAZAAR_BROKER_KEY"]), "file")
        key, env_venue = self._env
        if key is not None and key.get_secret_value() and (venue is None or env_venue in (None, venue)):
            return StoredVenue(str(env_venue or venue or ""), key, "environment")
        return None


# ---------------------------------------------------------------- one guarded write


@dataclass(frozen=True)
class VenueOutcome:
    sent: bool
    verdict: Verdict
    response: dict[str, Any] | None  # what the server answered, the broker key already removed
    message: str


def venue_context(rules: Guardrails, clock: dict[str, Any], cash: int = 0, me: dict[str, Any] | None = None) -> Context:
    """What `check()` needs for a venue write: cash (for the bond), the game hour, whether we already run
    a venue (/api/me), and the pause file."""
    return Context(
        cash=cash,
        held={},
        tick=int(clock.get("tick") or 0),
        t_hours=float(clock.get("t_hours") or 0.0),
        paused=(REPO_ROOT / rules.pause_file).exists(),
        has_venue=runs_venue(me or {}),
    )


def guarded_write(
    action: Action, ctx: Context, rules: Guardrails, *, live: bool, describe: str, call: Callable[[], Any]
) -> VenueOutcome:
    """Check, then send only when live and allowed. A refused or dry-run write never calls `call`."""
    verdict = check(action, ctx, rules)
    if not verdict.allowed:
        mode = "LIVE" if live else "dry run"
        return VenueOutcome(False, verdict, None, f"{mode}: guardrails refuse to {describe}: {verdict}")
    if not live:
        return VenueOutcome(False, verdict, None, f"dry run: would {describe}. Add --live to send it.")
    response = call()
    body = dict(response) if isinstance(response, dict) else {"result": response}
    return VenueOutcome(True, verdict, body, f"sent: {describe}")


@dataclass(frozen=True)
class Opened:
    """A venue we just opened. `saved` is where its key went: empty means only this process holds it."""

    venue: str
    key: SecretStr
    saved: tuple[str, ...]


def open_venue(
    team: Any,
    spec: VenueSpec,
    rules: Guardrails,
    *,
    live: bool,
    vault: KeyVault,
    durable: bool = False,
    me: dict[str, Any] | None = None,
    clock: dict[str, Any] | None = None,
) -> tuple[VenueOutcome, Opened | None]:
    """Open our venue (album first: /me for the cash the bond takes and the venue we may already run).

    Live and allowed, the vault must be able to hold the key BEFORE the request goes out (`durable`: in
    Postgres), and the key it returns is saved at once and removed from the outcome. It never raises after
    the open: a key that could not be saved is still returned in `Opened` (the caller keeps it in memory
    and says so, never showing it)."""
    me, clock = me if me is not None else team.me(), clock if clock is not None else team.clock()
    ctx = venue_context(rules, clock, int(me.get("cash") or 0), me)
    describe = (
        f"open venue {spec.name!r} ({spec.mechanism}, {spec.fee_bps} bps + {spec.fee_per_card} P per card) "
        f"for {VENUE_COST} P (bond + opening fee) with cash {ctx.cash}"
    )

    def send() -> Any:
        if (why := vault.ready(durable=durable)) is not None:  # nothing is opened if the key would be lost
            raise ConfigError(f"{why}: not opening")
        mechanism = {"mechanism": spec.mechanism}
        return team.open_venue(
            spec.name, spec.fee_bps, spec.fee_per_card, rules=mechanism, description=spec.description
        )

    outcome = guarded_write(
        Action("venue_open", spec.name, price=VENUE_COST), ctx, rules, live=live, describe=describe, call=send
    )
    if not outcome.sent or outcome.response is None:
        return outcome, None
    body = dict(outcome.response)
    key, venue = str(body.pop("broker_key", "") or ""), str(body.get("venue") or "")
    if not key or not venue:
        return VenueOutcome(True, outcome.verdict, body, f"{outcome.message}; no broker key came back"), None
    saved = vault.save(venue, key, ctx.tick)
    where = " + ".join(saved) if saved else "NOWHERE (kept in memory only)"
    message = f"{outcome.message}; venue {venue}, broker key saved to {where} (never shown)"
    return VenueOutcome(True, outcome.verdict, {**body, "broker_key": "[saved]"}, message), Opened(
        venue, SecretStr(key), saved
    )


def close_venue(team: Any, venue: str, rules: Guardrails, *, live: bool) -> VenueOutcome:
    ctx = venue_context(rules, team.clock())
    return guarded_write(
        Action("venue_close", venue),
        ctx,
        rules,
        live=live,
        describe=f"close venue {venue} (the bond comes back after a cooldown; a session counts the best venue open)",
        call=lambda: team.close_venue(venue),
    )


def set_fee(team: Any, venue: str, fee: FeeSpec, rules: Guardrails, *, live: bool) -> VenueOutcome:
    ctx = venue_context(rules, team.clock())
    per_card = "" if fee.fee_per_card is None else f" + {fee.fee_per_card} P per card"
    return guarded_write(
        Action("venue_fee", venue),
        ctx,
        rules,
        live=live,
        describe=f"announce fee {fee.fee_bps} bps{per_card} on {venue} (effective after the public notice)",
        call=lambda: team.set_fee(venue, fee.fee_bps, fee.fee_per_card),
    )


def announce(broker: Any, clock: dict[str, Any], note: Announcement, rules: Guardrails, *, live: bool) -> VenueOutcome:
    return guarded_write(
        Action("venue_announce"),
        venue_context(rules, clock),
        rules,
        live=live,
        describe=f"announce on our venue: {note.text!r}",
        call=lambda: broker.announce(note.text),
    )
