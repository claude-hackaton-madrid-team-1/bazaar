"""Guardrails: the rules in GUARDRAILS.md, parsed into a typed model and enforced before any write.

Every write path (dealer bids and accepts, listings, cancels, thread closes, duel moves) calls `check()`
first. A denied action is not sent; the caller turns it into a walk or a hold. A kill-switch denial
(`Verdict.halted`) is always a HOLD: nothing is sent, not even a cancel or a close, and open offers and
threads stay as they are. `kill_switch()` answers "is it on right now?": it re-reads `trading_enabled`
from GUARDRAILS.md on every call (cached by mtime) and checks the pause file. An append-only ledger
shared by all processes counts spend per game hour and accepts per tick: the Postgres `ledger` table
(`ledger_pg.open_ledger`, shared across machines; required by a live process), or
`.local/ledger.jsonl` (this machine, dry run only).
"""

from __future__ import annotations

import fcntl
import functools
import json
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol, cast, get_args

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from bazaar_agent.config import REPO_ROOT
from bazaar_agent.intel import TEAM_ID

GUARDRAILS_FILE = REPO_ROOT / "GUARDRAILS.md"
RULE_LINE = re.compile(r"^- `(?P<id>[a-z_]+)` = (?P<value>.+?) — (?P<why>.+)$")
PRINCIPLE_LINE = re.compile(r"^- (?!`)(?P<text>.+)$")


class GuardrailsError(ValueError):
    """GUARDRAILS.md has an unknown rule id or a bad value. The runtime refuses to start."""


class Guardrails(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    trading_enabled: bool = True
    pause_file: str = ".local/PAUSE"
    cash_floor: int = 270
    max_spend_per_game_hour: int = 150
    max_price_common: int = 12
    max_price_uncommon: int = 26
    max_price_rare: int = 80
    max_price_pack: int = 20
    max_packs_per_game_hour: int = 3
    sell_min_value_ratio: float = 1.0
    block_buying_held_cards: bool = True
    max_accepts_per_tick: int = 1
    dealer_max_ticks_per_thread: int = 14
    jev_can_accept_early: bool = True
    jev_timeout_s: float = 3.0
    duel_anchor: float = 0.6
    duel_floor_margin: float = 0.05
    duel_endgame_ticks: int = 2
    duel_inside_limit: bool = True
    duel_policy: Literal["v1", "v2"] = "v1"
    duel_max_own_offers: int = Field(default=3, ge=1)
    duel_stall_ticks: int = Field(default=3, ge=1)
    duel_open_wait_ticks: int = Field(default=0, ge=0)
    duel_free_offers: int = Field(default=16, ge=0)
    duel_answer_share: float = Field(default=0.2, ge=0, le=1)
    duel_accept_margin_ticks: int = Field(default=1, ge=0)
    duel_days_signed: bool = False
    steer_max_change: float = Field(default=0.5, ge=0, le=1)
    steer_max_ttl_ticks: int = Field(default=240, ge=1)
    allow_flags: bool = False
    allow_venue_open: bool = False
    venue_bond_reserve: int = Field(default=270, ge=0)
    venue_open_after_game_hours: float = Field(default=6.5, ge=0)
    dealer_price_caps: str = "none"  # "chato:uncommon=31,chato:rare=93": replaces max_price_<rarity> for that dealer
    max_counterparty_share: float = Field(default=1.0, gt=0, le=1)
    counterparty_cap_base: int = Field(default=200, ge=0)

    @field_validator("dealer_price_caps")
    @classmethod
    def _dealer_caps_parse(cls, value: str) -> str:
        parse_dealer_caps(value)
        return value

    @property
    def dealer_caps(self) -> dict[tuple[str, str], int]:
        return dict(_parsed_dealer_caps(self.dealer_price_caps))

    def max_price_for(self, rarity: str | None, dealer: str | None = None) -> int | None:
        """The cap for one rarity; a `dealer_price_caps` entry replaces it for that dealer only."""
        own = dict(_parsed_dealer_caps(self.dealer_price_caps)).get((dealer or "", rarity or ""))
        if dealer is not None and own is not None:
            return own
        return {
            "common": self.max_price_common,
            "uncommon": self.max_price_uncommon,
            "rare": self.max_price_rare,
            "pack": self.max_price_pack,
        }.get(rarity or "")


CAPPED_RARITIES = ("common", "uncommon", "rare", "pack")
MAX_DEALER_CAP = 1000  # runtime.actions.MAX_DEALER_PRICE: a cap above it is a typo


def parse_dealer_caps(value: str) -> dict[tuple[str, str], int]:
    """`none`, or comma-separated `dealer:rarity=price` (`chato:uncommon=31`) → {(dealer, rarity): price}."""
    return dict(_parsed_dealer_caps(value))


@functools.lru_cache(maxsize=16)
def _parsed_dealer_caps(value: str) -> tuple[tuple[tuple[str, str], int], ...]:
    out: dict[tuple[str, str], int] = {}
    if value.strip().lower() in ("", "none"):
        return ()
    for entry in value.split(","):
        m = re.fullmatch(r"\s*([a-z0-9][a-z0-9_-]{0,31}):([a-z]+)=(\d+)\s*", entry)
        if m is None or m[2] not in CAPPED_RARITIES or not 1 <= int(m[3]) <= MAX_DEALER_CAP:
            raise ValueError(
                f"dealer_price_caps entry {entry!r}: use dealer:rarity=price with a price 1..{MAX_DEALER_CAP}, "
                "e.g. chato:uncommon=31"
            )
        out[(m[1], m[2])] = int(m[3])
    return tuple(out.items())


# Which code enforces each rule: shown by `bazaar rules`, kept honest by a test.
ENFORCED_BY: dict[str, str] = {
    "trading_enabled": "guardrails.kill_switch (re-read every tick) + check: hold, never walk",
    "pause_file": "guardrails.kill_switch + check: hold, never walk",
    "cash_floor": "guardrails.check",
    "max_spend_per_game_hour": "guardrails.check + ledger",
    "max_price_common": "guardrails.check",
    "max_price_uncommon": "guardrails.check",
    "max_price_rare": "guardrails.check",
    "max_price_pack": "guardrails.check",
    "max_packs_per_game_hour": "guardrails.check + ledger",
    "sell_min_value_ratio": "guardrails.check",
    "block_buying_held_cards": "guardrails.check (album from /me)",
    "max_accepts_per_tick": "guardrails.check + ledger.reserve_accept (shared, atomic)",
    "dealer_max_ticks_per_thread": "agents.dealer.negotiate",
    "jev_can_accept_early": "cli dealer buy → apply_advice; agents.duel_jev.choose",
    "jev_timeout_s": "jev.judge (dealer buy, taker, duels, maker)",
    "duel_anchor": "agents.duelist.duel_move",
    "duel_floor_margin": "agents.duelist.duel_move",
    "duel_endgame_ticks": "agents.duelist.duel_move",
    "duel_inside_limit": "guardrails.check (duelist.duel_action) + agents.duelist.duel_move + agents.duel_v2",
    "duel_policy": "cli duel run + runtime duel_move + agents.duel_jev (v2: agents.duel_v2.plan_moves)",
    "duel_max_own_offers": "agents.duel_v2.duel_plan (v2 only)",
    "duel_stall_ticks": "agents.duel_v2.duel_plan (v2 only)",
    "duel_open_wait_ticks": "agents.duel_v2.duel_plan (v2 only)",
    "duel_free_offers": "agents.duel_v2.duel_plan (v2 only)",
    "duel_answer_share": "agents.duel_v2.duel_plan (v2 only)",
    "duel_accept_margin_ticks": "agents.duel_v2.duel_plan + plan_moves (v2 only)",
    "duel_days_signed": "guardrails.check (duel_inside_limit) + agents.duel_v2.value_of (v2 only)",
    "steer_max_change": "llm.steering.clamp",
    "steer_max_ttl_ticks": "llm.steering.steering_from_draft",
    "allow_flags": "guardrails.check",
    "allow_venue_open": "guardrails.check (venue open/fee/announce, broker matches); agents.venue_keeper opens it",
    "venue_bond_reserve": "guardrails.check (effective_cash_floor while a planned venue is not open yet)",
    "venue_open_after_game_hours": "guardrails.check (venue_open) + agents.venue_keeper (first tick past it)",
    "dealer_price_caps": "guardrails.check (Action.dealer: cli dealer buy, rules check --dealer, the desk's opens, "
    "bids and accepts, strategy, runtime dealer_buy, ask intents)",
    "max_counterparty_share": "guardrails.check (Action.counterparty + Context.trades: maker posts, taker accepts)",
    "counterparty_cap_base": "guardrails.check (with max_counterparty_share)",
}


@dataclass(frozen=True)
class RuleLine:
    rule_id: str
    raw_value: str
    why: str
    line: int


@dataclass(frozen=True)
class LoadedRules:
    rules: Guardrails
    lines: tuple[RuleLine, ...]
    principles: tuple[str, ...]
    path: Path


def _coerce(raw: str) -> Any:
    value = raw.strip().strip("`")
    if value.lower() in ("true", "false"):
        return value.lower() == "true"
    for convert in (int, float):
        try:
            return convert(value)
        except ValueError:
            pass
    return value


def parse_md_config(text: str, path: Path) -> tuple[tuple[RuleLine, ...], tuple[str, ...], dict[str, Any]]:
    """Read `` - `id` = value — why `` lines (rules) and other bullets (principles) from a Markdown file."""
    lines, principles, values = [], [], {}
    for number, raw in enumerate(text.splitlines(), start=1):
        if m := RULE_LINE.match(raw.strip()):
            rule = RuleLine(m["id"], m["value"].strip(), m["why"].strip(), number)
            if rule.rule_id in values:
                raise GuardrailsError(f"{path.name}:{number}: rule `{rule.rule_id}` is defined twice")
            lines.append(rule)
            values[rule.rule_id] = _coerce(rule.raw_value)
        elif raw.strip().startswith("- `"):
            raise GuardrailsError(f"{path.name}:{number}: not a rule line (expected - `id` = value — why)")
        elif m := PRINCIPLE_LINE.match(raw.strip()):
            principles.append(m["text"])
    return tuple(lines), tuple(principles), values


def validated(model: type[BaseModel], values: dict[str, Any], path: Path) -> Any:
    try:
        return model.model_validate(values)
    except ValidationError as e:
        problems = "; ".join(f"{'.'.join(map(str, err['loc']))}: {err['msg']}" for err in e.errors())
        raise GuardrailsError(f"{path.name}: {problems}") from None


def parse_guardrails(text: str, path: Path = GUARDRAILS_FILE) -> LoadedRules:
    lines, principles, values = parse_md_config(text, path)
    return LoadedRules(validated(Guardrails, values, path), lines, principles, path)


def load_guardrails(path: Path = GUARDRAILS_FILE) -> LoadedRules:
    if not path.is_file():
        raise GuardrailsError(f"{path} is missing: the runtime will not trade without its guardrails")
    return parse_guardrails(path.read_text(encoding="utf-8"), path)


# ---------------------------------------------------------------- the kill switch (read live)

_SWITCH_CACHE: dict[Path, tuple[tuple[int, int], str | None]] = {}


def _file_stop(path: Path) -> str | None:
    """Why GUARDRAILS.md stops trading right now (None: `trading_enabled` = true). Parsed again only when
    the file changed (mtime, size). Missing or invalid: every write holds (fail closed) until it is fixed."""
    try:
        stat = path.stat()
    except OSError:
        return f"{path.name} is missing: holding"
    key = (stat.st_mtime_ns, stat.st_size)
    cached = _SWITCH_CACHE.get(path)
    if cached is not None and cached[0] == key:
        return cached[1]
    try:
        stop = None if load_guardrails(path).rules.trading_enabled else "trading_enabled = false"
    except (GuardrailsError, OSError) as e:
        stop = f"{path.name} is invalid ({e}): holding"
    _SWITCH_CACHE[path] = (key, stop)
    return stop


def kill_switch(rules: Guardrails, path: Path | None = None) -> tuple[str, ...]:
    """Is the kill switch on right now? Every reason it is (empty: trading may go on).

    `trading_enabled` comes from GUARDRAILS.md as it is NOW (`path`, default `GUARDRAILS_FILE`), so an
    edit takes effect on the next tick without a restart, both ways; the pause file is `rules.pause_file`.
    While it is on our processes send NOTHING to the game (no bids, accepts, posts, cancels, thread
    closes or walks); reads continue, and open offers and threads stay exactly as they are.
    """
    stops = [stop] if (stop := _file_stop(path or GUARDRAILS_FILE)) else []
    if (REPO_ROOT / rules.pause_file).exists():
        stops.append(f"pause file {rules.pause_file} exists")
    return tuple(stops)


# ---------------------------------------------------------------- ledger (shared by processes)


def is_pack(item: str) -> bool:
    """'sobre_barrio' is a pack; 'LAV-09' is a card and 'duel:12' is not an item we hold."""
    return bool(item) and "-" not in item and ":" not in item


LedgerKind = Literal["spend", "accept", "listing"]
# A listing a person posted by hand (`bazaar sell ... --live`) is booked with this item prefix and its offer id:
# the maker, which owns our board offers, never cancels or reprices it.
HANDS_OFF = "hands-off:"


def hands_off_id(item: str) -> int | None:
    rest = item[len(HANDS_OFF) :] if item.startswith(HANDS_OFF) else ""
    return int(rest) if rest.isdigit() else None


class LedgerStore(Protocol):
    """What the guardrails read and the agents write: the JSONL file or the shared Postgres table."""

    @property
    def where(self) -> str: ...  # where the counts live, for logs: "file ledger.jsonl", "postgres ledger table on …"

    def record(self, kind: str, tick: int, t_hours: float, price: int = 0, item: str = "") -> None: ...
    def spent_since(self, t_hours: float) -> int: ...
    def packs_since(self, t_hours: float) -> Counter[str]: ...
    def accepts_in_tick(self, tick: int) -> int: ...
    def count_in_tick(self, kind: str, tick: int) -> int: ...
    def accept_items(self, tick: int) -> list[str]: ...
    def reserve_accept(self, tick: int, t_hours: float, price: int, item: str, limit: int) -> bool: ...
    def hands_off_ids(self) -> set[int]: ...


class Ledger:
    """Append-only JSONL of committed spend and accepts, read by every process on this machine."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.where = f"file {path.name}"

    def entries(self) -> list[dict[str, Any]]:
        if not self.path.is_file():
            return []
        return [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def record(self, kind: str, tick: int, t_hours: float, price: int = 0, item: str = "") -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        entry = {"kind": kind, "tick": tick, "t_hours": t_hours, "price": price, "item": item}
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry) + "\n")

    def spent_since(self, t_hours: float) -> int:
        return sum(
            int(e.get("price", 0)) for e in self.entries() if e.get("kind") == "spend" and e["t_hours"] > t_hours
        )

    def packs_since(self, t_hours: float) -> Counter[str]:
        """Packs bought after `t_hours`, by pack id (a pack spend carries the pack id as its item)."""
        return Counter(
            str(e.get("item"))
            for e in self.entries()
            if e.get("kind") == "spend" and e["t_hours"] > t_hours and is_pack(str(e.get("item") or ""))
        )

    def accepts_in_tick(self, tick: int) -> int:
        return self.count_in_tick("accept", tick)

    def count_in_tick(self, kind: str, tick: int) -> int:
        return sum(1 for e in self.entries() if e.get("kind") == kind and e.get("tick") == tick)

    def accept_items(self, tick: int) -> list[str]:
        """What took this tick's accepts: a card ref, a pack id, or `duel:<id>`."""
        return [str(e.get("item") or "") for e in self.entries() if e.get("kind") == "accept" and e.get("tick") == tick]

    def hands_off_ids(self) -> set[int]:
        """Offer ids a person posted by hand (`HANDS_OFF` listing rows)."""
        ids = (hands_off_id(str(e.get("item") or "")) for e in self.entries() if e.get("kind") == "listing")
        return {i for i in ids if i is not None}

    def reserve_accept(self, tick: int, t_hours: float, price: int, item: str, limit: int) -> bool:
        """Count and record an accept under one file lock: two processes cannot both take the last slot."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.with_suffix(".lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                if self.accepts_in_tick(tick) >= limit:
                    return False
                self.record("accept", tick, t_hours, price, item)
                return True
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)


LedgerRow = tuple[str, int, float, int, str]  # kind, tick, t_hours, price, item: `LedgerStore.record`'s arguments


def refund_row(
    price: int, item: str, created_tick: int | None, tick: int, t_hours: float, max_tick_seconds: float
) -> LedgerRow:
    """The ledger row that gives back a withdrawn bid's spend, booked in the game hour it was spent (the
    bid's `created_tick`): a refund booked at cancel time would outlive its spend inside the one-hour
    window and let `max_spend_per_game_hour` be spent twice.

    Only the spend's tick is known, and the pace may have changed since (60 s Friday ticks, 30 s on
    Saturday), so every tick since is taken at the clock's slowest pace (`max_tick_seconds`), plus one tick
    for the clock's rounded `t_hours`: the refund is never dated after its spend. Dated a little earlier, it
    leaves the window first, and the hour's spend over-counts for a moment (fail safe). A future created
    tick counts as now; an unknown one is dated an hour back, outside every window: no refund, fail safe."""
    if not isinstance(created_tick, int):
        return ("spend", tick, t_hours - 1.0, -price, item)
    ticks_ago = max(0, tick - created_tick)
    return ("spend", tick - ticks_ago, t_hours - (ticks_ago + 1) * max_tick_seconds / 3600, -price, item)


# ---------------------------------------------------------------- the check


DUEL_DAYS_MAX = 10  # RULES.md: two-issue duels trade delivery days 0 to 10


def duel_days_ok(days: float) -> bool:
    """Inside the rules' 0 to 10 days. Anything else (negative, NaN) would turn the days penalty into a bonus."""
    return 0 <= days <= DUEL_DAYS_MAX


# `cancel` (withdraw one of our offers) and `close_thread` (walk from a thread) move no cash: only the
# kill switch applies to them.
ActionKind = Literal[
    "buy",
    "sell",
    "accept_buy",
    "accept_sell",
    "bid",
    "duel_offer",
    "duel_accept",
    "flag",
    "venue_open",
    "venue_close",
    "venue_fee",
    "venue_announce",
    "broker_match",
    "cancel",
    "close_thread",
]
ACTION_KINDS: tuple[str, ...] = get_args(ActionKind)
TEAM_TRADES = ("buy", "sell", "accept_buy", "accept_sell", "bid")  # the kinds a counterparty cap applies to
ANY_TEAM = "*"  # the counterparty of an offer anyone may take: the worst case is the team we trade most with


@dataclass(frozen=True)
class Action:
    kind: ActionKind
    item: str = ""  # card ref, pack id or duel id
    rarity: str | None = None  # "common" | "uncommon" | "rare" | "pack" | ...
    price: int | None = None
    your_value: float | None = None  # for sells: what we lose by selling that copy
    limit: int | None = None  # duels: our private limit (a seller's cost, a buyer's value)
    role: str | None = None  # duels: "seller" | "buyer"
    days: float | None = None  # two-issue duels: the delivery days of the deal (None in price-only duels)
    days_weight: float | None = None  # two-issue duels: `your_days_weight`
    dealer: str | None = None  # a dealer buy: `dealer_price_caps` may set its own cap
    # Team-to-team trades: the other team (ANY_TEAM for an offer anyone may take). None: not a team trade
    # (a dealer), and `max_counterparty_share` does not apply.
    counterparty: str | None = None
    volume: int | None = None  # what the trade adds to the counterparty's share (default: `price`)


@dataclass(frozen=True)
class Verdict:
    allowed: bool
    violations: tuple[str, ...] = field(default_factory=tuple)
    halted: bool = False  # the kill switch is among the reasons: HOLD (send nothing), never walk

    def __str__(self) -> str:
        return "allowed" if self.allowed else "denied: " + "; ".join(self.violations)


@dataclass(frozen=True)
class TradeBook:
    """Our team-to-team volume in primas: settled with each counterparty, and what our open board offers
    could still add (`addressed` to one team, or `public`: anyone may take those, so the worst case is that
    one team takes them all)."""

    settled: dict[str, int] = field(default_factory=dict)
    addressed: dict[str, int] = field(default_factory=dict)
    public: int = 0

    @property
    def total(self) -> int:
        return sum(self.settled.values())

    def exposure(self, team: str) -> int:
        """The most `team` may have traded with us once every open offer it can take fills."""
        if team == ANY_TEAM:
            teams = set(self.settled) | set(self.addressed)
            return max((self.exposure(t) for t in teams), default=self.public)
        return self.settled.get(team, 0) + self.addressed.get(team, 0) + self.public


def counterparty_refusal(trades: TradeBook | None, team: str, price: int, rules: Guardrails) -> str | None:
    """Why a trade of `price` with `team` would break `max_counterparty_share`; None when it passes or the
    cap is off (1.0). The cap is `share × max(our settled volume + price, counterparty_cap_base)`: the base
    lets the first trades through. Fails closed when our volume was not read, or when the counterparty is
    not a team id (a board pseudonym the feed did not resolve: its volume with us is unknown)."""
    share = rules.max_counterparty_share
    if share >= 1:
        return None
    if trades is None:
        return "max_counterparty_share is on but our team-to-team volume was not read"
    if team != ANY_TEAM and not TEAM_ID.match(team):
        return f"counterparty {team!r} is not a known team: its share of our volume is unknown"
    cap = share * max(trades.total + price, rules.counterparty_cap_base)
    exposure = trades.exposure(team)
    if exposure + price <= cap:
        return None
    who = "any team (public offer)" if team == ANY_TEAM else team
    return (
        f"counterparty {who}: {exposure} + {price} > max_counterparty_share {share:g} × "
        f"max(volume {trades.total + price}, {rules.counterparty_cap_base}) = {cap:.0f}"
    )


@dataclass(frozen=True)
class Context:
    cash: int
    held: dict[str, int]  # card ref -> copies we hold (from /api/me)
    tick: int
    t_hours: float
    spent_last_hour: int = 0
    accepts_this_tick: int = 0
    paused: bool = False
    packs_last_hour: int = 0
    # The kill switch read live by `kill_switch()` (context_from fills it). None: not read, so `check()`
    # falls back to `rules.trading_enabled` and `paused`.
    stops: tuple[str, ...] | None = None
    has_venue: bool = False  # we run a venue we opened (open or closing), from /api/me `venue`
    # Our team-to-team volume (`TradeBook`), for `max_counterparty_share`. None: not read.
    trades: TradeBook | None = None


def runs_venue(me: dict[str, Any]) -> bool:
    """/api/me `venue`: our own market, open or closing (the bond is in it). A free starter stall is not one:
    /me carries `starter_broker_key` while we have the stall (the kit's `Bazaar.me`), and opening our own
    venue replaces the stall (RULES.md), so a venue named next to that key is the stall. The same answer
    drives the bond reserve and the refusal of a second opening."""
    venue = me.get("venue")
    if not venue or me.get("starter_broker_key"):
        return False
    if isinstance(venue, str):
        return True
    if not isinstance(venue, dict) or venue.get("starter") is True:
        return False
    return str(venue.get("status") or "open") in ("open", "closing")


def effective_cash_floor(rules: Guardrails, ctx: Context) -> int:
    """`cash_floor`, plus `venue_bond_reserve` while a planned venue (`allow_venue_open`) is not open yet:
    every purchase leaves the bond and opening fee in cash until the venue opens. The same for every writer."""
    reserve = rules.venue_bond_reserve if rules.allow_venue_open and not ctx.has_venue else 0
    return rules.cash_floor + reserve


def floor_text(rules: Guardrails, ctx: Context) -> str:
    if effective_cash_floor(rules, ctx) == rules.cash_floor:
        return f"cash_floor {rules.cash_floor}"
    return f"cash_floor {rules.cash_floor} + venue_bond_reserve {rules.venue_bond_reserve}"


def context_from(me: dict[str, Any], tick: int, t_hours: float, ledger: LedgerStore, rules: Guardrails) -> Context:
    held: dict[str, int] = {}
    for a in me.get("assets") or []:
        if a.get("kind") == "card":
            held[str(a.get("ref"))] = held.get(str(a.get("ref")), 0) + 1
    return Context(
        cash=int(me.get("cash") or 0),
        held=held,
        tick=tick,
        t_hours=t_hours,
        spent_last_hour=ledger.spent_since(t_hours - 1.0),
        accepts_this_tick=ledger.accepts_in_tick(tick),
        paused=(REPO_ROOT / rules.pause_file).exists(),
        packs_last_hour=sum(ledger.packs_since(t_hours - 1.0).values()),
        stops=kill_switch(rules),
        has_venue=runs_venue(me),
    )


def action_kind(kind: str) -> ActionKind:
    if kind not in ACTION_KINDS:
        raise ValueError(f"unknown action kind {kind!r}: use one of {', '.join(ACTION_KINDS)}")
    return cast(ActionKind, kind)


def check(action: Action, ctx: Context, rules: Guardrails) -> Verdict:
    """Every rule that the action breaks. Empty → allowed. The kill switch comes first and sets `halted`."""
    v: list[str] = list(halts(ctx, rules))
    halted = bool(v)
    buying = action.kind in ("buy", "accept_buy", "bid")
    accepting = action.kind in ("accept_buy", "accept_sell", "duel_accept")
    if buying and action.price is not None:
        cap = rules.max_price_for(action.rarity, action.dealer)
        own = cap != rules.max_price_for(action.rarity) or (action.dealer, action.rarity or "") in rules.dealer_caps
        if cap is None:
            v.append(f"no max_price for rarity {action.rarity!r}: buying it is not allowed")
        elif action.price > cap:
            where = f"dealer_price_caps {action.dealer}:{action.rarity}" if own else f"max_price_{action.rarity}"
            v.append(f"price {action.price} > {where} {cap}")
        if ctx.cash - action.price < effective_cash_floor(rules, ctx):
            v.append(f"cash {ctx.cash} - {action.price} < {floor_text(rules, ctx)}")
        if ctx.spent_last_hour + action.price > rules.max_spend_per_game_hour:
            v.append(
                f"spend {ctx.spent_last_hour} + {action.price} > max_spend_per_game_hour "
                f"{rules.max_spend_per_game_hour}"
            )
    if buying and action.rarity == "pack" and ctx.packs_last_hour >= rules.max_packs_per_game_hour:
        v.append(
            f"{ctx.packs_last_hour} pack(s) bought this game hour (max_packs_per_game_hour "
            f"{rules.max_packs_per_game_hour})"
        )
    if buying and rules.block_buying_held_cards and ctx.held.get(action.item, 0) > 0:
        v.append(f"we already hold {action.item} (block_buying_held_cards)")
    if action.kind in ("sell", "accept_sell") and action.price is not None and action.your_value is not None:
        floor = action.your_value * rules.sell_min_value_ratio
        if action.price < floor:
            v.append(f"sell price {action.price} < {rules.sell_min_value_ratio} × your_value {action.your_value}")
    if accepting and ctx.accepts_this_tick >= rules.max_accepts_per_tick:
        v.append(f"{ctx.accepts_this_tick} accept(s) already this tick (max_accepts_per_tick)")
    team_trade = action.kind in TEAM_TRADES and action.counterparty is not None and action.price is not None
    volume = action.volume if action.volume is not None else action.price or 0
    if team_trade and (refusal := counterparty_refusal(ctx.trades, str(action.counterparty), volume, rules)):
        v.append(refusal)
    if action.kind == "flag" and not rules.allow_flags:
        v.append("allow_flags = false")
    if action.kind in ("duel_offer", "duel_accept") and rules.duel_inside_limit:
        v2 = rules.duel_policy == "v2"
        v.extend(_duel_limit_violations(action, v2 and rules.duel_days_signed, zero_days_free=v2))
    v.extend(_venue_violations(action, ctx, rules))
    return Verdict(not v, tuple(v), halted)


def _duel_limit_violations(action: Action, signed: bool = False, zero_days_free: bool = False) -> list[str]:
    """A duel deal must be strictly better than our limit (a seller above its cost, a buyer below its value),
    after its days at |weight| each against us: the same worst case as `duelist.worth`, recomputed here.
    `signed` (`duel_days_signed`, v2 only): the weight is primas gained (+) or lost (−) per day instead.
    `zero_days_free` (v2 only): 0 days cost nothing under either sign, so a missing weight does not block them."""
    if action.price is None or action.limit is None or action.role not in ("seller", "buyer"):
        return ["cannot value the duel move (price, limit or role missing): duel_inside_limit"]
    missing = action.days is not None and action.days_weight is None
    if missing and (action.days or not zero_days_free):  # v2: 0 days cost nothing whatever the weight (B2c)
        return ["days without your_days_weight: cannot value the duel move (duel_inside_limit)"]
    if action.days is not None and not duel_days_ok(action.days):
        return [f"days {action.days} outside 0 to {DUEL_DAYS_MAX}: cannot value the duel move (duel_inside_limit)"]
    if action.days_weight is not None and not math.isfinite(action.days_weight):
        return [f"your_days_weight {action.days_weight}: cannot value the duel move (duel_inside_limit)"]
    weight = action.days_weight or 0.0
    penalty = (-weight if signed else abs(weight)) * (action.days or 0.0)
    seller = action.role == "seller"
    worth = action.price - penalty if seller else action.price + penalty
    if (worth > action.limit) if seller else (worth < action.limit):
        return []
    side = "above" if seller else "below"
    return [
        f"duel {action.role} price {action.price} is worth {worth:g}, not strictly {side} limit "
        f"{action.limit} (duel_inside_limit)"
    ]


def halts(ctx: Context, rules: Guardrails) -> tuple[str, ...]:
    """The kill-switch reasons in this context: the live read when there is one, else the loaded rules."""
    if ctx.stops is not None:
        return ctx.stops
    stops = [] if rules.trading_enabled else ["trading_enabled = false"]
    if ctx.paused:
        stops.append(f"pause file {rules.pause_file} exists")
    return tuple(stops)


# Our own market (RULES.md "Your own market"): opening costs a refundable bond plus an opening fee.
VENUE_BOND = 250
VENUE_OPENING_FEE = 20
VENUE_COST = VENUE_BOND + VENUE_OPENING_FEE
# Writes that only make sense while we run a venue: all of them wait for `allow_venue_open`. Closing does not,
# so a venue opened by hand can still be closed from the CLI (the kill switch still stops it).
VENUE_SWITCHED: frozenset[str] = frozenset({"venue_open", "venue_fee", "venue_announce", "broker_match"})


def _venue_violations(action: Action, ctx: Context, rules: Guardrails) -> list[str]:
    """Venue writes: the switch, opening once and not before `venue_open_after_game_hours`, and the bond +
    opening fee never taking cash below `cash_floor` (the reserve is what the opening spends, so it is not
    added on top here).

    The bond is not a purchase: it is never counted against `max_spend_per_game_hour` or a rarity cap
    (`action.price` is the cash the open takes, `VENUE_COST` when the caller leaves it out)."""
    v: list[str] = []
    if action.kind in VENUE_SWITCHED and not rules.allow_venue_open:
        v.append("allow_venue_open = false (build only: flip it in GUARDRAILS.md to run our venue)")
    if action.kind == "venue_open":
        cost = VENUE_COST if action.price is None else action.price
        if ctx.cash - cost < rules.cash_floor:
            v.append(f"cash {ctx.cash} - venue bond and fee {cost} < cash_floor {rules.cash_floor}")
        if ctx.has_venue:
            v.append("we already run a venue: never open a second one")
        if ctx.t_hours < rules.venue_open_after_game_hours:
            v.append(f"game hour {ctx.t_hours:g} < venue_open_after_game_hours {rules.venue_open_after_game_hours:g}")
    return v
