"""What the agent runtime reads: one backend for the in-process SDK MCP server (the desk), the remote
MCP server (`bazaar mcp serve`), the hooks and the CLI.

Reads call the same functions as the CLI commands (intel, album, conversation, monitor, strategy);
writes live in `runtime.actions`. Clients, the ledger and the decision log are built lazily, once per
process. Nothing here prints: every capability returns a JSON-able dict, and `tools.safe_text` scrubs
it before a model or a remote client sees it. Nothing here imports the Agent SDK.
"""

from __future__ import annotations

import re
import threading
from collections.abc import Callable, Iterable
from dataclasses import asdict
from functools import partial
from pathlib import Path
from typing import Any

from bazaar_agent.agents.runtime import live_mode
from bazaar_agent.album import album_view
from bazaar_agent.config import REPO_ROOT, Settings
from bazaar_agent.decisions import DecisionLog
from bazaar_agent.feed import DEFAULT_WINDOW, Event, FeedStore, load_events
from bazaar_agent.guardrails import ENFORCED_BY, Context, Guardrails, LedgerStore, context_from, load_guardrails
from bazaar_agent.holdings import Holdings, MeRead
from bazaar_agent.llm.chooser import injection_flags
from bazaar_agent.ticks import Clock, action_budget_s

SOURCE = "runtime"  # the ledger `source` and the decisions `agent` prefix for every runtime tool call
MAX_ROWS = 40  # a tool answer goes into a model's context: long tables are cut, with the total kept

CATALOG_TICKS = 30  # the catalog changes when a set is released: re-read it at most every 30 ticks

Spawner = Callable[[list[str], Path], Any]  # argv, log file -> a handle with `.pid` and `.poll()`


def _detached(argv: list[str], log_path: Path) -> Any:
    """Start `argv` in its own session, stdout and stderr to `log_path`; never through a shell."""
    import subprocess

    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("ab") as out:
        return subprocess.Popen(argv, stdout=out, stderr=out, stdin=subprocess.DEVNULL, start_new_session=True)


class Backend:
    """Settings, guardrails and lazily built clients. `live` is BAZAAR_LIVE=1 in the process environment.

    A live backend, and the remote server (`server`), take the shared Postgres ledger or nothing: a
    machine-local JSONL ledger would stop counting the team's accepts, listings and spend together with
    the taker, maker and duels. Ledger rows a sent request could not write wait in `pending` (and in a
    local file) and are written first by the next write; until then no write is approved."""

    def __init__(
        self,
        settings: Settings,
        rules: Guardrails,
        *,
        live: bool | None = None,
        team: Any = None,
        public: Any = None,
        ledger: LedgerStore | None = None,
        decisions: DecisionLog | None = None,
        spawn: Spawner = _detached,
        server: bool = False,
        log: Callable[[str], None] = lambda message: None,
        holdings: Holdings | None = None,
    ) -> None:
        self.settings, self.rules, self.log, self.spawn = settings, rules, log, spawn
        self.live = live_mode(False) if live is None else live
        self.server = server  # the remote MCP server: no live dealer children, no steering saved
        self.shared_ledger_only = server or self.live
        self.pending: list[tuple[str, int, float, int, str]] = []  # ledger rows of sent requests, not yet written
        self._team, self._public, self._ledger, self._decisions = team, public, ledger, decisions
        # A client handed in (tests, an embedding caller) gets no shared database unless holdings come too.
        self._holdings, self._shared_holdings = holdings, team is None
        self._build = threading.Lock()
        # One write at a time: the ledger connection and the per-tick quotas are shared by every tool call.
        self.write_lock = threading.RLock()
        self._catalog: tuple[int, dict[str, Any]] | None = None
        self.dealer_runs: dict[str, Any] = {}  # dealer -> the live `dealer buy` child this process started
        self.duel_said: set[tuple[int, int]] = set()  # (duel, tick): one message per duel per tick

    @property
    def team(self) -> Any:
        """Our team client. Raises `ConfigError` (naming BAZAAR_KEY, never a value) when the key is missing."""
        with self._build:
            if self._team is None:
                from bazaar_agent.sdk import team_client

                self._team = team_client(self.settings)
            return self._team

    @property
    def public(self) -> Any:
        with self._build:
            if self._public is None:
                from bazaar_agent.sdk import public_client

                self._public = public_client(self.settings)
            return self._public

    @property
    def ledger(self) -> LedgerStore:
        """The shared Postgres ledger (reopened after a failure), else the JSONL file unless shared-only."""
        with self._build:
            if self._ledger is None:
                from bazaar_agent.ledger_pg import LedgerUnavailable, PgLedger, open_ledger

                opened = open_ledger(self.settings.data_dir, source=SOURCE, log=self.log)
                if self.shared_ledger_only and not isinstance(opened, PgLedger):
                    raise LedgerUnavailable("the shared Postgres ledger is unreachable")
                self._ledger = opened
            return self._ledger

    def book(self, entries: list[tuple[str, int, float, int, str]]) -> str | None:
        """Write ledger rows for a request that WAS sent; None when written. On a failure the rows wait in
        `pending` (and `runtime/pending-ledger.jsonl`), and the error is returned, never raised."""
        try:
            for kind, tick, t_hours, price, item in entries:
                self.ledger.record(kind, tick, t_hours, price, item)
        except Exception as e:
            self.failed(e)
            self.pending.extend(entries)
            self._keep_pending(entries)
            return f"{type(e).__name__}: the shared ledger did not record this send (kept, written first next time)"
        return None

    def _keep_pending(self, entries: list[tuple[str, int, float, int, str]]) -> None:
        import json as _json

        path = self.settings.data_dir / "runtime" / "pending-ledger.jsonl"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as out:
                for entry in entries:
                    out.write(_json.dumps(entry) + "\n")
        except OSError as e:
            self.log(f"runtime: pending ledger rows kept in memory only ({type(e).__name__})")

    def flush_pending(self) -> None:
        """Write the rows a sent request left behind before judging anything new. Raises while the
        ledger is still down: no write is approved on totals that miss our own sends."""
        while self.pending:
            kind, tick, t_hours, price, item = self.pending[0]
            try:
                self.ledger.record(kind, tick, t_hours, price, item)
            except Exception as e:
                self.failed(e)
                raise
            self.pending.pop(0)

    def failed(self, error: BaseException) -> None:
        """After a ledger failure, drop the connection: the next call reopens it (Postgres came back)."""
        from bazaar_agent.ledger_pg import LedgerUnavailable

        if isinstance(error, LedgerUnavailable):
            with self._build:
                self._ledger = None

    @property
    def decisions(self) -> DecisionLog:
        with self._build:
            if self._decisions is None:
                from bazaar_agent import db

                url = self.settings.database_url.get_secret_value()
                self._decisions = DecisionLog(
                    self.settings.data_dir, lambda: db.connect(url, app="bazaar-runtime"), self.log
                )
            return self._decisions

    @property
    def holdings(self) -> Holdings:
        """Album first, shared: /me from the Postgres snapshot while provably current, else live (stored)."""
        with self._build:
            if self._holdings is None:
                from bazaar_agent import holdings as hd
                from bazaar_agent.identity import remember_team_id, resolve_team_id

                team = resolve_team_id(self.settings.team_id, self.settings.data_dir, None)
                if self._shared_holdings:
                    hd.name_process("mcp" if self.server else "runtime")
                    remember = partial(remember_team_id, self.settings.data_dir)
                    self._holdings = hd.for_process(
                        lambda: self.team.me(), self.rules, self.settings, team=team, on_team=remember
                    )
                else:
                    reader = "mcp" if self.server else "runtime"
                    self._holdings = hd.Holdings(
                        lambda: self.team.me(), hd.SharedDb(None), reader=reader, rules=self.rules
                    )
            return self._holdings

    def me_now(self) -> MeRead:
        """Our holdings now (album first). The tick comes from the public clock (no key call); unknown = live."""
        try:
            now: Clock | None = self.clock()
        except Exception as e:  # the clock is only the freshness test: without it, /me is read live
            self.log(f"runtime: clock unavailable ({type(e).__name__}); /me read live")
            now = None
        return self.holdings.me(now)

    def clock(self) -> Clock:
        return Clock.model_validate(self.public.clock())

    def catalog(self, tick: int) -> dict[str, Any]:
        """`/api/catalog`, re-read at most every CATALOG_TICKS game ticks (every caller shares 5 req/s);
        each fresh read is stored in `cards` (catalog_db)."""
        cached = self._catalog
        if cached is None or tick < cached[0] or tick - cached[0] >= CATALOG_TICKS:
            cached = (tick, self.public.catalog())
            self._catalog = cached
            self.holdings.observe_catalog(tick, cached[1])
        return cached[1]

    def events(self) -> list[Event]:
        """The captured feed merged with the live window (the window alone when nothing was captured)."""
        try:
            window = self.public.feed_window(DEFAULT_WINDOW)
        except Exception as e:  # the captured history still answers
            self.log(f"runtime: live feed window unavailable ({type(e).__name__})")
            window = []
        return load_events(FeedStore(self.settings.feed_dir), window)

    def us(self) -> str | None:
        from bazaar_agent.identity import resolve_team_id

        read_me = (lambda: self.team.me()) if self.settings.bazaar_key else None
        return resolve_team_id(self.settings.team_id, self.settings.data_dir, read_me)

    def my_offers(self) -> list[dict[str, Any]]:
        from bazaar_agent.agents.seller import offers_in

        return offers_in(self.team.my_offers())

    def commitments(self, me: dict[str, Any], offers: list[dict[str, Any]] | None = None) -> Any:
        from bazaar_agent.agents.seller import open_commitments

        return open_commitments(self.my_offers() if offers is None else offers, str(me.get("id") or ""))

    def rarity_of(self, item: str, tick: int) -> str | None:
        from bazaar_agent.llm.intent import rarity_of

        return rarity_of(self.catalog(tick), item)

    def dealer_running(self) -> str | None:
        """The dealer of a live `dealer buy` child this process started that is still negotiating."""
        for dealer, handle in list(self.dealer_runs.items()):
            if handle.poll() is None:
                return dealer
            del self.dealer_runs[dealer]
        return None


# ---------------------------------------------------------------- read capabilities


def _cut(rows: list[Any], limit: int = MAX_ROWS) -> dict[str, Any]:
    return {"rows": rows[:limit], "total": len(rows), "cut": len(rows) > limit}


def untrusted(text: str | None) -> dict[str, Any] | None:
    """A counterparty's words as data: angle brackets neutralised, injection shapes named. Never obeyed."""
    if not text:
        return None
    return {"untrusted_text": text.replace("<", "‹").replace(">", "›"), "injection_flags": list(injection_flags(text))}


def status(b: Backend) -> dict[str, Any]:
    """`bazaar status`: cash, level, score, album pages with missing cards, duplicates, our cards; `holdings`
    says where /me came from (the Postgres snapshot, its tick and age, or a live read and why)."""
    read = b.me_now()
    me = read.me
    score = me.get("score") or {}
    cards = sorted((a for a in me.get("assets") or [] if a.get("kind") == "card"), key=lambda a: str(a.get("ref")))
    pages = [
        {
            "set": p.set_code,
            "name": p.name,
            "affinity": p.affinity,
            "have": p.have,
            "of": p.of,
            "complete": p.complete,
            "missing": [{"ref": m.ref, "rarity": m.rarity, "value_to_us": m.value_to_us} for m in p.missing],
            "duplicates": list(p.duplicates),
        }
        for p in album_view(me, b.catalog(read.tick or 0))
    ]
    card_rows = [
        {"asset": a.get("id"), "ref": a.get("ref"), "rarity": a.get("rarity"), "your_value": a.get("your_value")}
        for a in cards
    ]
    return {
        "team": me.get("id"),
        "cash": me.get("cash"),
        "level": me.get("level"),
        "score": score.get("score"),
        "rank": score.get("rank"),
        "pages": pages,
        "cards": _cut(card_rows, 60),
        "holdings": read.meta(),
    }


def holdings(b: Backend) -> dict[str, Any]:
    """What we hold, from the shared Postgres snapshot while it is provably current (else /me, stored): our
    cards with asset ids, duplicates, missing page cards (value to us), sealed packs, cash, level, affinity."""
    from bazaar_agent.holdings import parse_me, summary

    read = b.me_now()
    me = parse_me(read.me)
    if me is None:
        return {"holdings": read.meta(), "error": "the /api/me payload did not validate"}
    held = summary(me)
    missing = [
        {"set": p.set_code, "ref": m.ref, "name": m.name, "rarity": m.rarity, "value_to_us": m.value_to_us}
        for p in album_view(read.me, b.catalog(read.tick or 0))
        for m in p.missing
    ]
    return {
        "team": me.id,
        "cash": read.me.get("cash"),
        "level": me.level,
        "affinity": me.affinity,
        "pages": held["pages"],
        "missing": _cut(missing, 60),
        "duplicates": held["duplicates"],
        "packs": held["packs"],
        "cards": _cut(held["cards"], 80),
        "holdings": read.meta(),
    }


def cards(b: Backend, set_code: str | None = None, rarity: str | None = None, ref: str | None = None) -> dict[str, Any]:
    """The card catalog from Postgres (`cards`, kept by the agents and this server), else `/api/catalog`."""
    import psycopg

    from bazaar_agent import catalog_db
    from bazaar_agent.holdings import READ_LOCK_TIMEOUT_S

    with b.holdings.shared.session(READ_LOCK_TIMEOUT_S) as conn:
        if conn is not None:
            try:
                rows = catalog_db.read_cards(conn, set_code, rarity, ref)
            except psycopg.Error as e:  # the catalog is public: the live read below still answers
                b.holdings.shared.failed(e)
                rows = []
            if rows:
                return {"source": "db", **_cut(rows, 80)}
    now = b.clock().tick
    live = [
        r
        for r in catalog_db.rows_as_dicts(catalog_db.card_rows(b.catalog(now)), now)
        if (not set_code or r["set"] == set_code)
        and (not rarity or r["rarity"] == rarity)
        and (not ref or r["ref"] == ref)
    ]
    return {"source": "live", **_cut(live, 80)}


def clock(b: Backend) -> dict[str, Any]:
    """`bazaar clock`: tick, pace, doors and the per-tick limits in force, with the action budget left."""
    c = b.clock()
    return {
        "tick": c.tick,
        "tick_seconds": c.tick_seconds,
        "next_tick_in": c.next_tick_in,
        "t_hours": c.t_hours,
        "doors": c.doors,
        "paused": c.paused,
        "next_opens": c.next_opens,
        "limits": c.limits.model_dump(),
        "action_budget_s": round(action_budget_s(c), 2),
    }


def curves(b: Backend, dealer: str | None = None, item: str | None = None, limit: int = 20) -> dict[str, Any]:
    """`bazaar curves`: dealer concession curves from every team's public threads (ours counted apart)."""
    from bazaar_agent import intel

    threads = [
        t
        for t in intel.dealer_threads(b.events(), b.us())
        if (not dealer or t.dealer == dealer) and (not item or t.item == item)
    ]
    return _cut([asdict(s) for s in intel.curve_summary(threads)], limit)


def tape(b: Backend, item: str | None = None, limit: int = 20) -> dict[str, Any]:
    """`bazaar tape`: the newest settlements first (who bought what from whom, at what price)."""
    from bazaar_agent import intel

    prints = [p for p in intel.tape(b.events()) if not item or p.ref == item]
    return _cut([asdict(p) for p in reversed(prints)], limit)


def teams(b: Backend) -> dict[str, Any]:
    """`bazaar teams`: each competitor's flow and the sets they chase; our own row apart."""
    from bazaar_agent import intel

    theirs, ours = intel.split_us(intel.team_flows(b.events()), b.us(), lambda f: f.team)

    def row(f: Any) -> dict[str, Any]:
        return {
            "team": f.team,
            "dealer_threads": f.dealer_threads,
            "bids": f.bids,
            "buys": f.buys,
            "sells": f.sells,
            "spent": f.spent,
            "earned": f.earned,
            "avg_pack_price": f.avg_pack_price,
            "listings": f.listings,
            "top_sets": [s for s, _ in f.set_interest.most_common(3)],
        }

    return {"competition": _cut([row(f) for f in theirs]), "us": [row(f) for f in ours]}


def book(b: Backend, venue: str = "rastro", card: str | None = None) -> dict[str, Any]:
    """`bazaar book`: a venue's live order book, pseudonyms resolved from the feed; our offers apart."""
    from bazaar_agent import intel

    board = b.public.board(venue).get("offers") or []
    lines = [x for x in intel.order_book(board, intel.listed_makers(b.events())) if not card or x.card == card]
    theirs, ours = intel.split_us(lines, b.us(), lambda x: x.maker)
    return {"venue": venue, "book": _cut([asdict(x) for x in theirs]), "ours": [asdict(x) for x in ours]}


def traders(b: Backend) -> dict[str, Any]:
    """`bazaar traders`: every dealer and team the monitor has seen (Postgres)."""
    from bazaar_agent import db

    with db.connect(b.settings.database_url.get_secret_value(), app="bazaar-runtime") as conn:
        return _cut(db.trader_rows(conn))


def alerts(b: Backend, limit: int = 20) -> dict[str, Any]:
    """`bazaar alerts`: the monitor's latest alerts (new dealers, level changes, announcements)."""
    from bazaar_agent.monitor import read_alerts

    rows = read_alerts(b.settings.data_dir / "alerts.jsonl", limit)
    # A venue name or an announcement is written by a team or the organisers: data, never instructions.
    return {
        "rows": [
            {
                **row,
                "subject": untrusted(str(row.get("subject") or "")),
                "detail": untrusted(str(row.get("detail") or "")),
            }
            for row in rows
        ]
    }


def rules(b: Backend) -> dict[str, Any]:
    """`bazaar rules`: every guardrail, its value and enforcing code; the kill switch; the steering."""
    from bazaar_agent.llm.steering import STEERABLE, STEERING_FILE, load_steering

    loaded = load_guardrails()
    paused = (REPO_ROOT / b.rules.pause_file).exists() or not b.rules.trading_enabled
    steering = load_steering(b.settings.data_dir / STEERING_FILE)
    return {
        "rules": [
            {"rule": r.rule_id, "value": r.raw_value, "enforced_by": ENFORCED_BY.get(r.rule_id), "why": r.why}
            for r in loaded.lines
        ],
        "principles": list(loaded.principles),
        "kill_switch": "paused" if paused else "trading enabled",
        "live": b.live,
        "steerable": {name: bound.meaning for name, bound in STEERABLE.items()},
        "steering": None if steering is None else {**asdict(steering), "deltas": dict(steering.deltas)},
    }


def playbook_now(
    me: dict[str, Any],
    commitments: Any,
    public: Any,
    events: list[Event],
    settings: Settings,
    rules: Guardrails,
    loaded: Any,
    ledger: LedgerStore,
    judge: Any,
) -> tuple[Any, Context]:
    """The ranked, guarded playbook `bazaar strategy` prints and the `strategy` tool returns (one code path)."""
    from bazaar_agent import strategy as st
    from bazaar_agent.agents.seller import committed_context
    from bazaar_agent.llm.steering import STEERING_FILE, steered_strategy_params
    from bazaar_agent.pack_gate import gate_packs

    now = Clock.model_validate(public.clock())
    personas = public.dealers()
    dealers_now = personas.get("personas") or personas.get("dealers") or []
    params = steered_strategy_params(loaded.params, rules, settings.data_dir / STEERING_FILE, now.tick)
    book_ = st.build_playbook(me, public.catalog(), events, dealers_now, params, rules)
    ctx = committed_context(context_from(me, now.tick, now.t_hours, ledger, rules), commitments)
    used = ledger.packs_since(now.t_hours - 1.0)
    slots = st.PackSlots(sum(used.values()), rules.max_packs_per_game_hour)
    book_ = gate_packs(book_, judge, slots, used, rules, now.t_hours)
    return st.guarded(book_, ctx, rules, commitments.listed), ctx


def _move_row(mv: Any) -> dict[str, Any]:
    return {
        "side": mv.side,
        "ref": mv.ref,
        "rarity": mv.rarity,
        "source": mv.source,
        "value": mv.value,
        "price": mv.price,
        "surplus": mv.surplus,
        "limit": mv.limit,
        "reason": mv.reason,
        "guardrail": mv.guardrail,
        "jev": mv.jev,
        "command": mv.command,
    }


def strategy(b: Backend, limit: int = 5) -> dict[str, Any]:
    """`bazaar strategy`: the ranked buys, sells and packs from STRATEGY.md, each with its guardrail verdict."""
    from bazaar_agent.pack_gate import jev_pack_judge
    from bazaar_agent.strategy import load_strategy

    me = b.me_now().me
    book_, ctx = playbook_now(
        me,
        b.commitments(me),
        b.public,
        b.events(),
        b.settings,
        b.rules,
        load_strategy(),
        b.ledger,
        jev_pack_judge(b.settings, b.rules.jev_timeout_s),
    )
    return {
        "tick": book_.tick,
        "cash": book_.cash,
        "above_cash_floor": max(0, ctx.cash - b.rules.cash_floor),
        "spent_last_game_hour": ctx.spent_last_hour,
        "buys": [_move_row(m) for m in book_.buys[:limit]],
        "sells": [_move_row(m) for m in book_.sells[:limit]],
        "packs": [_move_row(m) for m in book_.packs[:limit]],
        "not_proposed": list(book_.skipped[:limit]),
    }


def _thread_view(raw: dict[str, Any]) -> Any:
    from bazaar_agent.conversation import Thread

    return Thread.model_validate(raw)


def _mark_lines(lines: Iterable[dict[str, Any]], us: str | None) -> list[dict[str, Any]]:
    """Our own lines keep their text; a counterparty's words become `untrusted_text` (new dicts)."""
    marked = []
    for line in lines:
        rest = {k: v for k, v in line.items() if k != "text"}
        ours = us is not None and line.get("sender") == us
        marked.append({**rest, "text": line.get("text")} if ours else {**rest, "words": untrusted(line.get("text"))})
    return marked


def _safe_header(header: dict[str, Any]) -> dict[str, Any]:
    """A thread header as data: the topic is written by whoever opened the thread (another team may),
    so it travels as `untrusted_text`; ids and refs pass only when they have the shape of one."""
    import json as _json

    from bazaar_agent.runtime.actions import ITEM, SLUG

    def shaped(value: Any, pattern: str) -> Any:
        return value if value is None or (isinstance(value, str) and re.fullmatch(pattern, value)) else None

    topic = header.get("topic")
    return {
        **header,
        "with": shaped(header.get("with"), SLUG),
        "team": shaped(header.get("team"), SLUG),
        "ref": shaped(header.get("ref"), ITEM),
        "item": shaped(header.get("item"), ITEM),
        "closed_reason": shaped(header.get("closed_reason"), SLUG),
        "topic": untrusted(_json.dumps(topic, ensure_ascii=False)) if topic else None,
    }


def threads(b: Backend, status_filter: str | None = None) -> dict[str, Any]:
    """`bazaar threads`: our negotiation threads with their last message (counterparty words as data)."""
    from bazaar_agent.conversation import summary_json

    us = b.us()
    rows = []
    for raw in b.team.my_threads(status_filter).get("threads") or []:
        summary = summary_json(_thread_view(raw))
        last = summary.get("last")
        rows.append({**_safe_header(summary), "last": _mark_lines([last], us)[0] if last else None})
    return _cut(rows)


def thread(b: Backend, thread_id: int) -> dict[str, Any]:
    """`bazaar thread <id>`: every message with sender and structured price (counterparty words as data)."""
    from bazaar_agent.conversation import conversation_json

    view = conversation_json(_thread_view(b.team.thread(thread_id)))
    return {**view, "thread": _safe_header(view["thread"]), "messages": _mark_lines(view["messages"], b.us())}
