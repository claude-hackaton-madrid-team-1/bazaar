"""The live watchdog: after the taker's sends, read recent rows from Postgres (never the game) and trip breakers.

It only ever makes us MORE careful: it trips a circuit breaker (`breakers.trip_and_record`, source `watchdog`),
which never shortens or replaces an open-ended human trip, and it never resets one, never changes a price, a
cap or a limit. Evidence at or before a scope's last breaker change (a trip, or a human's reset) is not used
again, so a human reset is never undone by the same old rows. GUARDRAILS.md "Live guard" holds the thresholds.

Each rule is a pure function over plain rows (dicts), tested on its own; `run` does the reads (the last
`watchdog_window_ticks` ticks, live rows only) and the trips, and swallows every error. `Watchdog.tick` runs
`run` on a worker thread with a deadline, so a slow or black-holed database never holds the taker's tick.

Where each rule's fields are written (read them there before changing a rule):
- decisions (`decisions.DecisionLog.decide`, decisions.py:131-150): `candidates` = the inputs, `chosen` = the
  move (only when chosen), `policy_checks.guardrail`, `status` (approved, then done/failed by `Recorder.send`,
  agents/runtime.py:505-520), `dry_run`.
  - `dealer_bid` (agents/taker.py:1311-1342): inputs `thread`, `max`, `final_max`; move `price`.
  - `accept_ask` (taker.py:310-324, 1555): inputs `ref`, `total` (ask + fee), `value` (OUR model's value).
  - `dealer_accept` (taker.py:327-340, 1555): inputs `item`, `ask`, `value` (our model's value).
  - `accept_bid` (taker.py:246-258, 1646): inputs `ref`, `asset_id`, `surplus` (what the sale gains us).
  - `team_offer` (agents/team_desk.py:643) and its `say` execution (team_desk.py:669-674): request
    `thread_id`, `swap.give.cash` / `swap.want.cash`.
  - `team_accept` (taker.py:1730): inputs `thread`; what we paid is the ledger `accept` row `team:<thread>`
    (taker.py:1705 via `_slot`, price = cash out + fee).
  - `dealer_ask` (cli.py:977, `bazaar dealer sell`): inputs `dealer`, `asset`; move `price`.
  - `post_bid` / `post_ask` (agents/maker.py:523-534, 553): inputs `ref`, `price`, `to`.
  - `duel_offer` / `duel_accept` / `duel_hold` (cli.py:1241): move `duel`.
- settlements: `feed_events` type `settlement` (archived by the taker, agents/runtime.py:198): payload `items`
  (`id`, `kind`, `ref`, `frm`, `to`), `price`, `persona`, `parties`.
- our values: `me_snapshots.cards[].your_value` per asset (holdings.py:162-183, written each tick at :317):
  the server's own value of a copy we hold, the value the score counts.
- duels: the `duels` table (duel_store.py:24-63): `status` (live | deal | no_deal), `role`, `your_limit`,
  `price`, `days`, `deadline_tick`, `payload.rival_offer.price`.
"""

from __future__ import annotations

import contextlib
import re
import threading
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

import psycopg

from bazaar_agent import breakers
from bazaar_agent.guardrails import Guardrails

STATEMENT_TIMEOUT_MS = 1000  # every watchdog query; a slow one fails and the tick goes on
WORKER_BUDGET_S = 3.0  # the longest the taker's tick waits for the watchdog
MAX_ROWS = 20000  # per read: a bounded window even if something floods a table
STORM_EVERY_TICKS = 30  # one WARN per refusal storm per this many ticks
SAME_TRADE_TICKS = 2  # a settlement lands at most this many ticks after the decision that made it
SOURCE = "watchdog"

Level = Literal["trip", "critical", "warn"]
Row = Mapping[str, Any]


@dataclass(frozen=True)
class Finding:
    scope: str | None  # the breaker to trip (None: log only)
    reason: str
    at: int  # the tick of the evidence
    level: Level = "trip"
    until_tick: int | None = None  # a timed trip lapses by itself


@dataclass(frozen=True)
class Trade:
    """One settlement we are a party to, from our side."""

    sid: int
    tick: int
    kind: Literal["buy", "sell", "swap"]
    other: str
    persona: str | None
    price: int
    got: tuple[tuple[int, str, str], ...]  # (asset id, ref, kind) that came to us
    gave: tuple[tuple[int, str, str], ...]  # (asset id, ref, kind) that left us


def _int(value: object) -> int | None:
    return int(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


def _num(value: object) -> float | None:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


def _map(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _inputs(row: Row) -> Mapping[str, Any]:
    return _map(row.get("inputs"))


def _move(row: Row) -> Mapping[str, Any]:
    return _map(row.get("chosen"))


# ---------------------------------------------------------------- settlements as trades


def trades_of(settlements: Iterable[Row], us: str) -> list[Trade]:
    """Our settlements as trades: a buy (cards came to us), a sell (cards left us) or a swap (both)."""
    out = []
    for s in settlements:
        p = _map(s.get("payload"))
        items = [i for i in p.get("items") or [] if isinstance(i, Mapping) and _int(i.get("id")) is not None]
        got = tuple((int(i["id"]), str(i.get("ref")), str(i.get("kind"))) for i in items if i.get("to") == us)
        gave = tuple((int(i["id"]), str(i.get("ref")), str(i.get("kind"))) for i in items if i.get("frm") == us)
        if not got and not gave:
            continue
        others = [str(x) for x in p.get("parties") or [] if x != us]
        others += [str(i.get("frm") if i.get("to") == us else i.get("to")) for i in items]
        kind: Literal["buy", "sell", "swap"] = "swap" if got and gave else "buy" if got else "sell"
        tick = _int(p.get("tick")) or _int(s.get("tick")) or 0
        persona = p.get("persona") if isinstance(p.get("persona"), str) and p.get("persona") else None
        sid = _int(p.get("settlement")) or _int(s.get("id")) or 0
        out.append(Trade(sid, tick, kind, others[0] if others else "?", persona, _int(p.get("price")) or 0, got, gave))
    return out


def _value_after(snapshots: Sequence[Row], asset: int, tick: int) -> float | None:
    """The asset's value in the first snapshot at or after `tick` that holds it (what we just bought)."""
    for s in snapshots:
        if (_int(s.get("tick")) or 0) >= tick and (v := _asset_value(s, asset)) is not None:
            return v
    return None


def _value_before(snapshots: Sequence[Row], asset: int, tick: int) -> float | None:
    """The asset's value in the last snapshot at or before `tick` that still holds it (what we just sold)."""
    for s in reversed(snapshots):
        if (_int(s.get("tick")) or 0) <= tick and (v := _asset_value(s, asset)) is not None:
            return v
    return None


def _asset_value(snapshot: Row, asset: int) -> float | None:
    for c in snapshot.get("cards") or []:
        if isinstance(c, Mapping) and _int(c.get("asset")) == asset:
            return _num(c.get("your_value"))
    return None


def _done(decisions: Iterable[Row], kind: str) -> Sequence[Row]:
    return [d for d in decisions if d.get("kind") == kind and d.get("status") == "done"]


def _near(d: Row, tick: int) -> bool:
    return tick - SAME_TRADE_TICKS <= (_int(d.get("tick")) or -(10**9)) <= tick


def _buy_scope(t: Trade, decisions: Sequence[Row]) -> str:
    refs = {ref for _, ref, _ in t.got}
    if t.persona is not None:  # her offer we accepted (an `accept_buy`) or our bid she took (a `bid`)
        accepted = any(_inputs(d).get("item") in refs and _near(d, t.tick) for d in _done(decisions, "dealer_accept"))
        return "board_accept" if accepted else "dealer_buy"
    accepted = any(_inputs(d).get("ref") in refs and _near(d, t.tick) for d in _done(decisions, "accept_ask"))
    return "board_accept" if accepted else "maker_post"  # else a bid of ours was filled


def _sell_scope(t: Trade, decisions: Sequence[Row]) -> str:
    if t.persona is not None:
        return "dealer_sell"
    ids = {aid for aid, _, _ in t.gave}
    accepted = any(_int(_inputs(d).get("asset_id")) in ids for d in _done(decisions, "accept_bid"))
    return "board_accept" if accepted else "maker_post"  # else an ask of ours was filled


def bad_trades(
    trades: Iterable[Trade], snapshots: Sequence[Row], decisions: Sequence[Row], margin: float
) -> list[Finding]:
    """(a) A buy above the server's value of the copy we got (minus `official_value_margin`, as the buy cap), or
    a sell below the value of the copy we gave. Cards only; a trade with any value unknown is skipped."""
    out = []
    snaps = sorted(snapshots, key=lambda s: _int(s.get("tick")) or 0)
    for t in trades:
        items = t.got if t.kind == "buy" else t.gave if t.kind == "sell" else ()
        if not items or any(kind != "card" for _, _, kind in items):
            continue
        find = _value_after if t.kind == "buy" else _value_before
        values = [find(snaps, aid, t.tick) for aid, _, _ in items]
        if any(v is None for v in values):
            continue
        value = sum(v for v in values if v is not None)
        refs = "+".join(ref for _, ref, _ in items)
        if t.kind == "buy" and t.price > value - margin + 1e-9:
            reason = f"bought {refs} for {t.price} > its value {value:g} (settlement {t.sid}, tick {t.tick})"
            out.append(Finding(_buy_scope(t, decisions), reason, t.tick))
        elif t.kind == "sell" and t.price < value - 1e-9:
            reason = f"sold {refs} for {t.price} < its value {value:g} (settlement {t.sid}, tick {t.tick})"
            out.append(Finding(_sell_scope(t, decisions), reason, t.tick))
    return out


def decision_findings(decisions: Iterable[Row]) -> list[Finding]:
    """(a) What a sent decision row already says was wrong: a dealer bid above our own cap, an accept above OUR
    model's value (not the official value: these rows do not store it), a sell into a bid at a loss."""
    out = []
    for d in decisions:
        if d.get("status") != "done":
            continue
        kind, inputs, tick = d.get("kind"), _inputs(d), _int(d.get("tick")) or 0
        if kind == "dealer_bid":
            price, cap = _int(_move(d).get("price")), _int(inputs.get("max"))
            top = max(cap or 0, _int(inputs.get("final_max")) or 0)
            if price is not None and cap is not None and price > top:
                out.append(Finding("dealer_buy", f"bid {price} > our cap {top} on thread {inputs.get('thread')}", tick))
        elif kind in ("accept_ask", "dealer_accept"):
            cost = _num(inputs.get("total" if kind == "accept_ask" else "ask"))
            value = _num(inputs.get("value"))
            if cost is not None and value is not None and cost > value + 1e-9:
                what = inputs.get("ref") or inputs.get("item")
                out.append(Finding("board_accept", f"{kind} {what} for {cost:g} > our value {value:g}", tick))
        elif kind == "accept_bid":
            surplus = _num(inputs.get("surplus"))
            if surplus is not None and surplus < -1e-9:
                out.append(Finding("board_accept", f"sold {inputs.get('ref')} into a bid at {surplus:g}", tick))
    return out


# ---------------------------------------------------------------- (b) team swaps


def swap_cash(
    decisions: Sequence[Row], executions: Sequence[Row], ledger: Sequence[Row], trades: Iterable[Trade] = ()
) -> dict[int, tuple[int, int]]:
    """Cash we added to each SETTLED swap thread in the window, thread -> (tick, cash): our last offer posted there
    before a swap with that team settled (each new offer replaces the last; an offer that lapsed or was cancelled
    moved nothing and is not counted), or what we paid to take theirs."""
    team_of = {_int(d.get("id")): str(_inputs(d).get("team") or "") for d in decisions if d.get("kind") == "team_offer"}
    settled: dict[str, list[int]] = defaultdict(list)
    for t in trades:
        if t.kind == "swap":
            settled[t.other].append(t.tick)
    posted: dict[int, list[tuple[int, int]]] = defaultdict(list)
    team_of_thread: dict[int, str] = {}
    out: dict[int, tuple[int, int]] = {}

    def keep(thread: int | None, tick: int, cash: int) -> None:
        if thread is not None and cash > 0 and cash > out.get(thread, (0, 0))[1]:
            out[thread] = (tick, cash)

    for e in executions:
        request = _map(e.get("request"))
        did = _int(e.get("decision_id"))
        if did not in team_of or e.get("sdk_method") != "say" or e.get("error_code"):
            continue
        thread, give = _int(request.get("thread_id")), _map(_map(request.get("swap")).get("give"))
        if thread is not None and team_of[did]:
            posted[thread].append((_int(e.get("tick")) or 0, _int(give.get("cash")) or 0))
            team_of_thread[thread] = team_of[did]
    for team, ticks in settled.items():
        for at in ticks:  # one settled swap = one thread: the one with that team's newest offer before it
            last = [(max(o for o in offers if o[0] <= at), th) for th, offers in posted.items()
                    if team_of_thread[th] == team and any(o[0] <= at for o in offers)]  # fmt: skip
            if last:
                (_, cash), thread = max(last)
                keep(thread, at, cash)
    taken = {_int(_inputs(d).get("thread")) for d in _done(decisions, "team_accept")}
    for row in ledger:
        item = str(row.get("item") or "")
        thread = int(item[5:]) if item.startswith("team:") and item[5:].isdigit() else None
        if thread in taken:
            keep(thread, _int(row.get("tick")) or 0, _int(row.get("price")) or 0)
    return out


def swap_rules(
    trades: Iterable[Trade],
    snapshots: Sequence[Row],
    cash: Mapping[int, tuple[int, int]],
    rules: Guardrails,
    since: Mapping[str, int] | None = None,
    now: int = 0,
) -> list[Finding]:
    """(b) A swap that left us without a copy of the card we gave, more than `watchdog_max_swaps_per_team` swaps
    with one team, or more than `watchdog_swap_cash_per_hour` cash added to swaps: trip `team_swap`."""
    out = []
    since = since or {}
    snaps = sorted(snapshots, key=lambda s: _int(s.get("tick")) or 0)
    after_change = since.get("team_swap", -(10**9))  # evidence before the last trip or human reset is spent
    swaps = [t for t in trades if t.kind == "swap" and t.tick > after_change]
    cash = {k: v for k, v in cash.items() if v[0] > after_change}
    for t in swaps:
        after = next((s for s in snaps if (_int(s.get("tick")) or 0) >= t.tick), None)
        if after is None:
            continue  # no snapshot since: the next tick looks again
        held = Counter(str(c.get("ref")) for c in after.get("cards") or [] if isinstance(c, Mapping))
        for _, ref, kind in t.gave:
            if kind == "card" and held[ref] == 0:
                out.append(
                    Finding("team_swap", f"swap {t.sid} with {t.other} gave away our last copy of {ref}", t.tick)
                )
    per_team = Counter(t.other for t in swaps)
    for team, n in per_team.items():
        if n > rules.watchdog_max_swaps_per_team:
            last = max(t.tick for t in swaps if t.other == team)
            reason = f"{n} swaps with {team} in the window (max {rules.watchdog_max_swaps_per_team})"
            out.append(Finding("team_swap", reason, last, until_tick=max(now, last) + rules.watchdog_repeat_trip_ticks))
    total = sum(c for _, c in cash.values())
    if total > rules.watchdog_swap_cash_per_hour:
        last = max(t for t, _ in cash.values())
        reason = f"{total} cash added to team swaps in the window (max {rules.watchdog_swap_cash_per_hour})"
        out.append(Finding("team_swap", reason, last, until_tick=max(now, last) + rules.watchdog_repeat_trip_ticks))
    return out


# ---------------------------------------------------------------- (c) the same price again (Day-2 hint 5)


@dataclass(frozen=True)
class Send:
    scope: str
    key: str  # the counterparty or thread
    price: int
    tick: int
    order: int


def sends_of(decisions: Iterable[Row], executions: Iterable[Row]) -> list[Send]:
    """Every priced send to a dealer that went out, with its breaker scope and its thread (or dealer and asset)."""
    out = []
    rows = list(decisions)
    for d in rows:
        if d.get("status") != "done":
            continue
        kind, inputs, move = d.get("kind"), _inputs(d), _move(d)
        tick, order = _int(d.get("tick")) or 0, _int(d.get("id")) or 0
        if kind == "dealer_bid" and (price := _int(move.get("price"))) is not None:
            thread = _int(d.get("thread_id")) or _int(inputs.get("thread"))
            out.append(Send("dealer_buy", f"thread:{thread}", price, tick, order))
        elif kind == "dealer_ask" and (price := _int(move.get("price"))) is not None:
            out.append(Send("dealer_sell", f"{inputs.get('dealer')}:{inputs.get('asset')}", price, tick, order))
    # Dealers only: hint 5 is about a dealer hearing the same price again. A maker re-post at a steady target and a
    # cash-free swap step (net 0 every time) are not spam, and counting them tripped normal trading (#203 reviews).
    return out


def repeat_price_rule(sends: Iterable[Send], tick: int, rules: Guardrails, since: Mapping[str, int]) -> list[Finding]:
    """(c) The same price sent twice in a row to one counterparty or thread, counted per scope (sends after the
    scope's last breaker change only); more than `watchdog_repeat_price_max` trips the scope for
    `watchdog_repeat_trip_ticks` ticks."""
    by_key: dict[tuple[str, str], list[Send]] = defaultdict(list)
    for s in sends:
        if s.tick > since.get(s.scope, -(10**9)):
            by_key[(s.scope, s.key)].append(s)
    repeats: Counter[str] = Counter()
    last: dict[str, int] = {}
    for (scope, _), rows in by_key.items():
        rows.sort(key=lambda s: (s.tick, s.order))
        for a, b in zip(rows, rows[1:], strict=False):
            if a.price == b.price:
                repeats[scope] += 1
                last[scope] = max(last.get(scope, b.tick), b.tick)
    until = tick + rules.watchdog_repeat_trip_ticks
    return [
        Finding(scope, f"the same price sent twice in a row {n} times in the window (spam)", last[scope], "trip", until)
        for scope, n in sorted(repeats.items())
        if n > rules.watchdog_repeat_price_max
    ]


# ---------------------------------------------------------------- (d) duels


def duel_findings(duels: Iterable[Row], duel_moves: Iterable[Row], tick: int) -> list[Finding]:
    """(d) A price-only duel that closed outside our limit (seller at or below cost, buyer at or above value):
    trip `duel_accept`. A live duel at its last tick (deadline - 1) with an acceptable rival offer and no move of
    ours this tick: CRITICAL only (tripping would make it worse). Two-issue duels are skipped: the stored row has
    no value for the days, so the price alone cannot say."""
    moved = {
        _int(_move(m).get("duel")) for m in duel_moves if _int(m.get("tick")) == tick and m.get("kind") != "duel_hold"
    }
    out = []
    for d in duels:
        did, role, limit = _int(d.get("duel")), d.get("role"), _int(d.get("your_limit"))
        payload = _map(d.get("payload"))
        rival = _map(payload.get("rival_offer"))
        if (
            did is None
            or limit is None
            or role not in ("seller", "buyer")
            or _int(d.get("days"))
            or _int(rival.get("days"))
        ):
            continue
        price, at = _int(d.get("price")), _int(d.get("tick")) or 0
        if d.get("status") == "deal" and price is not None:
            if (role == "seller" and price <= limit) or (role == "buyer" and price >= limit):
                out.append(Finding("duel_accept", f"duel {did} closed at {price} outside our limit ({role})", at))
        elif d.get("status") == "live" and at == tick and _int(d.get("deadline_tick")) == tick + 1 and did not in moved:
            offer = _int(rival.get("price"))
            if offer is not None and ((role == "seller" and offer > limit) or (role == "buyer" and offer < limit)):
                reason = f"duel {did} ends next tick with an acceptable rival offer {offer} and no move of ours"
                out.append(Finding(None, reason, tick, "critical"))
    return out


# ---------------------------------------------------------------- (e) refusal storms


def _norm(text: str) -> str:
    return re.sub(r"\d+(?:\.\d+)?", "#", text)[:200]


def _item(inputs: Mapping[str, Any]) -> str:
    for key in ("ref", "item", "wanted", "want_card", "pack"):
        if isinstance(inputs.get(key), str):
            return str(inputs[key])
    return "-"


def _fix(text: str) -> str:
    low = text.lower()
    if "official value" in low:
        return "official value cap; drop it from the plan or price it under its value"
    if "cash" in low or "floor" in low:
        return "cash; stop planning buys until cash recovers"
    if "max_price" in low:
        return "rarity price cap; drop it from the plan"
    if "rate" in low or "429" in low:
        return "rate limit; send less per tick"
    return "find the rule that refuses it and drop it from the plan"


@dataclass(frozen=True)
class Storm:
    reason: str
    item: str
    count: int
    suggestion: str

    @property
    def key(self) -> tuple[str, str]:
        return _norm(self.reason), self.item

    def line(self) -> str:
        return f"{self.item} refused {self.count} times: {self.reason[:120]}; likely fix: {self.suggestion}"


def refusal_storms(decisions: Iterable[Row], refused: Iterable[Row], threshold: int) -> list[Storm]:
    """(e) One refusal (a guardrail denial or a server refusal code, numbers ignored) for one item more than
    `threshold` times in the window. WARN only, never a trip."""
    counts: Counter[tuple[str, str]] = Counter()
    sample: dict[tuple[str, str], str] = {}
    texts = []
    for d in decisions:
        policy = _map(d.get("policy"))
        text = str(policy.get("guardrail") or "")
        if d.get("status") == "rejected" and text.startswith("denied"):
            texts.append((text, _item(_inputs(d))))
    texts += [(f"refused {r.get('error_code')}", _item(_inputs(r))) for r in refused if r.get("error_code")]
    for text, item in texts:
        key = (_norm(text), item)
        counts[key] += 1
        sample.setdefault(key, text)
    return [Storm(sample[key], key[1], n, _fix(sample[key])) for key, n in sorted(counts.items()) if n > threshold]


@dataclass
class WatchdogState:
    """What survives between ticks in one process: when each storm was last logged."""

    warned: dict[tuple[str, str], int] = field(default_factory=dict)

    def should_warn(self, storm: Storm, tick: int) -> bool:
        last = self.warned.get(storm.key)
        if last is not None and tick - last < STORM_EVERY_TICKS:
            return False
        self.warned[storm.key] = tick
        return True


# ---------------------------------------------------------------- the reads and the trips


@dataclass(frozen=True)
class Window:
    decisions: Sequence[Row]
    executions: Sequence[Row]
    settlements: Sequence[Row]
    us: str | None
    snapshots: Sequence[Row]
    duels: Sequence[Row]
    ledger: Sequence[Row]
    since: dict[str, int]  # scope -> the tick of its last breaker change


def _rows(conn: psycopg.Connection, query: str, params: tuple[Any, ...]) -> list[tuple[Any, ...]]:
    return list(conn.execute(query, params).fetchall())


def read_window(conn: psycopg.Connection, tick: int, ticks: int) -> Window:
    lo = tick - ticks
    since: dict[str, int] = {}
    if conn.execute("select to_regclass('guard_breakers')").fetchone() not in (None, (None,)):
        since = {
            str(s): int(t) for s, t in _rows(conn, "select scope, tick from guard_breakers where tick is not null", ())
        }
    decisions = [
        {"id": r[0], "tick": r[1], "agent": r[2], "kind": r[3], "status": r[4], "chosen": r[5], "inputs": r[6],
         "policy": r[7], "thread_id": r[8]}
        for r in _rows(
            conn,
            "select id, tick, agent, kind, status, chosen, candidates, policy_checks, thread_id from decisions "
            "where tick > %s and tick <= %s and dry_run is false order by id desc limit %s",
            (lo, tick, MAX_ROWS),
        )
    ]  # fmt: skip
    executions = [
        {"decision_id": r[0], "tick": r[1], "sdk_method": r[2], "request": r[3], "error_code": r[4]}
        for r in _rows(
            conn,
            "select decision_id, tick, sdk_method, request, error_code from executions "
            "where tick > %s and tick <= %s order by id desc limit %s",
            (lo, tick, MAX_ROWS),
        )
    ]
    settlements = [
        {"id": r[0], "tick": r[1], "payload": r[2]}
        for r in _rows(
            conn,
            "select id, tick, payload from feed_events where type = 'settlement' and tick > %s and tick <= %s "
            "order by id desc limit %s",
            (lo, tick, MAX_ROWS),
        )
    ]
    me = conn.execute("select world, team from me_snapshots order by read_at desc limit 1").fetchone()
    snapshots = []
    if me is not None:
        snapshots = [
            {"tick": r[0], "cards": r[1] or []}
            for r in _rows(
                conn,
                "select tick, cards from me_snapshots where world = %s and team = %s and tick > %s and tick <= %s "
                "order by tick limit %s",
                (me[0], me[1], lo - 10, tick, MAX_ROWS),
            )
        ]
    duels = [
        {"duel": r[0], "tick": r[1], "status": r[2], "role": r[3], "your_limit": r[4], "price": r[5], "days": r[6],
         "deadline_tick": r[7], "payload": r[8]}
        for r in _rows(
            conn,
            "select duel, tick, status, role, your_limit, price, days, deadline_tick, payload from duels "
            "where tick > %s and tick <= %s order by duel limit %s",
            (lo, tick + 1, MAX_ROWS),
        )
    ]  # fmt: skip
    ledger = [
        {"tick": r[0], "price": r[1], "item": r[2]}
        for r in _rows(
            conn,
            "select tick, price, item from ledger where kind = 'accept' and item like 'team:%%' "
            "and tick > %s and tick <= %s limit %s",
            (lo, tick, MAX_ROWS),
        )
    ]
    conn.commit()
    return Window(decisions, executions, settlements, str(me[1]) if me else None, snapshots, duels, ledger, since)


def evaluate(w: Window, tick: int, rules: Guardrails) -> tuple[list[Finding], list[Storm]]:
    """Every rule over one window: the findings (trips and CRITICAL lines) and the refusal storms."""
    trades = trades_of(w.settlements, w.us) if w.us else []
    by_id = {_int(d.get("id")): d for d in w.decisions}
    refused = [
        {"error_code": e.get("error_code"), "kind": d.get("kind"), "inputs": d.get("inputs")}
        for e in w.executions
        if e.get("error_code") and (d := by_id.get(_int(e.get("decision_id")))) is not None
    ]
    duel_moves = [d for d in w.decisions if str(d.get("kind") or "").startswith("duel_")]
    findings = [
        *bad_trades(trades, w.snapshots, w.decisions, rules.official_value_margin),
        *decision_findings(w.decisions),
        *swap_rules(trades, w.snapshots, swap_cash(w.decisions, w.executions, w.ledger, trades), rules, w.since, tick),
        *repeat_price_rule(sends_of(w.decisions, w.executions), tick, rules, w.since),
        *duel_findings(w.duels, duel_moves, tick),
    ]
    fresh = [f for f in findings if f.scope is None or f.at > w.since.get(f.scope, -(10**9))]
    return fresh, refusal_storms(w.decisions, refused, rules.watchdog_refusal_storm)


def run(
    conn: psycopg.Connection,
    tick: int,
    rules: Guardrails,
    log: Callable[[str], None],
    state: WatchdogState | None = None,
) -> list[Finding]:
    """Read the window, apply every rule, trip what must be tripped, log the rest. Never raises: any error is
    logged, the transaction rolled back, and nothing more is done this tick."""
    state = state if state is not None else WatchdogState()
    try:
        findings, storms = evaluate(read_window(conn, tick, rules.watchdog_window_ticks), tick, rules)
        tripped: set[str] = set()
        for f in findings:
            if f.level == "critical":
                log(f"tick {tick} CRITICAL watchdog: {f.reason}")
            elif f.scope is not None and f.scope not in tripped:
                tripped.add(f.scope)  # one trip per scope per tick (the first reason)
                if breakers.trip_and_record(conn, f.scope, f.reason, tick, until_tick=f.until_tick, source=SOURCE):
                    log(f"tick {tick} watchdog: TRIPPED {f.scope}: {f.reason}")
        for s in storms:
            if state.should_warn(s, tick):
                log(f"tick {tick} WARN watchdog: {s.line()}")
        return findings
    except Exception as e:  # noqa: BLE001 — the watchdog must never break a tick
        log(f"tick {tick} watchdog: check failed ({type(e).__name__}); nothing tripped")
        with contextlib.suppress(Exception):
            conn.rollback()
        return []


class Watchdog:
    """The taker's watchdog: one Postgres connection (`connect`, opened lazily), `run` on a worker thread that
    the tick waits for at most `timeout_s`; a worker still running from an earlier tick is never doubled."""

    def __init__(
        self,
        connect: Callable[[], psycopg.Connection] | None,
        log: Callable[[str], None],
        timeout_s: float = WORKER_BUDGET_S,
    ) -> None:
        self._connect, self.log, self._timeout_s = connect, log, timeout_s
        self._conn: psycopg.Connection | None = None
        self._worker: threading.Thread | None = None
        self.state = WatchdogState()

    def tick(self, tick: int, rules: Guardrails) -> None:
        if self._connect is None:
            return
        if self._worker is not None and self._worker.is_alive():
            self.log(f"tick {tick} watchdog: the last check is still running; skipped this tick")
            return
        worker = threading.Thread(target=self._work, args=(tick, rules), name="watchdog", daemon=True)
        self._worker = worker
        worker.start()
        worker.join(self._timeout_s)
        if worker.is_alive():
            self.log(f"tick {tick} watchdog: no answer in {self._timeout_s:g} s; the tick goes on")

    def _work(self, tick: int, rules: Guardrails) -> None:
        try:
            if self._conn is None or self._conn.closed:
                assert self._connect is not None
                conn = self._connect()
                conn.execute(f"set statement_timeout = {STATEMENT_TIMEOUT_MS}")
                conn.commit()
                self._conn = conn
            run(self._conn, tick, rules, self.log, self.state)
        except Exception as e:  # noqa: BLE001 — a connect failure: logged, retried next tick
            self.log(f"tick {tick} watchdog: Postgres unavailable ({type(e).__name__}); nothing checked")
            broken, self._conn = self._conn, None
            if broken is not None:
                with contextlib.suppress(Exception):
                    broken.close()
