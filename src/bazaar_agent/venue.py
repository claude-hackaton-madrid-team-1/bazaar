"""Our own market (RULES.md "Your own market", #11): open, close, re-fee and announce our venue, and the
broker key that comes with it. Build only until GUARDRAILS.md says `allow_venue_open = true`.

Every write here is a dry run unless the caller passes `live=True` (`--live`), and even then it goes
through `guardrails.check()` first: the kill switch and the pause file stop all of them,
`allow_venue_open = false` stops opening, fee changes and announcements, and the 250 P bond + 20 P
opening fee may never take cash below `cash_floor`.

The broker key is returned once, by the opening call. It is a secret like the team key: never printed,
never logged, saved only to `<data_dir>/broker.env` (mode 0600, read back by `config.load_settings` as
`BAZAAR_BROKER_KEY`), or set by hand as a Railway variable. A real broker key is only sent to the real
game, a simulator key (`simbk-...`, PR #55's `bazaar-sim`) only to another host.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field

from bazaar_agent.config import BROKER_ENV_FILE, REPO_ROOT, ConfigError, Settings
from bazaar_agent.guardrails import VENUE_COST, Action, Context, Guardrails, Verdict, check

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


def broker_client(settings: Settings) -> Any:
    """Our venue's broker connection (header X-Broker-Key), after the key/host guard."""
    from bazaar_agent.sdk import Broker

    key = settings.require_broker_key()
    check_broker_key_for_url(settings.bazaar_url, key)
    return Broker(settings.bazaar_url, key, retries=2)


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


# ---------------------------------------------------------------- one guarded write


@dataclass(frozen=True)
class VenueOutcome:
    sent: bool
    verdict: Verdict
    response: dict[str, Any] | None  # what the server answered, the broker key already removed
    message: str


def venue_context(rules: Guardrails, clock: dict[str, Any], cash: int = 0) -> Context:
    """What `check()` needs for a venue write: cash (for the bond), the tick, and the pause file."""
    return Context(
        cash=cash,
        held={},
        tick=int(clock.get("tick") or 0),
        t_hours=float(clock.get("t_hours") or 0.0),
        paused=(REPO_ROOT / rules.pause_file).exists(),
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


def open_venue(
    team: Any, spec: VenueSpec, rules: Guardrails, *, live: bool, settings: Settings
) -> tuple[VenueOutcome, Path | None]:
    """Open our venue (album first: /me for the cash the bond takes). Live and allowed, the broker key it
    returns is saved to `<data_dir>/broker.env` and removed from the outcome; it is never printed."""
    me, clock = team.me(), team.clock()
    ctx = venue_context(rules, clock, int(me.get("cash") or 0))
    describe = (
        f"open venue {spec.name!r} ({spec.mechanism}, {spec.fee_bps} bps + {spec.fee_per_card} P per card) "
        f"for {VENUE_COST} P (bond + opening fee) with cash {ctx.cash}"
    )

    def send() -> Any:
        check_key_file_writable(settings.data_dir)  # nothing is opened if the key would be lost
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
    try:
        path = save_broker_key(settings.data_dir, venue, key)
    except OSError as e:  # checked writable a moment ago; if it still fails, say so plainly (never the key)
        raise ConfigError(
            f"venue {venue} is OPEN but its broker key could not be saved ({type(e).__name__}). "
            f"Close it (`bazaar venue close {venue} --live`, the bond comes back) and open it again."
        ) from e
    return VenueOutcome(True, outcome.verdict, {**body, "broker_key": "[saved]"}, outcome.message), path


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
