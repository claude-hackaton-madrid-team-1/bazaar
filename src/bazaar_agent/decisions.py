"""Every move the autonomous agents propose (`decisions`) and every request they send (`executions`).

A decision row holds the inputs, the strategy's reason, Jev's verdict with its floats, the guardrail
verdict, and whether the move was chosen. Dry runs write decisions too (`dry_run = true`), never
executions: nothing was sent. Postgres when it answers (the shared memory), else JSONL under
`<data_dir>/agents/`. Logging never breaks a tick: a failed write drops to the file and the loop goes on.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import os
import socket
import uuid
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

import psycopg

from bazaar_agent.telemetry import scrub

Status = Literal["approved", "rejected", "expired", "done", "failed"]
STATEMENT_TIMEOUT_MS = 3000


@dataclass(frozen=True)
class Decision:
    agent: str  # "taker" | "maker"
    tick: int
    kind: str  # accept_ask, dealer_open, dealer_bid, dealer_accept, dealer_walk, post_ask, post_bid, cancel, ...
    inputs: dict[str, Any]
    reason: str  # the strategy's reason for the move
    guardrail: str  # "allowed" or "denied: ..."
    chosen: bool
    status: Status
    dry_run: bool
    jev: dict[str, Any] | None = None  # verdict, value, probabilities, decided
    thread_id: int | None = None
    move: dict[str, Any] = field(default_factory=dict)  # what we send (or would send) when chosen

    def digest(self) -> str:
        return hashlib.sha256(json.dumps(self.inputs, sort_keys=True, default=str).encode()).hexdigest()[:16]


def scrubbed(value: object) -> object:
    """Every string scrubbed (secrets, key shapes, Jev masking) before it is stored; the shape is kept."""
    if isinstance(value, str):
        return scrub(value)
    if isinstance(value, dict):
        return {str(k): scrubbed(v) for k, v in value.items()}
    if isinstance(value, list | tuple | set | frozenset):
        return [scrubbed(v) for v in value]
    return value


def _json(value: object) -> str:
    return json.dumps(scrubbed(value), default=str, ensure_ascii=False)


class DecisionLog:
    """Writes decisions and executions to Postgres (`connect` lazily, reopened after a drop) or JSONL."""

    def __init__(
        self,
        data_dir: Path,
        connect: Callable[[], psycopg.Connection] | None = None,
        log: Callable[[str], None] = lambda message: None,
    ) -> None:
        self.dir = data_dir / "agents"
        self._connect, self._log = connect, log
        self._conn: psycopg.Connection | None = None
        self._down = False
        self._tick: int | None = None
        self._tried_tick: int | None = None
        self._local_ids = itertools.count(1)

    def begin_tick(self, tick: int) -> None:
        """While Postgres is down, try it again at most once per tick (a connect can take 10 s)."""
        self._tick = tick

    def writer(self) -> str:
        """This log's writer token (`writer`): one per Railway service, else one per data directory."""
        return writer(self.dir)

    @property
    def complete(self) -> bool:
        """The last read reached every store configured: no Postgres, or Postgres answered."""
        return self._connect is None or not self._down

    @property
    def where(self) -> str:
        return "postgres decisions/executions" if self._db() is not None else f"{self.dir}/*.jsonl"

    def _db(self) -> psycopg.Connection | None:
        if self._conn is not None and not self._conn.closed:
            return self._conn
        if self._connect is None or (self._down and self._tried_tick == self._tick):
            return None
        self._tried_tick = self._tick
        try:
            conn = self._connect()
            conn.autocommit = True
            conn.execute(f"set statement_timeout = {STATEMENT_TIMEOUT_MS}")
        except Exception as e:
            if not self._down:
                self._log(f"decisions: Postgres unavailable ({type(e).__name__}), writing {self.dir}/*.jsonl")
            self._down = True
            return None
        if self._down:
            self._log("decisions: Postgres back")
        self._conn, self._down = conn, False
        return conn

    def _failed(self, what: str, error: Exception) -> None:
        self._log(f"decisions: {what} failed in Postgres ({type(error).__name__}), JSONL instead")
        if self._conn is not None:
            self._conn.close()
        self._conn, self._down, self._tried_tick = None, True, self._tick

    def _append(self, name: str, row: dict[str, Any]) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        with (self.dir / name).open("a", encoding="utf-8") as handle:
            handle.write(_json(row) + "\n")

    def decide(self, d: Decision) -> int:
        """The decision's id: a `decisions.id` in Postgres, or a negative local id in the JSONL file."""
        conn = self._db()
        policy = {"guardrail": d.guardrail, "allowed": d.guardrail == "allowed"}
        if conn is not None:
            try:
                row = conn.execute(
                    "insert into decisions (thread_id, tick, state_digest, candidates, jev, policy_checks, chosen, "
                    "status, reason, agent, kind, dry_run) values (%s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb, "
                    "%s::jsonb, %s, %s, %s, %s, %s) returning id",
                    (
                        d.thread_id,
                        d.tick,
                        d.digest(),
                        _json(d.inputs),
                        _json(d.jev) if d.jev is not None else None,
                        _json(policy),
                        _json(d.move) if d.chosen else None,
                        d.status,
                        scrubbed(d.reason),
                        d.agent,
                        d.kind,
                        d.dry_run,
                    ),
                ).fetchone()
                if row is not None:
                    return int(row[0])
            except psycopg.Error as e:
                self._failed("decision insert", e)
        local = -next(self._local_ids)
        self._append("decisions.jsonl", {"id": local, **asdict(d), "digest": d.digest()})
        return local

    def settle(self, decision_id: int, status: Status) -> None:
        """The decision's final status once its request came back (done / failed / expired)."""
        conn = self._db() if decision_id > 0 else None
        if conn is not None:
            try:
                conn.execute("update decisions set status = %s where id = %s", (status, decision_id))
                return
            except psycopg.Error as e:
                self._failed("decision update", e)
        self._append("decisions.jsonl", {"id": decision_id, "status": status, "update": True})

    def executed(
        self,
        decision_id: int,
        tick: int,
        method: str,
        request: dict[str, Any],
        response: dict[str, Any] | None,
        error_code: str | None = None,
    ) -> None:
        """One request we actually sent (live only), with the server's answer or refusal code."""
        conn = self._db() if decision_id > 0 else None
        if conn is not None:
            try:
                conn.execute(
                    "insert into executions (decision_id, tick, sdk_method, request, response, error_code) "
                    "values (%s, %s, %s, %s::jsonb, %s::jsonb, %s)",
                    (decision_id, tick, method, _json(request), _json(response), error_code),
                )
                return
            except psycopg.Error as e:
                self._failed("execution insert", e)
        row = {"decision_id": decision_id, "tick": tick, "sdk_method": method, "request": request}
        self._append("executions.jsonl", {**row, "response": response, "error_code": error_code})

    def thread_trails(self, agent: str, since_tick: int, thread_ids: Iterable[int] = ()) -> dict[int, ThreadTrail]:
        """What this log remembers of `agent`'s live threads with a decision at or after `since_tick`, and of
        `thread_ids` whatever their age (the threads still open): the memory a restarted process has of the
        threads the one before it drove. Postgres and this machine's JSONL are both read (a write falls back to
        the file while Postgres is down). Only dealer threads (`DEALER_KINDS` rows): a swap thread with another
        team (`team_*` rows) is the team desk's, booked by it. Raises nothing: an unreadable store remembers
        nothing (`complete`)."""
        ids = sorted({int(i) for i in thread_ids})
        rows: list[tuple[Any, ...]] = []
        conn = self._db()
        if conn is not None:
            try:
                rows += conn.execute(
                    "select thread_id, tick, kind, candidates->>'item', chosen->>'price', candidates->>'owner' "
                    "from decisions where agent = %s and thread_id is not null and dry_run is not true "
                    "and starts_with(kind, %s) and (tick >= %s or thread_id = any(%s)) order by id",
                    (agent, DEALER_KINDS, since_tick, ids),
                ).fetchall()
            except psycopg.Error as e:
                self._failed("thread read", e)
        path = self.dir / "decisions.jsonl"
        lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
        for row in _live_rows(lines, agent):
            tick, tid = _int(row.get("tick")), row.get("thread_id")
            if not isinstance(tid, int) or tick is None or (tick < since_tick and tid not in ids):
                continue
            if not str(row.get("kind") or "").startswith(DEALER_KINDS):
                continue
            move = row.get("move") if row.get("chosen") else None
            price = move.get("price") if isinstance(move, dict) else None
            inputs = _inputs(row)
            rows.append((tid, tick, row.get("kind"), inputs.get("item"), price, inputs.get("owner")))
        trails: dict[int, ThreadTrail] = {}
        for thread_id, tick, kind, item, price, owner in rows:
            old = trails.get(int(thread_id), ThreadTrail(int(thread_id), "", int(tick)))
            prices = [p for p in (old.top_price, _int(price)) if p is not None]
            trails[int(thread_id)] = ThreadTrail(
                old.thread_id,
                str(item or old.item),
                max(old.last_tick, int(tick)),
                max(prices) if prices else None,
                old.closed or kind == THREAD_CLOSED,
                old.owner or (str(owner) if owner else None),
            )
        return trails

    def ask_rows(self, agent: str, since_tick: int) -> list[tuple[int, str, int, int | None]]:
        """`agent`'s live asks that went out (`post_ask`, status done) and its relist rests (RELIST_REST) since
        `since_tick`, oldest first, as (tick, kind, asset id, price or rest end): the per-copy relist history
        (`agents.relist`). Postgres and this machine's JSONL are both read; an unreadable store gives nothing."""
        found: list[tuple[int, int, str, Any, Any]] = []
        conn = self._db()
        if conn is not None:
            try:
                found += conn.execute(
                    "select id, tick, kind, candidates->>'asset_id', coalesce(candidates->>'until_tick', "
                    "candidates->>'price') from decisions where agent = %s and dry_run is not true and tick >= %s "
                    "and ((kind = 'post_ask' and status = 'done') or kind = %s) order by id",
                    (agent, since_tick, RELIST_REST),
                ).fetchall()
            except psycopg.Error as e:
                self._failed("ask read", e)
        path = self.dir / "decisions.jsonl"
        lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
        failed = _failed_ids(lines)
        for row in _live_rows(lines, agent):
            kind, tick, inputs = row.get("kind"), _int(row.get("tick")), _inputs(row)
            sent = kind == "post_ask" and row.get("chosen") and row.get("status") == "approved"
            if tick is None or tick < since_tick or row.get("id") in failed or not (sent or kind == RELIST_REST):
                continue
            value = inputs.get("until_tick") if kind == RELIST_REST else inputs.get("price")
            found.append((0, tick, str(kind), inputs.get("asset_id"), value))
        out = []
        for _, tick, kind, asset, value in sorted(found, key=lambda r: (r[1], r[0])):
            asset_id = _int(asset)
            if asset_id is not None:
                out.append((int(tick), str(kind), asset_id, _int(value)))
        return out

    def first_tick(self, agent: str, kind: str, owner: str) -> int | None:
        """The earliest tick of a live `kind` row by `agent` that `owner` wrote (`writer()`), in Postgres or this
        machine's JSONL (None: no row or an unreadable store)."""
        ticks: list[int] = []
        conn = self._db()
        if conn is not None:
            try:
                row = conn.execute(
                    "select min(tick) from decisions where agent = %s and kind = %s and dry_run is not true "
                    "and candidates->>'owner' = %s",
                    (agent, kind, owner),
                ).fetchone()
                if row is not None and row[0] is not None:
                    ticks.append(int(row[0]))
            except psycopg.Error as e:
                self._failed("first tick read", e)
        path = self.dir / "decisions.jsonl"
        lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
        for r in _live_rows(lines, agent):
            inputs = _inputs(r)
            if r.get("kind") == kind and inputs.get("owner") == owner and (t := _int(r.get("tick"))) is not None:
                ticks.append(t)
        return min(ticks) if ticks else None

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()


THREAD_CLOSED = "dealer_closed"  # the decision kind that wraps a thread up: its deal (if any) is booked
DEALER_KINDS = "dealer_"  # the prefix of every dealer-thread decision kind: the only threads a restart wraps up
PROCESS_STARTED = "process_started"  # a process that writes THREAD_CLOSED started: its threads' deals are known
RELIST_REST = "hold_relist"  # the maker stops listing one copy for a while (agents.relist); sends nothing


@dataclass(frozen=True)
class ThreadTrail:
    """One of an agent's threads as its decisions remember it (`DecisionLog.thread_trails`)."""

    thread_id: int
    item: str
    last_tick: int
    top_price: int | None = None  # the highest price we bid or accepted there
    closed: bool = False  # a THREAD_CLOSED row: the thread was wrapped up and its deal booked
    owner: str | None = None  # `writer()` of the process that opened it (None: opened before owners were written)


def writer(data_dir: Path | None = None) -> str:
    """Who writes these rows, as a short token the log keeps (it scrubs host names): the Railway service (its
    `RAILWAY_SERVICE_ID` is stable across redeploys), else an id saved once under `data_dir` (a laptop's host
    name changes with its network), else this machine's host name. Two live processes of one agent (Railway
    and a laptop) never take over each other's threads."""
    service = os.environ.get("RAILWAY_SERVICE_ID")
    if service:
        return "w" + hashlib.sha256(service.encode()).hexdigest()[:10]
    if data_dir is not None:
        path = data_dir / WRITER_FILE
        try:
            saved = path.read_text(encoding="utf-8").strip()
            if saved:
                return saved
        except OSError:
            pass
        token = "w" + uuid.uuid4().hex[:10]
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(token, encoding="utf-8")
            return token
        except OSError:
            pass
    return "w" + hashlib.sha256((socket.gethostname() or "local").encode()).hexdigest()[:10]


WRITER_FILE = "writer-id"  # under the decisions log's directory: this checkout's writer token


MAX_INT = 10_000_000  # RULES.md: prices are whole primas up to 10,000,000; ticks stay far below it


def _int(value: object) -> int | None:
    """A whole number from a log row, else None (bool, text, out of range)."""
    if isinstance(value, bool):
        return None
    try:
        n = int(value)  # type: ignore[call-overload]
    except (TypeError, ValueError, OverflowError):
        return None
    return n if 0 <= n <= MAX_INT else None


def _inputs(row: dict[str, Any]) -> dict[str, Any]:
    inputs = row.get("inputs")
    return inputs if isinstance(inputs, dict) else {}


def _failed_ids(lines: list[str]) -> set[int]:
    """Decision ids whose JSONL update row settled them as anything but done (a refused or lost send)."""
    out = set()
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if (
            isinstance(row, dict)
            and row.get("update")
            and row.get("status") != "done"
            and isinstance(row.get("id"), int)
        ):
            out.add(row["id"])
    return out


def _live_rows(lines: list[str], agent: str) -> list[dict[str, Any]]:
    """The JSONL rows `agent` wrote live: objects only, update and dry-run rows skipped, bad lines ignored."""
    rows = []
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and not row.get("update") and row.get("agent") == agent and not row.get("dry_run"):
            rows.append(row)
    return rows
