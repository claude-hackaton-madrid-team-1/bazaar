"""The activity watchdog: after the taker's sends, is ANY of our agents still doing something? (UB1)

Omar, Sat 3 Oct: "if in 30 s we have no movement or anything live, something is blocked and not working".
No settlement from tick 1099 to 1201 (~50 min) and nothing made it loud. This module makes it loud: it reads
the shared `decisions` table (Postgres only, never the game), calls the team STALLED when no agent sent
anything for `window` ticks, names the most frequent blocker, and labels the expected idle times (kill
switch, pause file, doors closed, a Market Test bench, a duel session) so they never read as a stall.

It only ever LOGS and RECORDS: one WARN line per stalled agent, one `activity_stall` decisions row per tick
(chosen false, never published), one learning per stall episode. It never trades, never relaxes or touches a
guardrail, never trips or resets a breaker.

What counts as activity (`is_activity`): a live row (`dry_run` false) whose status is `done` (the request went
out and the game took it; `Recorder.send`, agents/runtime.py) and whose kind is NOT bookkeeping
(`BOOKKEEPING_KINDS` and `BOOKKEEPING_PREFIXES`): an exclusion list, not an allow-list, so a send kind added
later counts by default. A `duel_hold` row that is `approved` counts too (holding inside a live duel is a
negotiation progressing), and so does the caller's flag that the taker holds an open dealer conversation.
Rows of agent `guard` (approval and breaker bookkeeping) are ignored.

The window (GUARDRAILS.md `activity_stall_seconds`): max(1, ceil(seconds / the clock's `tick_seconds`)) ticks,
1 tick at 30 s, 2 at 15 s. The current tick counts as well (the maker and the duels act later in the tick
than the taker's check, BAZAAR_TICK_OFFSET_S), so the team is stalled when its last activity is more than
`window` ticks old. Each rule is a pure function over plain rows; `ActivityWatch.tick` does the bounded reads
on a worker thread with a deadline (the watchdog's pattern) and swallows every error.
"""

from __future__ import annotations

import contextlib
import math
import re
import threading
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

import psycopg

from bazaar_agent.guardrails import Guardrails
from bazaar_agent.learn.model import Learning
from bazaar_agent.schedule_watch import crossing

STATEMENT_TIMEOUT_MS = 1000  # every query; a slow one fails and the tick goes on
WORKER_BUDGET_S = 2.0  # the longest the taker's tick waits for the read
READ_TICKS_MIN = 60  # the read window: at least this many ticks, so `stalled_for_ticks` can name a long stall
MAX_ROWS = 2000  # per read: bounded even if something floods the table
LEARN_EVERY_TICKS = 20  # a stall episode refreshes its learning at most this often
BENCH_TICKS_DEFAULT = 30  # a `bench.started` without its length runs at most this long
DUEL_ROW_FRESH_TICKS = 5  # a live duel row the runner has not refreshed for longer is not a session
TEXT_MAX = 160

Activity = Literal["ok", "stalled", "idle", "unknown"]
Row = Mapping[str, Any]

IGNORED_AGENTS = frozenset({"guard"})
# Rows that record what an agent thought or kept, not what it sent. Everything else that is `done` was sent.
BOOKKEEPING_KINDS = frozenset(
    {
        "process_started",
        "dealer_opened",
        "dealer_closed",
        "activity_stall",
        "ladder_probe",
        "strategy_gate",
        "dealer_skip",
        "pack_value",
        "approval_needed",
    }
)
BOOKKEEPING_PREFIXES = ("approval_", "breaker_", "hold_")
HOLD_ACTIVITY = frozenset({"duel_hold"})  # approved = still negotiating inside a live duel
PUBLIC_RULE = re.compile(r"^[a-z0-9_]{1,40}$")
RULE_IDS = tuple(sorted(Guardrails.model_fields, key=len, reverse=True))  # longest first: max_price_rare wins
_RULE_RE = re.compile(r"\b(" + "|".join(re.escape(r) for r in RULE_IDS) + r")\b")


def window_ticks(stall_seconds: float, tick_seconds: float) -> int:
    """Ticks in the stall window: 30 s is 1 tick at 30 s ticks and 2 at 15 s; never less than 1."""
    if tick_seconds <= 0 or not math.isfinite(tick_seconds) or stall_seconds <= 0:
        return 1
    return max(1, math.ceil(round(stall_seconds / tick_seconds, 6)))


def _int(value: object) -> int | None:
    return int(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


def _num(value: object) -> float | None:
    if isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value):
        return float(value)
    return None


def _map(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def is_bookkeeping(kind: str) -> bool:
    return kind in BOOKKEEPING_KINDS or kind.startswith(BOOKKEEPING_PREFIXES)


def is_activity(row: Row) -> bool:
    """A live send the game took (status `done`, not a bookkeeping kind), or an approved duel hold."""
    agent, kind, status = str(row.get("agent") or ""), str(row.get("kind") or ""), row.get("status")
    if not agent or agent in IGNORED_AGENTS or row.get("dry_run") is True:
        return False
    if status == "approved" and kind in HOLD_ACTIVITY:
        return True
    return status == "done" and not is_bookkeeping(kind)


def last_activity(rows: Iterable[Row]) -> dict[str, int]:
    """agent -> the tick of its last activity (rows may be `group by agent, kind, status` with `max(tick)`)."""
    out: dict[str, int] = {}
    for r in rows:
        tick = _int(r.get("tick"))
        if tick is not None and is_activity(r):
            agent = str(r["agent"])
            out[agent] = max(out.get(agent, tick), tick)
    return out


# ---------------------------------------------------------------- blockers


def _norm(text: str) -> str:
    """The watchdog's normalisation: digits collapsed, so 'cash 300 - 40' and 'cash 280 - 41' are one blocker."""
    return re.sub(r"\d+(?:\.\d+)?", "#", text)[:200]


def _text(row: Row) -> str:
    """Why the row did not go out: the guardrail's denial first, else the strategy's reason."""
    guardrail = str(_map(row.get("policy")).get("guardrail") or "")
    if guardrail.startswith("denied"):
        return guardrail.removeprefix("denied").lstrip(": ")
    if row.get("status") == "failed":
        return f"send failed ({row.get('kind')})"
    return str(row.get("reason") or row.get("kind") or "-")


def rule_of(text: str) -> str:
    """A coarse public category for one blocker: a GUARDRAILS.md rule id when the text names one, else a few
    known shapes; never a number, a card or a counterparty."""
    if m := _RULE_RE.search(text):
        return m.group(1)
    low = text.lower()
    checks = (
        (("jev",), "jev_undecided"),
        (("kill switch", "trading_enabled"), "kill_switch"),
        (("pause file",), "pause_file"),
        (("official value",), "official_value_margin"),
        (("breaker",), "breaker"),
        (("approval",), "human_approval_above"),
        (("cash",), "cash_floor"),
        (("send failed", "refused", "429", "rate"), "server_refusal"),
        (("cooloff", "quota", "locked"), "dealer_blocker"),
    )
    for needles, rule in checks:
        if any(n in low for n in needles):
            return rule
    return "other"


def _item(inputs: Mapping[str, Any]) -> str:
    for key in ("ref", "item", "wanted", "want_card", "card", "pack"):
        if isinstance(inputs.get(key), str) and inputs[key]:
            return str(inputs[key])[:32]
    return "-"


def _price(inputs: Mapping[str, Any]) -> float | None:
    for key in ("price", "ask", "total", "bid", "max"):
        if (p := _num(inputs.get(key))) is not None:
            return p
    return None


@dataclass(frozen=True)
class Blocker:
    rule: str  # coarse, the only part /state shows
    text: str  # an example of the refusal (private: logs, decisions, learnings)
    item: str
    price: float | None
    count: int

    def line(self) -> str:
        at = f" at {self.price:g}" if self.price is not None else ""
        return f"{self.rule} on {self.item}{at} ({self.count}x: {self.text[:TEXT_MAX]})"


def blockers(rows: Iterable[Row], since_tick: int, agent: str | None = None) -> list[Blocker]:
    """The rejected (or failed) rows at or after `since_tick` (of `agent`, or every agent), grouped by their
    normalised refusal, most frequent first; each with its newest example."""
    counts: Counter[str] = Counter()
    sample: dict[str, tuple[int, Row, str]] = {}
    for r in rows:
        tick, kind = _int(r.get("tick")), str(r.get("kind") or "")
        if tick is None or tick < since_tick or r.get("status") not in ("rejected", "failed"):
            continue
        if str(r.get("agent") or "") in IGNORED_AGENTS or kind == "activity_stall":
            continue
        if agent is not None and r.get("agent") != agent:
            continue
        text = _text(r)
        key = _norm(text)
        counts[key] += 1
        if key not in sample or tick >= sample[key][0]:
            sample[key] = (tick, r, text)
    out = []
    for key, n in counts.most_common():
        _, row, text = sample[key]
        inputs = _map(row.get("inputs"))
        out.append(Blocker(rule_of(text), text[:TEXT_MAX], _item(inputs), _price(inputs), n))
    return out


# ---------------------------------------------------------------- expected idle


def bench_running(rows: Iterable[Row], tick: int) -> bool:
    """A Market Test bench started (`bench.started` in the feed) and neither finished nor past its length."""
    started = finished = None
    for r in rows:
        rid = _int(r.get("id")) or 0
        if r.get("type") == "bench.started" and (started is None or rid > (_int(started.get("id")) or 0)):
            started = r
        elif r.get("type") == "bench.finished" and (finished is None or rid > finished):
            finished = rid
    if started is None or (finished is not None and finished > (_int(started.get("id")) or 0)):
        return False
    at = _int(started.get("tick"))
    length = _int(_map(started.get("payload")).get("ticks")) or BENCH_TICKS_DEFAULT
    return at is not None and at <= tick <= at + length


def duel_session_live(rows: Iterable[Row], tick: int) -> bool:
    """A duel of ours the duel runner still reads as live (refreshed lately) and whose deadline is ahead."""
    for r in rows:
        deadline, seen = _int(r.get("deadline_tick")), _int(r.get("tick"))
        if str(r.get("status") or "live") != "live" or deadline is None or seen is None:
            continue
        if deadline >= tick and tick - seen <= DUEL_ROW_FRESH_TICKS:
            return True
    return False


def idle_reason(
    *,
    stops: Sequence[str] = (),
    doors: str | None = "open",
    paused: bool = False,
    bench: bool = False,
    duels: bool = False,
    upcoming: Sequence[Mapping[str, Any]] = (),
    t_hours: float = 0.0,
    tick_seconds: float = 0.0,
    lead_ticks: int = 0,
) -> str | None:
    """Why no activity is expected right now (None: it is expected). A coarse label, safe to publish."""
    if any("pause file" in s for s in stops):
        return "pause_file"
    if stops:
        return "kill_switch"
    if doors not in (None, "open"):
        return "doors_closed"
    if paused:
        return "clock_paused"
    soon = crossing(upcoming, t_hours, tick_seconds, lead_ticks) if lead_ticks > 0 else None
    if bench or (soon is not None and soon.get("action") == "bench"):
        return "market_test"
    if duels or (soon is not None and soon.get("action") == "duels"):
        return "duel_session"
    return None


# ---------------------------------------------------------------- the report


@dataclass(frozen=True)
class AgentActivity:
    agent: str
    last_tick: int | None
    stalled_for_ticks: int | None  # ticks since its last activity; None: none in the read window
    blocker: Blocker | None


@dataclass(frozen=True)
class ActivityReport:
    tick: int
    window: int
    read_ticks: int
    activity: Activity
    stalled_for_ticks: int | None  # the team's: ticks since ANY agent's last activity
    idle_reason: str | None
    top_blocker: Blocker | None
    agents: tuple[AgentActivity, ...] = ()

    def public(self) -> dict[str, Any]:
        """What /health and /state carry: the state and a coarse rule id, never a price, card or limit."""
        rule = self.top_blocker.rule if self.top_blocker is not None else None
        return {
            "activity": self.activity,
            "stalled_for_ticks": self.stalled_for_ticks,
            "idle_reason": self.idle_reason,
            "top_blocker": rule if rule is None or PUBLIC_RULE.fullmatch(rule) else "other",
        }

    def summary(self) -> dict[str, Any]:
        """The private summary for the `activity_stall` decisions row and the learning."""
        return {
            **self.public(),
            "window_ticks": self.window,
            "top_blocker_text": self.top_blocker.line() if self.top_blocker is not None else None,
            "agents": {
                a.agent: {
                    "stalled_for_ticks": a.stalled_for_ticks,
                    "blocker": a.blocker.line() if a.blocker is not None else None,
                }
                for a in self.agents
            },
        }

    def warn_lines(self) -> list[str]:
        """One WARN line per stalled agent (the sim smoke fails on the word " refused ": never written)."""
        out = []
        for a in self.agents:
            ticks = f"{a.stalled_for_ticks} ticks" if a.stalled_for_ticks is not None else f"{self.read_ticks}+ ticks"
            why = a.blocker.line() if a.blocker is not None else "nothing rejected; check that it runs"
            out.append(safe_line(f"tick {self.tick} WARN activity: {a.agent} STALLED {ticks}: {why}"))
        return out


def safe_line(text: str) -> str:
    """One printable line without " refused " (scripts/sim_smoke.py fails a step on it)."""
    flat = " ".join("".join(ch if ch.isprintable() else " " for ch in text).split())
    return re.sub(r"refused", "blocked", flat, flags=re.IGNORECASE)


def assess(
    tick: int,
    window: int,
    read_ticks: int,
    decisions: Sequence[Row] | None,
    refusals: Sequence[Row] = (),
    *,
    taker_busy: bool = False,
    idle: str | None = None,
    agents: Iterable[str] = ("taker",),
) -> ActivityReport:
    """The verdict for one tick. `decisions` None: the read failed (activity `unknown` unless the taker holds a
    conversation or the idle is expected). `agents`: the agents always listed (others appear when seen)."""
    if decisions is None:
        state: Activity = "ok" if taker_busy else "idle" if idle else "unknown"
        return ActivityReport(tick, window, read_ticks, state, 0 if taker_busy else None, idle, None)
    last = {**last_activity(decisions), **({"taker": tick} if taker_busy else {})}
    seen = {str(r.get("agent")) for r in [*decisions, *refusals] if r.get("agent")} - IGNORED_AGENTS
    names = sorted({*agents, *seen} - {"", "None"})
    team_last = max(last.values(), default=None)
    team_for = tick - team_last if team_last is not None else None
    lo = tick - read_ticks + 1
    if team_for is not None and team_for <= window:
        return ActivityReport(tick, window, read_ticks, "ok", team_for, None, None)
    rows = []
    for name in names:
        at = last.get(name)
        found = blockers(refusals, lo if at is None else max(lo, at + 1), name)
        rows.append(AgentActivity(name, at, tick - at if at is not None else None, found[0] if found else None))
    top = blockers(refusals, lo if team_last is None else max(lo, team_last + 1))
    state = "idle" if idle else "stalled"
    return ActivityReport(tick, window, read_ticks, state, team_for, idle, top[0] if top else None, tuple(rows))


def learning_of(report: ActivityReport, since: int, us: str | None) -> Learning:
    """One learning per stall episode (the dedupe key holds the episode's first tick, so a refresh upserts)."""
    who = us if us and re.fullmatch(r"[A-Za-z0-9_.:\-]{1,64}", us) else "us"
    stalled = ", ".join(a.agent for a in report.agents) or "every agent"
    top = report.top_blocker.line() if report.top_blocker is not None else "no rejection recorded"
    ticks = report.stalled_for_ticks if report.stalled_for_ticks is not None else f"{report.read_ticks}+"
    return Learning(
        subject_kind="team",
        subject=who,
        kind="activity_stall",
        tick=max(0, report.tick),
        confidence=1.0,
        text=safe_line(f"no agent sent anything for {ticks} ticks ({stalled}); top blocker: {top}"),
        detail={"event_id": f"activity_stall:{since}", **report.summary()},
    )


# ---------------------------------------------------------------- the reads


@dataclass(frozen=True)
class Rows:
    decisions: Sequence[Row]  # one row per (agent, kind, status) with its newest tick
    refusals: Sequence[Row]  # rejected / failed rows, newest first
    bench: Sequence[Row]  # the newest bench.started / bench.finished feed events
    duels: Sequence[Row]  # live duel rows


def read_rows(conn: psycopg.Connection, tick: int, read_ticks: int) -> Rows:
    lo = tick - read_ticks
    decisions = [
        {"agent": r[0], "kind": r[1], "status": r[2], "tick": r[3], "dry_run": False}
        for r in conn.execute(
            "select agent, kind, status, max(tick) from decisions where tick > %s and tick <= %s "
            "and dry_run is false group by agent, kind, status limit %s",
            (lo, tick, MAX_ROWS),
        ).fetchall()
    ]
    refusals = [
        {"tick": r[0], "agent": r[1], "kind": r[2], "status": r[3], "inputs": r[4], "policy": r[5], "reason": r[6]}
        for r in conn.execute(
            "select tick, agent, kind, status, candidates, policy_checks, reason from decisions "
            "where tick > %s and tick <= %s and dry_run is false and status in ('rejected', 'failed') "
            "order by id desc limit %s",
            (lo, tick, MAX_ROWS),
        ).fetchall()
    ]
    bench = [
        {"id": r[0], "tick": r[1], "type": r[2], "payload": r[3]}
        for r in conn.execute(
            "select id, tick, type, payload from feed_events where type in ('bench.started', 'bench.finished') "
            "order by id desc limit 20"
        ).fetchall()
    ]
    duels = [
        {"status": r[0], "deadline_tick": r[1], "tick": r[2]}
        for r in conn.execute(
            "select status, deadline_tick, tick from duels where coalesce(status, 'live') = 'live' "
            "and deadline_tick >= %s and tick >= %s limit 200",
            (tick, tick - DUEL_ROW_FRESH_TICKS),
        ).fetchall()
    ]
    conn.commit()
    return Rows(decisions, refusals, bench, duels)


# ---------------------------------------------------------------- the taker's hook


class ActivityWatch:
    """The taker's activity check, after the watchdog. The read runs on a worker thread that the tick waits for
    at most `timeout_s`; the log line, the decisions row and the learning are written on the caller's thread
    (the stores are not shared with the worker). Never raises."""

    def __init__(
        self,
        connect: Callable[[], psycopg.Connection] | None,
        log: Callable[[str], None],
        *,
        decide: Callable[..., object] | None = None,
        record: Callable[[list[Learning]], object] | None = None,
        timeout_s: float = WORKER_BUDGET_S,
    ) -> None:
        self._connect, self.log, self.decide, self.record = connect, log, decide, record
        self._timeout_s = timeout_s
        self._conn: psycopg.Connection | None = None
        self._worker: threading.Thread | None = None
        self._result: Rows | None = None
        self.tick_seconds = 0.0  # the last pace the clock said
        self.report: ActivityReport | None = None
        self._episode: int | None = None  # the first tick of the current stall
        self._learned_at: int | None = None

    def tick(
        self,
        tick: int,
        rules: Guardrails,
        *,
        clock: Any = None,
        stops: Sequence[str] = (),
        upcoming: Sequence[Mapping[str, Any]] = (),
        taker_busy: bool = False,
        us: str | None = None,
    ) -> ActivityReport | None:
        try:
            if rules.activity_stall_seconds <= 0:
                self.report = None
                return None
            report = self._assess(tick, rules, clock, stops, upcoming, taker_busy)
            self.report = report
            self._emit(report, us)
            return report
        except Exception as e:  # noqa: BLE001 — the activity check must never cost the tick
            self.log(f"tick {tick} activity: check skipped ({type(e).__name__})")
            return self.report

    def _assess(
        self,
        tick: int,
        rules: Guardrails,
        clock: Any,
        stops: Sequence[str],
        upcoming: Sequence[Mapping[str, Any]],
        taker_busy: bool,
    ) -> ActivityReport:
        if clock is not None and (seconds := _num(getattr(clock, "tick_seconds", None))) and seconds > 0:
            self.tick_seconds = seconds
        window = window_ticks(rules.activity_stall_seconds, self.tick_seconds)
        read_ticks = max(window + 1, READ_TICKS_MIN)
        rows = self._read(tick, read_ticks)
        idle = idle_reason(
            stops=stops,
            doors=getattr(clock, "doors", "open") if clock is not None else "open",
            paused=bool(getattr(clock, "paused", False)) if clock is not None else False,
            bench=rows is not None and bench_running(rows.bench, tick),
            duels=rows is not None and duel_session_live(rows.duels, tick),
            upcoming=upcoming,
            t_hours=float(getattr(clock, "t_hours", 0.0) or 0.0) if clock is not None else 0.0,
            tick_seconds=self.tick_seconds,
            lead_ticks=rules.deploy_guard_bench_ticks,
        )
        return assess(
            tick,
            window,
            read_ticks,
            rows.decisions if rows is not None else None,
            rows.refusals if rows is not None else (),
            taker_busy=taker_busy,
            idle=idle,
        )

    def _read(self, tick: int, read_ticks: int) -> Rows | None:
        if self._connect is None:
            return None
        if self._worker is not None and self._worker.is_alive():
            self.log(f"tick {tick} activity: the last read is still running; skipped this tick")
            return None
        self._result = None
        worker = threading.Thread(target=self._work, args=(tick, read_ticks), name="activity", daemon=True)
        self._worker = worker
        worker.start()
        worker.join(self._timeout_s)
        if worker.is_alive():
            self.log(f"tick {tick} activity: no answer in {self._timeout_s:g} s; the tick goes on")
            return None
        return self._result

    def _work(self, tick: int, read_ticks: int) -> None:
        try:
            if self._conn is None or self._conn.closed:
                assert self._connect is not None
                conn = self._connect()
                conn.execute(f"set statement_timeout = {STATEMENT_TIMEOUT_MS}")
                conn.commit()
                self._conn = conn
            self._result = read_rows(self._conn, tick, read_ticks)
        except Exception as e:  # noqa: BLE001 — logged, retried next tick
            self.log(f"tick {tick} activity: Postgres read failed ({type(e).__name__}); activity unknown")
            broken, self._conn = self._conn, None
            if broken is not None:
                with contextlib.suppress(Exception):
                    broken.close()

    def _emit(self, report: ActivityReport, us: str | None) -> None:
        if report.activity != "stalled":
            self._episode = self._learned_at = None
            return
        start = self._episode = self._episode if self._episode is not None else report.tick
        for line in report.warn_lines():
            self.log(line)
        top = report.top_blocker.line() if report.top_blocker is not None else "no rejection recorded"
        line = safe_line(f"activity STALLED {report.stalled_for_ticks} ticks (window {report.window}): {top}")
        if self.decide is not None:
            with contextlib.suppress(Exception):
                self.decide(
                    report.tick,
                    "activity_stall",
                    line,
                    inputs=report.summary(),
                    reason=line,
                    guardrail="-",
                    chosen=False,
                    status="rejected",
                )
        due = self._learned_at is None or report.tick - self._learned_at >= LEARN_EVERY_TICKS
        if self.record is not None and due:
            with contextlib.suppress(Exception):
                self.record([learning_of(report, start, us)])
                self._learned_at = report.tick
