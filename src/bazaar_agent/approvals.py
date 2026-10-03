"""Human approvals for big trades: `human_approvals` in Postgres, shared by every process.

A card buy or sell priced at or above `human_approval_above` (GUARDRAILS.md) is refused by `guardrails.check()`
unless a human approved it first with `bazaar approve`: an approval covers one card and one side, up to a max
price (buy) or down to a min price (sell), until a tick. It is a pre-approval because a dealer's final lapses in
2 ticks: nobody can answer inside a tick. An approval never loosens another rule (the rarity caps, the official
value cap, the cash floor and the hourly spend cap still apply).

Reads fail CLOSED, unlike the breakers: `ApprovalBoard.read` reads once per tick per process on a worker thread
(`breakers.TickBoard`), and a failed or slow read answers None, which refuses every big trade. The first refusal per
(card, side) per game hour writes a `decisions` row (agent `guard`, kind `approval_needed`) and a WARN line, from a
background thread so a send never waits on it; `bazaar approvals` lists them.
"""

from __future__ import annotations

import contextlib
import json
import logging
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

import psycopg

from bazaar_agent.breakers import CONNECT_TIMEOUT_S, TickBoard

log = logging.getLogger(__name__)

Side = Literal["buy", "sell"]
SIDES: tuple[str, ...] = ("buy", "sell")
CARD = re.compile(r"^[A-Z]{3}-\d{2}$")
DEFAULT_TTL_TICKS = 240
PENDING_TICKS = 240  # "the last 2 game hours" at Saturday's 30 s ticks: how far back a request is still listed
UNREAD = "(approvals unreadable)"  # ends a refusal because the approvals could not be read: hold, never walk

DDL = (
    "create table if not exists human_approvals (card text not null, "
    "side text not null check (side in ('buy','sell')), "
    "max_price int, min_price int, until_tick int not null, by text, reason text, "
    "created_at timestamptz not null default now(), primary key (card, side))"
)
READ = "select card, side, max_price, min_price, until_tick from human_approvals where until_tick > %s"


class ApprovalError(ValueError):
    """A bad card, side or price: nothing was written."""


@dataclass(frozen=True)
class Approval:
    card: str
    side: str
    max_price: int | None
    min_price: int | None
    until_tick: int
    by: str = ""
    reason: str = ""
    created_at: str = ""

    def covers(self, price: float, tick: int) -> bool:
        if tick >= self.until_tick:
            return False
        if self.side == "buy":
            return self.max_price is not None and price <= self.max_price
        return self.min_price is not None and price >= self.min_price


@dataclass(frozen=True)
class ApprovalBook:
    """The active approvals read for one tick, by (card, side)."""

    approvals: dict[tuple[str, str], Approval] = field(default_factory=dict)

    def covers(self, card: str, side: str, price: float, tick: int) -> bool:
        found = self.approvals.get((card, side))
        return found is not None and found.covers(price, tick)


EMPTY = ApprovalBook()
NeedWrite = Callable[[dict[str, Any]], None]


class ApprovalBoard(TickBoard["ApprovalBook | None"]):
    """The active approvals, read once per tick; fail CLOSED (a failed read is None: no big trade)."""

    name = "approvals"
    fallback_note = "no trade at or above human_approval_above goes out (fail closed)"

    def __init__(self, *args: Any, write: NeedWrite | None = None, **kwargs: Any) -> None:
        kwargs.setdefault("notify", log.warning)
        super().__init__(*args, **kwargs)
        self._write = write or _write_in_background
        self._asked: set[tuple[str, str, int]] = set()
        self._asked_lock = threading.Lock()

    def fallback(self) -> ApprovalBook | None:
        return None

    def no_table(self) -> ApprovalBook | None:
        return EMPTY  # nobody ever approved anything on this database: readable, and empty

    def query(self, conn: psycopg.Connection, tick: int) -> ApprovalBook | None:
        out = conn.execute(READ, (tick,)).fetchall()
        return ApprovalBook(
            {(str(r[0]), str(r[1])): Approval(str(r[0]), str(r[1]), r[2], r[3], int(r[4])) for r in out}
        )

    def needed(self, inputs: dict[str, Any], hour: int) -> bool:
        """Say once per (card, side) per game hour that a trade waits for a human: a WARN line and a `decisions`
        row. True when this call said it."""
        key = (str(inputs["card"]), str(inputs["side"]), hour)
        with self._asked_lock:
            if key in self._asked:
                return False
            self._asked.add(key)
        log.warning(
            "APPROVAL NEEDED %s %s at %s (counterparty %s, official value %s, our value %s): "
            "`uv run bazaar approve %s --%s ...`",
            inputs["card"],
            inputs["side"],
            inputs["price"],
            inputs.get("counterparty"),
            inputs.get("official_value"),
            inputs.get("our_value"),
            inputs["card"],
            inputs["side"],
        )
        self._write({**inputs, "hour": hour})
        return True


_BOARD: dict[str, ApprovalBoard] = {}


def _default_connect() -> psycopg.Connection:
    from bazaar_agent import pgconn

    return pgconn.connect(app="bazaar-approvals", connect_timeout_s=CONNECT_TIMEOUT_S)


def board(timeout_s: float = 1.0) -> ApprovalBoard:
    """This process's board (one per process, DATABASE_URL). Tests install their own with `install`."""
    if "board" not in _BOARD:
        _BOARD["board"] = ApprovalBoard(_default_connect, timeout_s)
    return _BOARD["board"]


def install(b: ApprovalBoard) -> ApprovalBoard | None:
    """Replace this process's board; returns the old one (for a test to put back)."""
    old = _BOARD.get("board")
    _BOARD["board"] = b
    return old


def _write_in_background(inputs: dict[str, Any]) -> None:
    threading.Thread(target=_write_need, args=(inputs,), name="approvals-need", daemon=True).start()


def _write_need(inputs: dict[str, Any]) -> None:
    try:
        with _default_connect() as conn:
            conn.execute("set statement_timeout = 2000")
            record_need(conn, inputs)
    except Exception as e:  # noqa: BLE001 — best effort: the WARN line already said it
        log.warning("approvals: could not record the approval request (%s)", type(e).__name__)


def record_need(conn: psycopg.Connection, inputs: dict[str, Any]) -> bool:
    """One `approval_needed` row per (card, side, game hour) across processes. True when it was written."""
    from bazaar_agent.decisions import scrubbed

    reason = f"needs human approval: {inputs['card']} {inputs['side']} {inputs['price']}"
    row = conn.execute(
        "insert into decisions (tick, candidates, policy_checks, status, reason, agent, kind, dry_run) "
        "select %s, %s::jsonb, %s::jsonb, 'rejected', %s, 'guard', 'approval_needed', false where not exists ("
        "select 1 from decisions where agent = 'guard' and kind = 'approval_needed' and candidates->>'card' = %s "
        "and candidates->>'side' = %s and candidates->>'hour' = %s) returning id",
        (
            inputs.get("tick"),
            json.dumps(scrubbed(inputs), default=str),
            json.dumps({"human_approval": True}),
            reason,
            str(inputs["card"]),
            str(inputs["side"]),
            str(inputs["hour"]),
        ),
    ).fetchone()
    conn.commit()
    return row is not None


# ---------------------------------------------------------------- the CLI's writes


def known(card: str, side: str) -> tuple[str, str]:
    card = card.strip().upper()
    if not CARD.match(card):
        raise ApprovalError(f"not a card ref: {card!r} (e.g. LAV-09)")
    if side not in SIDES:
        raise ApprovalError(f"side must be buy or sell, not {side!r}")
    return card, side


def ensure_table(conn: psycopg.Connection) -> None:
    conn.execute(DDL)
    conn.commit()


def approve(
    conn: psycopg.Connection, card: str, side: str, price: int, until_tick: int, by: str, reason: str
) -> Approval:
    """Approve (or replace the approval of) `card` on `side`: up to `price` for a buy, down to it for a sell."""
    card, side = known(card, side)
    if price <= 0:
        raise ApprovalError("the price must be a positive number of primas")
    ensure_table(conn)
    max_price, min_price = (price, None) if side == "buy" else (None, price)
    conn.execute(
        "insert into human_approvals (card, side, max_price, min_price, until_tick, by, reason, created_at) "
        "values (%s, %s, %s, %s, %s, %s, %s, now()) on conflict (card, side) do update set "
        "max_price = excluded.max_price, min_price = excluded.min_price, until_tick = excluded.until_tick, "
        "by = excluded.by, reason = excluded.reason, created_at = now()",
        (card, side, max_price, min_price, until_tick, by[:100], reason[:500]),
    )
    conn.commit()
    return Approval(card, side, max_price, min_price, until_tick, by, reason)


def revoke(conn: psycopg.Connection, card: str, side: str) -> bool:
    """Remove the approval of `card` on `side`; True when there was one."""
    card, side = known(card, side)
    ensure_table(conn)
    row = conn.execute(
        "delete from human_approvals where card = %s and side = %s returning card", (card, side)
    ).fetchone()
    conn.commit()
    return row is not None


def active(conn: psycopg.Connection, tick: int) -> list[Approval]:
    ensure_table(conn)
    out = conn.execute(
        "select card, side, max_price, min_price, until_tick, coalesce(by, ''), coalesce(reason, ''), "
        "created_at::text from human_approvals where until_tick > %s order by card, side",
        (tick,),
    ).fetchall()
    conn.commit()
    return [Approval(str(r[0]), str(r[1]), r[2], r[3], int(r[4]), str(r[5]), str(r[6]), str(r[7])) for r in out]


def pending(conn: psycopg.Connection, since_tick: int) -> list[dict[str, Any]]:
    """The `approval_needed` rows since `since_tick`, newest first, one per (card, side)."""
    out = conn.execute(
        "select distinct on (candidates->>'card', candidates->>'side') tick, candidates from decisions "
        "where agent = 'guard' and kind = 'approval_needed' and tick >= %s "
        "order by candidates->>'card', candidates->>'side', tick desc",
        (since_tick,),
    ).fetchall()
    conn.commit()
    rows = [{**(r[1] or {}), "tick": r[0]} for r in out]
    return sorted(rows, key=lambda r: -(r["tick"] or 0))


def denials(conn: psycopg.Connection, since_tick: int) -> list[tuple[str, str, int]]:
    """(card, side, tick) of every `approval_denied` row since `since_tick`: a human said no to that request."""
    out = conn.execute(
        "select candidates->>'card', candidates->>'side', tick from decisions "
        "where agent = 'guard' and kind = 'approval_denied' and tick >= %s",
        (since_tick,),
    ).fetchall()
    conn.commit()
    return [(str(r[0]), str(r[1]), int(r[2])) for r in out if r[2] is not None]


def record(conn: psycopg.Connection, kind: str, tick: int, inputs: dict[str, Any]) -> None:
    """A `decisions` row (agent `guard`) for an approve or a revoke. Best effort: logged, never raised."""
    from bazaar_agent.decisions import scrubbed

    try:
        conn.execute(
            "insert into decisions (tick, candidates, policy_checks, status, reason, agent, kind, dry_run) "
            "values (%s, %s::jsonb, %s::jsonb, 'done', %s, 'guard', %s, false)",
            (
                tick,
                json.dumps(scrubbed(inputs), default=str),
                json.dumps({"human_approval": True}),
                scrubbed(str(inputs.get("reason") or kind)),
                kind,
            ),
        )
        conn.commit()
    except psycopg.Error as e:
        log.warning("approvals: could not record %s (%s)", kind, type(e).__name__)
        with contextlib.suppress(psycopg.Error):
            conn.rollback()
