"""BROKER: every tick, read our venue's book and pair crossing offers (RULES.md "Your own market", #12).

Once per game tick (`ticks.run_per_tick`, never wall-clock; on Railway inside the maker's tick, see
`agents/venue_keeper.py`):
  1. `GET /api/broker/book` with the broker key: the venue's public offers (makers as pseudonyms) and,
     during the Market Test, `bench_offers`;
  2. our own open offers (`/api/me/offers`, team key, or the maker's read of them this tick) so none of
     them is ever matched; if they cannot be read, only the bench is matched that tick (fail closed);
     an offer we already matched is never proposed again;
  3. the exact maximum-surplus matching (`agents/matcher.py`), bench first, at most
     `max_matches_per_tick` matches; with `bench_policy = "edge"` (BAZAAR_BENCH_POLICY=edge, default exact) the
     bench pairs come from `agents/bench_edge.py` instead (the exact plan unless the edge beats it by a margin);
     with `bench_policy = "probe"` the exact plan goes out unchanged and then a few bench pairs whose quotes do
     not cross, priced between the quotes (`agents/bench_probe.py`): refused if the server checks quotes;
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
import math
import os
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Literal

from pydantic import ValidationError

from bazaar_agent import telemetry as tm
from bazaar_agent.agents.bench_capture import BenchBooks
from bazaar_agent.agents.bench_edge import DEFAULT_GUARD_MARGIN, BenchEdge, edge_plan, expiries_in
from bazaar_agent.agents.bench_model import PRIORS
from bazaar_agent.agents.bench_probe import BenchProbe
from bazaar_agent.agents.matcher import BrokerBook, Fee, Match, Quote, Quotes, plan_matches, quotes_from
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
    # How the Market Test bench is matched. "exact" (today): the maximum quoted-surplus matching, which pairs the
    # same traders as the free stall. "edge": `agents/bench_edge.py` (PR #84), the maximum *estimated true* surplus
    # from per-trader limit bands, sent only when it beats the exact plan by `bench_guard_margin` estimated primas
    # (-inf: whenever it has at least as many pairs, #84 as it was). "probe": the exact plan, then a few
    # non-crossing bench pairs priced between their quotes (`agents/bench_probe.py`).
    bench_policy: BenchPolicy = "exact"
    bench_guard_margin: float = DEFAULT_GUARD_MARGIN


BenchPolicy = Literal["exact", "edge", "probe"]
BENCH_POLICIES: dict[str, BenchPolicy] = {"exact": "exact", "edge": "edge", "probe": "probe"}
BENCH_POLICY_ENV = "BAZAAR_BENCH_POLICY"
BENCH_MARGIN_ENV = "BAZAAR_BENCH_GUARD_MARGIN"
UNGUARDED = ("none", "-inf", "-infinity")  # BAZAAR_BENCH_GUARD_MARGIN values for no guard at all
NOT_WIRED = ("BAZAAR_BENCH_CROSS", "BAZAAR_BENCH_PRESET")  # #84's other switches: limit probes and presets stay off


def bench_text(config: BrokerConfig) -> str:
    """How the broker matches the bench, for the keeper's lines: `exact`, `probe (exact + non-crossing probes)`,
    `edge (guard margin 10 P)` or `edge (unguarded, as #84)`."""
    if config.bench_policy == "probe":
        return "probe (exact + non-crossing probes)"
    if config.bench_policy != "edge":
        return "exact"
    margin = config.bench_guard_margin
    return "edge (unguarded, as #84)" if margin == float("-inf") else f"edge (guard margin {margin:g} P)"


def _margin(value: str) -> float | None:
    """A guard margin from the environment: a number (inf: the edge never fires), or none / -inf (no guard)."""
    if value in UNGUARDED:
        return float("-inf")
    try:
        margin = float(value)
    except ValueError:
        return None
    return None if math.isnan(margin) else margin


def bench_config_from_env(
    base: BrokerConfig, environ: Mapping[str, str] | None = None, log: Callable[[str], None] | None = None
) -> BrokerConfig:
    """The bench options of a broker with no command line (the maker's venue keeper on Railway), case-insensitive:
    BAZAAR_BENCH_POLICY (`exact`, `edge` or `probe`) and BAZAAR_BENCH_GUARD_MARGIN (estimated primas; `none` or
    `-inf`: no guard). Unset or empty: `base` unchanged. Any other value is IGNORED, loudly (its length only, never
    its text), and `base` stays: a typo must never stop the maker, which also posts our offers. #84's
    BAZAAR_BENCH_CROSS and BAZAAR_BENCH_PRESET are not wired here: set, they are reported as ignored."""
    env = os.environ if environ is None else environ
    say = log or (lambda line: None)
    config = base
    value = (env.get(BENCH_POLICY_ENV) or "").strip().lower()
    if value:
        policy = BENCH_POLICIES.get(value)
        if policy is None:
            say(
                f"broker: IGNORED {BENCH_POLICY_ENV} ({len(value)} chars; exact, edge or probe); "
                f"it stays {base.bench_policy}"
            )
        else:
            config = replace(config, bench_policy=policy)
    value = (env.get(BENCH_MARGIN_ENV) or "").strip().lower()
    if value:
        margin = _margin(value)
        if margin is None:
            say(
                f"broker: IGNORED {BENCH_MARGIN_ENV} ({len(value)} chars; a number, none or -inf); it stays "
                f"{base.bench_guard_margin:g}"
            )
        else:
            config = replace(config, bench_guard_margin=margin)
    for name in NOT_WIRED:
        if (env.get(name) or "").strip():
            say(f"broker: IGNORED {name}: not wired in this broker (quote-crossing pairs only, normal priors)")
    return config


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

    def record(self, m: Match, tick: int, refused: bool, surplus: int | None = None) -> None:
        session = self._start(m.sell.item.removeprefix("bench:"), tick)
        if session is None:
            return
        session.last_tick = tick
        if refused:
            session.refused += 1
        elif (key := (str(m.sell.id), str(m.buy.id))) not in session.matched:
            session.matched.add(key)
            session.pairs, session.surplus = (
                session.pairs + 1,
                session.surplus + (m.surplus if surplus is None else surplus),
            )


# ---------------------------------------------------------------- the agent


def broker_context(rules: Guardrails, clock: Clock) -> Context:
    """A match moves no cash of ours: only the tick and the pause file matter to `check()`."""
    return Context(
        cash=0,
        held={},
        tick=clock.tick,
        t_hours=clock.t_hours,
        paused=(REPO_ROOT / rules.pause_file).exists(),
        stops=kill_switch(rules),  # GUARDRAILS.md as it is now: an edit stops the next match, no restart
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
        books: BenchBooks | None = None,
    ) -> None:
        self.broker, self.team, self.us, self.rules = broker, team, us, rules
        self.live, self.log, self.events, self.now, self.sleep = live, log, events, now, sleep
        self.config = config or BrokerConfig()
        self.edge = BenchEdge(PRIORS["normal"])  # used only with bench_policy = "edge"
        self.edge_pairs: set[tuple[str, str]] = set()  # this tick's bench pairs that come from the edge itself
        self.probe = BenchProbe()  # used only with bench_policy = "probe"
        self.bench_runs: set[str] = set()  # "b87-": bench runs seen, to spot their rows in the book's settlements
        self.probe_pairs: set[tuple[str, str]] = set()  # this tick's bench pairs that are probes (quotes do not cross)
        self.stats_dir = stats_dir
        self.books = books if books is not None else BenchBooks(None, stats_dir, self.log)  # JSONL only
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
        if bench := sorted((q for q in found.quotes if q.bench), key=lambda q: str(q.id)):
            # the whole bench book, every tick: arrivals, quote drift and departures are only seen here
            self.log(f"tick {clock.tick} broker: bench book " + " ".join(f"{q.id}:{q.side[0]}{q.price}" for q in bench))
        if self.config.bench_policy == "probe":
            self._log_bench_settlements(clock.tick, book, found.quotes)
        plan = self._plan(quotes, Fee(book.fee_bps, book.fee_per_card), clock.tick, book)
        stats = TickStats(clock.tick, self.live, skipped=quotes.skipped, ours=quotes.ours)
        run = _Run(clock, window, stats)
        for m in plan:
            self._match(run, m)
        self.pairs_seen |= run.pairs
        self.books.record(clock.tick, book.bench_offers, book.fee_bps, book.fee_per_card)  # after the sends, no request
        stats.distinct_pairs, stats.pairs_so_far = len(run.pairs), len(self.pairs_seen)
        self._tick_done(stats)

    def _plan(self, quotes: Quotes, fee: Fee, tick: int, book: BrokerBook) -> list[Match]:
        """Today's exact matching of everything or, with `bench_policy = "edge"`, the edge's bench plan (the exact
        one unless the edge beats it by its guard margin) and the exact public matching with the slots left."""
        cap = self.config.max_matches_per_tick
        self.edge_pairs, self.probe_pairs = set(), set()
        if self.config.bench_policy == "probe":
            return self._with_probes(plan_matches(quotes.quotes, fee, cap), quotes, fee, tick, cap)
        if self.config.bench_policy != "edge":
            return plan_matches(quotes.quotes, fee, cap)
        bench = [q for q in quotes.quotes if q.bench]
        try:
            expiries = expiries_in(book.bench_offers, tick)
            picked = edge_plan(self.edge, bench, fee, tick, cap, expiries, self.config.bench_guard_margin)
        except Exception as e:  # never lose the tick to the edge: today's matching instead, and the models restart
            self.log(f"tick {tick} broker: bench edge failed ({type(e).__name__}); exact matching this tick")
            self.edge = BenchEdge(PRIORS["normal"])
            return plan_matches(quotes.quotes, fee, cap)
        if picked.edge and picked.matches:  # an empty bench with a margin <= 0 is no news
            self.edge_pairs = {(str(m.sell.id), str(m.buy.id)) for m in picked.matches}
            self.log(
                f"tick {tick} broker: bench edge over exact by {picked.gain:.1f} estimated P "
                f"({len(picked.matches)} pair(s))"
            )
        public = [q for q in quotes.quotes if not q.bench]
        return picked.matches + plan_matches(public, fee, cap - len(picked.matches))

    def _log_bench_settlements(self, tick: int, book: BrokerBook, found: list[Quote]) -> None:
        """The book's `settlements` rows that name a bench run we saw: whether a queued probe really settled (the
        POST only says `queued`) may show nowhere else before the session's score."""
        self.bench_runs |= {q.item.removeprefix("bench:") + "-" for q in found if q.bench}
        rows = (book.model_extra or {}).get("settlements") or []
        named = [json.dumps(r, sort_keys=True) for r in rows if any(run in json.dumps(r) for run in self.bench_runs)]
        if named:
            self.log(f"tick {tick} broker: bench settlements {len(named)}: " + " | ".join(named)[:600])

    def _with_probes(self, exact: list[Match], quotes: Quotes, fee: Fee, tick: int, cap: int) -> list[Match]:
        """The exact plan, untouched and first, then the probes in the slots left: a probe never displaces,
        delays or reprices an exact match, and a failing probe planner leaves the exact plan alone."""
        try:
            bench = [q for q in quotes.quotes if q.bench]
            for m, accepted in self.probe.resolve(bench):
                self.log(
                    f"tick {tick} broker: bench probe {m.sell.id}×{m.buy.id} at {m.price} "
                    + (
                        "GONE from the book (settled, or removed while queued)"
                        if accepted
                        else "DROPPED (traders back in the book)"
                    )
                    + f" · {self.probe.summary(m.sell.item)}"
                )
            probes = self.probe.plan(bench, exact, fee, cap - len(exact))
        except Exception as e:  # never lose the tick to the probe
            self.log(f"tick {tick} broker: bench probe failed ({type(e).__name__}); exact matching only")
            self.probe = BenchProbe(self.probe.config)
            return exact
        self.probe_pairs = {(str(m.sell.id), str(m.buy.id)) for m in probes}
        if probes:
            self.log(
                f"tick {tick} broker: bench probe {len(probes)} non-crossing pair(s) after {len(exact)} exact: "
                + ", ".join(f"{m.sell.id}@{m.sell.price}×{m.buy.id}@{m.buy.price} at {m.price}" for m in probes)
            )
        return exact + probes

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
        probe = (str(m.sell.id), str(m.buy.id)) in self.probe_pairs
        stats.proposed += 1
        stats.proposed_surplus += 0 if probe else m.surplus
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
            reason=(
                "bench edge: maximum estimated true surplus (limit bands from quotes), midpoint price"
                if (str(m.sell.id), str(m.buy.id)) in self.edge_pairs
                else (
                    "bench probe: quotes do not cross, price between them (accepted only if limits are checked)"
                    if probe
                    else "maximum-surplus matching (exact), midpoint price"
                )
            ),
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
            if not refused and not probe:  # a probe's traders come back if it is dropped at settlement
                self.done |= {str(m.sell.id), str(m.buy.id)}
            if probe and refused:
                code = self.rec.last_error.code if self.rec.last_error else None
                self.probe.record(m, False, code)
                self.log(
                    f"tick {tick} broker: bench probe {m.sell.id}×{m.buy.id} at {m.price} REFUSED {code} "
                    f"· {self.probe.summary(m.sell.item)}"
                )
            elif probe:
                self.probe.sent(m)
                self.log(f"tick {tick} broker: bench probe {m.sell.id}×{m.buy.id} at {m.price} QUEUED")
        if m.sell.bench:
            self.sessions.record(m, tick, refused, 0 if probe else m.surplus)
        if refused:
            stats.refused += 1
            return
        stats.sent += 1
        if m.sell.bench:
            stats.surplus_bench += 0 if probe else m.surplus
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
        self.edge.forget(s.run)
        if self.config.bench_policy == "probe":
            # the run's probe memory stays: its cap must hold even if its offers show again after this close
            self.log(f"broker: Market Test {s.run} {self.probe.summary(s.run)}; refusal codes {dict(self.probe.codes)}")
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
