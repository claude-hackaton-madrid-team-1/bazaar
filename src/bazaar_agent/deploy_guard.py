"""Deploy guard: may we merge to main right now? A merge redeploys bazaar-duels (and taker, maker, MCP) on Railway.

An unanswered duel scores 0 for both sides, so a merge waits while a live duel of ours is near its deadline, while a
Market Test bench runs or is about to start, and while any other `/api/schedule` event is about to start
(GUARDRAILS.md "Live guard": `deploy_guard_duel_ticks`, `deploy_guard_bench_ticks`).

Payload shapes (vendored SDK + recorded fixtures under tests/fixtures/api/):
- `/api/clock`: `tick`, `tick_seconds`, `t_hours` (game hours played), `doors`, `paused`, `next_opens` (ISO wall time).
- `/api/duels` (live only): `{"duels": [{"duel", "status", "deadline_tick", ...}]}`.
- `/api/schedule`: `{"now_hours", "upcoming": [{"action", "at_hours", "note", "params"}]}`. `at_hours` is on the same
  game-hours axis as the clock's `t_hours`, NOT wall time: a start in ticks is `(at_hours - now_hours) * 3600 /
  tick_seconds` at today's pace (a pace change before the event moves it; the guard re-runs before every merge).
- A started bench is not guaranteed to stay in `upcoming` (the feed's `bench.started` is the only other trace), so a
  running bench is also inferred from the cadence: RULES.md "every two hours", or the spacing of the two first
  listed benches; the previous one started one period before the first listed one and lasts `params.ticks`.

Malformed or missing game data fails CLOSED (unsafe, "could not read ..."): this is a merge gate.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from typing import Any

import typer
from rich.console import Console
from rich.markup import escape

from bazaar_agent.agents.duelist import duel_done, duel_id
from bazaar_agent.guardrails import Guardrails
from bazaar_agent.ticks import _epoch

BENCH_EVERY_HOURS = 2.0  # RULES.md "The Market Test: every two hours"
READS = ("/api/clock", "/api/duels", "/api/schedule")


@dataclass(frozen=True)
class GuardVerdict:
    safe: bool
    reasons: tuple[str, ...]
    next_safe_tick: int | None  # the current tick when safe; None when the game could not be read
    tick: int | None = None
    window: str = ""  # the next safe window, for a human
    notes: tuple[str, ...] = ()  # assumptions the verdict rests on

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class _Block:
    start: int  # first unsafe tick
    end: int  # last unsafe tick
    reason: str


class _Unreadable(ValueError):
    pass


def _num(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _clock(clock: Any) -> tuple[int, float, float]:
    if not isinstance(clock, Mapping):
        raise _Unreadable("/api/clock (no data)")
    tick, tick_s, t_hours = _int(clock.get("tick")), _num(clock.get("tick_seconds")), _num(clock.get("t_hours"))
    if tick is None or tick_s is None or tick_s <= 0 or t_hours is None:
        raise _Unreadable("/api/clock (tick, tick_seconds or t_hours missing)")
    return tick, tick_s, t_hours


def _duel_blocks(duels: Any, tick: int, rules: Guardrails) -> list[_Block]:
    rows = duels.get("duels") if isinstance(duels, Mapping) else None
    if not isinstance(rows, list):
        raise _Unreadable("/api/duels (no duel list)")
    out = []
    for i, d in enumerate(rows):
        if not isinstance(d, Mapping):
            raise _Unreadable(f"/api/duels (row {i} is not an object)")
        if duel_done(d):
            continue
        deadline = _int(d.get("deadline_tick", d.get("deadline")))
        if deadline is None:
            raise _Unreadable(f"/api/duels (the deadline of duel {duel_id(d)})")
        reason = f"duel {duel_id(d)} is live with its deadline at tick {deadline} ({deadline - tick} ticks away)"
        out.append(_Block(deadline - rules.deploy_guard_duel_ticks, deadline, reason))
    return out


def _events(schedule: Any) -> tuple[float | None, list[tuple[str, float, Mapping[str, Any]]]]:
    upcoming = schedule.get("upcoming") if isinstance(schedule, Mapping) else None
    if not isinstance(upcoming, list):
        raise _Unreadable("/api/schedule (no upcoming list)")
    out = []
    for i, e in enumerate(upcoming):
        at = _num(e.get("at_hours")) if isinstance(e, Mapping) else None
        action = e.get("action") if isinstance(e, Mapping) else None
        if at is None or not isinstance(action, str):
            raise _Unreadable(f"/api/schedule (entry {i}: action or at_hours)")
        params = e.get("params")
        out.append((action, at, params if isinstance(params, Mapping) else {}))
    return _num(schedule.get("now_hours")), out


def _schedule_blocks(schedule: Any, tick: int, tick_s: float, t_hours: float, rules: Guardrails) -> list[_Block]:
    now_hours, events = _events(schedule)
    now_h = t_hours if now_hours is None else now_hours
    at_tick = lambda at: tick + round((at - now_h) * 3600.0 / tick_s)  # nearest tick: at_hours is rounded  # noqa: E731
    window, out = rules.deploy_guard_bench_ticks, []
    benches = sorted((at, p) for action, at, p in events if action == "bench")
    for action, at, params in events:
        start = at_tick(at)
        if action == "bench":
            length = _int(params.get("ticks"))
            if length is None:
                raise _Unreadable(f"/api/schedule (the length of the bench at {at} h)")
            when = "running" if start <= tick else f"starts at tick {start}"
            out.append(
                _Block(start - window, start + length, f"Market Test bench {when} (until tick {start + length})")
            )
        elif start >= tick:
            out.append(_Block(start - window, start, f"scheduled {action} starts at tick {start}"))
            first_deadline = _int(params.get("duel_ticks")) if action == "duels" else None
            if first_deadline is not None:  # its first duels close then: the duel rule, ahead of time
                end = start + first_deadline
                out.append(_Block(end - rules.deploy_guard_duel_ticks, end, f"first duels of {action} close at {end}"))
    if benches:
        first, params = benches[0]
        period = benches[1][0] - first if len(benches) > 1 else BENCH_EVERY_HOURS
        start, length = at_tick(first - period), _int(params.get("ticks"))
        if start <= tick and length is not None and period > 0:
            reason = f"Market Test bench running (inferred from the schedule's cadence) until tick {start + length}"
            out.append(_Block(start - window, start + length, reason))
    return out


def _closed(clock: Mapping[str, Any], tick: int, tick_s: float, rules: Guardrails, now: float) -> GuardVerdict:
    opens = _epoch(clock.get("next_opens"))
    if opens is None:
        raise _Unreadable("/api/clock (doors closed and next_opens missing)")
    margin_s = rules.deploy_guard_bench_ticks * tick_s
    if opens - now <= margin_s:
        reason = f"the doors open in {max(0.0, (opens - now) / 60):.1f} min (a redeploy must be done before then)"
        return GuardVerdict(False, (reason,), None, tick, "after the doors open: re-run the guard")
    left = (opens - now - margin_s) / 60
    window = f"doors closed: safe for about {left:.0f} more min (next opening {clock.get('next_opens')})"
    return GuardVerdict(True, (), tick, tick, window)


def _next_safe(blocks: list[_Block], tick: int) -> int:
    t = tick
    while hits := [b for b in blocks if b.start <= t <= b.end]:
        t = max(b.end for b in hits) + 1
    return t


def _window(blocks: list[_Block], tick: int, safe_from: int, tick_s: float) -> str:
    later = sorted((b for b in blocks if b.start > safe_from), key=lambda b: b.start)
    mins = (safe_from - tick) * tick_s / 60
    start = "now" if safe_from == tick else f"tick {safe_from} (~{mins:.0f} min)"
    if not later:
        return f"safe from {start}; nothing scheduled after it"
    until = later[0].start - 1
    span = (until - safe_from + 1) * tick_s / 60
    return f"safe from {start} until tick {until} (~{span:.0f} min; then {later[0].reason})"


def verdict(
    duels: Any,
    schedule: Any,
    clock: Any,
    rules: Guardrails,
    *,
    now: float | None = None,
    read_errors: Mapping[str, str] | None = None,
) -> GuardVerdict:
    """Pure: the three game payloads (None when a read failed) → safe to merge or not, and the next safe tick.

    `now` (epoch seconds, default the wall clock) only matters while the doors are closed."""
    errors = tuple(f"could not read {path} ({why})" for path, why in (read_errors or {}).items())
    if errors:
        return GuardVerdict(False, errors, None, notes=("a merge gate fails closed",))
    try:
        tick, tick_s, t_hours = _clock(clock)
        if clock.get("doors", "open") != "open":
            return _closed(clock, tick, tick_s, rules, time.time() if now is None else now)
        blocks = _duel_blocks(duels, tick, rules) + _schedule_blocks(schedule, tick, tick_s, t_hours, rules)
    except _Unreadable as e:
        return GuardVerdict(False, (f"could not read {e}",), None, notes=("a merge gate fails closed",))
    reasons = tuple(dict.fromkeys(b.reason for b in blocks if b.start <= tick <= b.end))
    safe_from = _next_safe(blocks, tick)
    notes = ("schedule times converted at today's pace", "re-run the guard right before the merge")
    return GuardVerdict(not reasons, reasons, safe_from, tick, _window(blocks, tick, safe_from, tick_s), notes)


def _reason(e: Exception) -> str:
    code, status = getattr(e, "code", None), getattr(e, "status", None)
    return f"{code} {status}" if code is not None else type(e).__name__  # never the message: no echo of a header


def run(client: Any, rules: Guardrails, *, now: float | None = None) -> GuardVerdict:
    """Exactly three keyed GETs (/api/clock, live /api/duels, /api/schedule); no writes. A failed read fails closed."""
    payloads: dict[str, Any] = {}
    errors: dict[str, str] = {}
    calls: dict[str, Callable[[], Any]] = {
        "/api/clock": client.clock,
        "/api/duels": client.duels,
        "/api/schedule": client.schedule,
    }
    for path in READS:
        try:
            payloads[path] = calls[path]()
        except Exception as e:  # noqa: BLE001 - any failure is "could not read": the gate stays shut
            errors[path] = _reason(e)
    return verdict(
        payloads.get("/api/duels"),
        payloads.get("/api/schedule"),
        payloads.get("/api/clock"),
        rules,
        now=now,
        read_errors=errors,
    )


def _client() -> Any:
    from bazaar_agent.config import load_settings
    from bazaar_agent.sdk import team_client

    return team_client(load_settings(), track=False)  # reads only: no holdings tracker, no database


def _load_rules() -> Guardrails:
    from bazaar_agent.guardrails import load_guardrails

    return load_guardrails().rules


def _print(v: GuardVerdict, console: Console) -> None:
    head = "[green]SAFE TO MERGE[/green]" if v.safe else "[red]DO NOT MERGE[/red]"
    console.print(f"{head} · tick {v.tick if v.tick is not None else '?'}")
    for reason in v.reasons:
        console.print(f"  - {escape(reason)}")
    if v.next_safe_tick is not None and not v.safe:
        console.print(f"  next safe tick: tick {v.next_safe_tick}")
    if v.window:
        console.print(f"  {escape(v.window)}")
    for note in v.notes:
        console.print(f"  [dim]{escape(note)}[/dim]")


def deploy_guard_cmd(json_out: bool = typer.Option(False, "--json", help="Print the verdict as JSON")) -> None:
    """Exit 0 when a merge to main (a Railway redeploy) is safe now, 1 when not: duel deadlines, the Market Test
    and scheduled events (GUARDRAILS.md "Live guard"). Three reads with our key, no writes."""
    err = Console(stderr=True)
    try:
        client, rules = _client(), _load_rules()
    except Exception as e:  # noqa: BLE001 - a missing key or a bad GUARDRAILS.md: the gate stays shut
        err.print(f"[red]DO NOT MERGE: could not start the guard ({escape(type(e).__name__)}: {escape(str(e))})[/red]")
        raise typer.Exit(1) from None
    v = run(client, rules)
    if json_out:
        typer.echo(json.dumps(v.as_dict()))
    else:
        _print(v, Console())
    raise typer.Exit(0 if v.safe else 1)
