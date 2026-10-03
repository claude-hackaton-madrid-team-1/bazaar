"""BROKER: every tick, read our venue's book and pair crossing offers (RULES.md "Your own market", #12).

Once per game tick (`ticks.run_per_tick`, never wall-clock; on Railway inside the maker's tick, see
`agents/venue_keeper.py`):
  1. `GET /api/broker/book` with the broker key: the venue's public offers (makers as pseudonyms) and,
     during the Market Test, `bench_offers`;
  2. our own open offers (`/api/me/offers`, team key, or the maker's read of them this tick) so none of
     them is ever matched; if they cannot be read, only the bench is matched that tick (fail closed);
     an offer we already matched is never proposed again;
  3. the exact maximum-surplus matching (`agents/matcher.py`), bench first, at most
     `max_matches_per_tick` matches;
  4. `guardrails.check()` on a `broker_match`: the kill switch, the pause file and `allow_venue_open`;
  5. each match logged to `decisions` (and, live, its request to `executions`), the tick window checked
     right before every send: a match that would land late is dropped, never sent late. Sends are paced
     (`pace_s`) so the broker stays inside the key budget beside the maker's own writes.

Dry run (the default) sends nothing: the decisions are written with `dry_run = true`. Live needs `--live`
(or BAZAAR_LIVE=1 on a service) AND `allow_venue_open = true` in GUARDRAILS.md.

Telemetry, per tick and per Market Test session: matches proposed / sent / refused, distinct maker pairs
(the organic score is pair-capped, so breadth wins) and the quoted surplus realised. A session is a bench
run ("b12"): it opens on `bench.started` or when its offers first show in the book, and closes on
`bench.finished` or when its offers are gone from the book.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from bazaar_agent import telemetry as tm
from bazaar_agent.agents.matcher import BrokerBook, Fee, Match, Quotes, plan_matches, quotes_from
from bazaar_agent.agents.runtime import Recorder, TickWindow, window_for
from bazaar_agent.agents.seller import offers_in
from bazaar_agent.config import REPO_ROOT
from bazaar_agent.decisions import DecisionLog, Status
from bazaar_agent.feed import Event
from bazaar_agent.guardrails import Action, Context, Guardrails, check, kill_switch
from bazaar_agent.sdk import BazaarError
from bazaar_agent.ticks import Clock


@dataclass(frozen=True)
class BrokerConfig:
    # The starter broker sends at most 10 public matches a tick; a bench run has 5 sellers. 15 sends stay
    # inside the key's burst of 20.
    max_matches_per_tick: int = 15
    pace_s: float = 0.0  # seconds between two sends (0.2 = 5 per second, the key's sustained rate)


def bench_run(value: object) -> str:
    """A bench run id as the offer ids carry it: 12, "12" and "b12" are all "b12"."""
    text = str(value)
    return text if text.startswith("b") else f"b{text}"


# ---------------------------------------------------------------- telemetry


@dataclass
class Session:
    run: str
    first_tick: int
    last_tick: int
    in_book: bool = False
    pairs: int = 0
    surplus: int = 0
    refused: int = 0
    matched: set[tuple[str, str]] = field(default_factory=set, repr=False)  # a dry run sees a pair every tick


@dataclass
class TickStats:
    tick: int
    live: bool
    proposed: int = 0
    proposed_surplus: int = 0
    sent: int = 0  # live: accepted by the server; dry run: would be sent (allowed, in time)
    refused: int = 0  # live: refused by the server
    denied: int = 0  # refused by the guardrails
    expired: int = 0  # the tick window closed first
    distinct_pairs: int = 0  # distinct maker pairs among this tick's public matches
    surplus_public: int = 0
    surplus_bench: int = 0
    pairs_so_far: int = 0  # distinct maker pairs since the broker started
    skipped: int = 0  # book rows the venue cannot cross (or malformed)
    ours: int = 0  # book offers dropped because they are ours


class BenchSessions:
    """Market Test sessions by bench run, from feed events when there are any, else from the book."""

    def __init__(self, on_close: Callable[[Session], None]) -> None:
        self.open: dict[str, Session] = {}
        self.closed: list[Session] = []
        self._on_close = on_close
        self._seen: set[int] = set()
        self._finished: set[str] = set()  # a run closed by its event never reopens from a late book read

    def _start(self, run: str, tick: int) -> Session | None:
        if run in self._finished:
            return None
        return self.open.setdefault(run, Session(run, tick, tick))

    def _finish(self, run: str, tick: int) -> None:
        self._finished.add(run)
        session = self.open.pop(run, None)
        if session is not None:
            session.last_tick = max(session.last_tick, tick)
            self.closed.append(session)
            self._on_close(session)

    def observe_events(self, events: Iterable[Event], tick: int) -> None:
        for e in events:
            if e.get("type") not in ("bench.started", "bench.finished") or e.get("id") in self._seen:
                continue
            self._seen.add(int(e.get("id") or 0))
            run = (e.get("payload") or {}).get("run")
            if run is None:
                continue
            if e["type"] == "bench.started":
                self._start(bench_run(run), int(e.get("tick") or tick))
            else:
                self._finish(bench_run(run), int(e.get("tick") or tick))

    def observe_book(self, runs: set[str], tick: int) -> None:
        for run in runs:
            if (session := self._start(run, tick)) is not None:
                session.in_book, session.last_tick = True, tick
        for run in [r for r, s in self.open.items() if s.in_book and r not in runs]:
            self._finish(run, tick)

    def record(self, m: Match, tick: int, refused: bool) -> None:
        session = self._start(m.sell.item.removeprefix("bench:"), tick)
        if session is None:
            return
        session.last_tick = tick
        if refused:
            session.refused += 1
        elif (key := (str(m.sell.id), str(m.buy.id))) not in session.matched:
            session.matched.add(key)
            session.pairs, session.surplus = session.pairs + 1, session.surplus + m.surplus


# ---------------------------------------------------------------- the agent


def broker_context(rules: Guardrails, clock: Clock) -> Context:
    """A match moves no cash of ours: only the tick and the kill switch matter to `check()`.

    The kill switch is read live (#68): a GUARDRAILS.md edit or the pause file counts on the next match."""
    return Context(
        cash=0,
        held={},
        tick=clock.tick,
        t_hours=clock.t_hours,
        paused=(REPO_ROOT / rules.pause_file).exists(),
        stops=kill_switch(rules),
    )


@dataclass
class _Run:
    clock: Clock
    window: TickWindow
    stats: TickStats
    pairs: set[frozenset[str]] = field(default_factory=set)
    sends: int = 0


class BrokerAgent:
    def __init__(
        self,
        broker: Any,
        team: Any,
        *,
        us: str | None,
        rules: Guardrails,
        decisions: DecisionLog,
        live: bool,
        log: Callable[[str], None],
        events: Callable[[], list[Event]] | None = None,
        stats_dir: Path | None = None,
        config: BrokerConfig | None = None,
        now: Callable[[], float] = time.monotonic,
        hub: Any = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.broker, self.team, self.us, self.rules = broker, team, us, rules
        self.live, self.log, self.events, self.now, self.sleep = live, log, events, now, sleep
        self.config = config or BrokerConfig()
        self.stats_dir = stats_dir
        self.rec = Recorder("broker", decisions, live, log, hub)
        self.sessions = BenchSessions(self._session_closed)
        self.pairs_seen: set[frozenset[str]] = set()
        self.done: set[str] = set()  # offer ids the venue accepted in a match: never proposed again
        self.history: list[TickStats] = []

    def on_tick(
        self,
        clock: Clock,
        *,
        window: TickWindow | None = None,
        our_offers: Any = None,
        events: list[Event] | None = None,
    ) -> None:
        """One pass. Inside the maker: its tick `window`, its read of `/api/me/offers` and of the feed."""
        window = window or window_for(clock, self.now(), self.now)
        self.rec.decisions.begin_tick(clock.tick)
        if not window.open():  # nothing could be sent in time: not even the read
            return
        try:
            book = BrokerBook.model_validate(self.broker.book())
        except BazaarError as e:
            self.log(f"tick {clock.tick} broker: book refused {e.code} ({e.message[:80]}); nothing matched")
            return
        except ValidationError as e:
            self.log(f"tick {clock.tick} broker: book unreadable ({e.error_count()} problem(s)); nothing matched")
            return
        self._observe_feed(clock.tick, events)
        ours_ok, our_ids = self._our_offer_ids(clock.tick, our_offers)
        found = quotes_from(book, our_ids, public=ours_ok)
        fresh = [q for q in found.quotes if str(q.id) not in self.done]
        quotes = Quotes(fresh, found.skipped, found.ours)
        self.sessions.observe_book({q.item.removeprefix("bench:") for q in quotes.quotes if q.bench}, clock.tick)
        plan = plan_matches(quotes.quotes, Fee(book.fee_bps, book.fee_per_card), self.config.max_matches_per_tick)
        stats = TickStats(clock.tick, self.live, skipped=quotes.skipped, ours=quotes.ours)
        run = _Run(clock, window, stats)
        for m in plan:
            self._match(run, m)
        self.pairs_seen |= run.pairs
        stats.distinct_pairs, stats.pairs_so_far = len(run.pairs), len(self.pairs_seen)
        self._tick_done(stats)

    def _observe_feed(self, tick: int, events: list[Event] | None = None) -> None:
        if events is not None:
            self.sessions.observe_events(events, tick)
            return
        if self.events is None:
            return
        try:
            self.sessions.observe_events(self.events(), tick)
        except Exception as e:  # the feed is a hint for session bounds; the book still drives matching
            self.log(f"tick {tick} broker: feed unavailable ({type(e).__name__}); sessions from the book")

    def _our_offer_ids(self, tick: int, preread: Any = None) -> tuple[bool, set[int]]:
        """Ids of our open offers. (False, ∅) when they cannot be read: public offers are then skipped."""
        if preread is None and self.team is None:
            return False, set()
        try:
            rows = offers_in(preread if preread is not None else self.team.my_offers())
        except BazaarError as e:
            self.log(f"tick {tick} broker: our offers unreadable ({e.code}); bench only this tick")
            return False, set()

        def ours(o: dict[str, Any]) -> bool:  # an offer another team addressed to us is not ours
            return not (self.us and o.get("to") == self.us and o.get("maker") != self.us)

        return True, {o["id"] for o in rows if isinstance(o.get("id"), int) and ours(o)}

    def _match(self, run: _Run, m: Match) -> None:
        tick, stats = run.clock.tick, run.stats
        stats.proposed += 1
        stats.proposed_surplus += m.surplus
        # checked per match, not per tick: a pause file touched mid-tick stops the very next send
        verdict = check(Action("broker_match"), broker_context(self.rules, run.clock), self.rules)
        status: Status = "rejected" if not verdict.allowed else "approved" if run.window.open() else "expired"
        chosen = status == "approved"
        what = "bench" if m.sell.bench else m.sell.item
        line = (
            f"match {what}: sell {m.sell.id} (ask {m.sell.price}) × buy {m.buy.id} (bid {m.buy.price}) "
            f"at {m.price} + fee {m.fee}, surplus {m.surplus}"
        )
        request = {"sell": m.sell.id, "buy": m.buy.id, "price": m.price}
        did = self.rec.decide(
            tick,
            "broker_match",
            line if chosen else f"skip {line}: {verdict if status == 'rejected' else 'tick window closed'}",
            inputs={
                "item": m.sell.item,
                "bench": m.sell.bench,
                "ask": m.sell.price,
                "bid": m.buy.price,
                "makers": sorted(m.makers),
                "fee": m.fee,
                "surplus": m.surplus,
                **request,
            },
            reason="maximum-surplus matching (exact), midpoint price",
            guardrail=str(verdict),
            chosen=chosen,
            status=status,
            move=request,
        )
        if status == "rejected":
            stats.denied += 1
            return
        if status == "expired":
            stats.expired += 1
            return
        if self.live and not run.window.open():  # logging took the last of the tick: drop it, never send late
            self.rec.decisions.settle(did, "expired")
            self.log(f"tick {tick} broker: DROPPED match {m.sell.id} × {m.buy.id}: tick window closed")
            stats.expired += 1
            return
        refused = False
        if self.live:
            if run.sends and self.config.pace_s > 0:
                self.sleep(self.config.pace_s)
            run.sends += 1
            refused = self.rec.send(did, tick, "broker_match", request, lambda: self.broker.match(**request)) is None
            if not refused:
                self.done |= {str(m.sell.id), str(m.buy.id)}
        if m.sell.bench:
            self.sessions.record(m, tick, refused)
        if refused:
            stats.refused += 1
            return
        stats.sent += 1
        if m.sell.bench:
            stats.surplus_bench += m.surplus
        else:
            stats.surplus_public += m.surplus
            run.pairs.add(m.makers)

    def _tick_done(self, stats: TickStats) -> None:
        self.history.append(stats)
        tm.event("broker.tick", asdict(stats))
        self._append("broker_ticks.jsonl", asdict(stats))
        if not stats.proposed:  # an empty book: the stats file has the row, the console stays quiet
            return
        verb = "matched" if self.live else "would match"
        self.log(
            f"tick {stats.tick} broker: {stats.proposed} proposed (surplus {stats.proposed_surplus}), "
            f"{stats.sent} {verb}, {stats.refused} refused, {stats.denied} denied, {stats.expired} dropped · "
            f"{stats.distinct_pairs} distinct pair(s), {stats.pairs_so_far} so far · surplus public "
            f"{stats.surplus_public} bench {stats.surplus_bench} · {'LIVE' if self.live else 'dry run'}"
        )

    def _session_closed(self, s: Session) -> None:
        row = {"run": s.run, "first_tick": s.first_tick, "last_tick": s.last_tick, "pairs": s.pairs}
        row |= {"surplus": s.surplus, "refused": s.refused, "live": self.live}
        tm.event("broker.bench_session", row)
        self._append("broker_sessions.jsonl", row)
        self.log(
            f"broker: Market Test {s.run} over (ticks {s.first_tick}–{s.last_tick}): {s.pairs} pair(s), "
            f"quoted surplus {s.surplus}, {s.refused} refused"
        )

    def _append(self, name: str, row: dict[str, Any]) -> None:
        if self.stats_dir is None:
            return
        try:
            self.stats_dir.mkdir(parents=True, exist_ok=True)
            with (self.stats_dir / name).open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row) + "\n")
        except OSError as e:  # telemetry never breaks a tick
            self.log(f"broker: could not write {name} ({type(e).__name__})")
