"""Guardrails: the rules in GUARDRAILS.md, parsed into a typed model and enforced before any write.

Every write path (dealer bids and accepts, listings, cancels, thread closes, duel moves) calls `check()`
first. A denied action is not sent; the caller turns it into a walk or a hold. A kill-switch denial
(`Verdict.halted`) is always a HOLD: nothing is sent, not even a cancel or a close, and open offers and
threads stay as they are. `kill_switch()` answers "is it on right now?": it re-reads `trading_enabled`
from GUARDRAILS.md on every call (cached by mtime) and checks the pause file. An append-only ledger
shared by all processes counts spend per game hour and accepts per tick: the Postgres `ledger` table
when DATABASE_URL is reachable (`ledger_pg.open_ledger`, shared across machines), else
`.local/ledger.jsonl` (this machine).
"""

from __future__ import annotations

import fcntl
import json
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol, cast, get_args

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from bazaar_agent.config import REPO_ROOT

GUARDRAILS_FILE = REPO_ROOT / "GUARDRAILS.md"
RULE_LINE = re.compile(r"^- `(?P<id>[a-z_]+)` = (?P<value>.+?) — (?P<why>.+)$")
PRINCIPLE_LINE = re.compile(r"^- (?!`)(?P<text>.+)$")
SET_CODE = re.compile(r"^[A-Z]{3}$")
OFF_PAGE_RARITIES = ("epic", "legendary")  # RULES.md: on top of the page; any other rarity counts as a page card
NO_SETS = ("", "none", "-")


def set_codes(value: str) -> tuple[str, ...]:
    """'RET,CHA' -> ('RET', 'CHA'); 'none' -> (). A code that is not three capitals is refused."""
    if value.strip().lower() in NO_SETS:
        return ()
    codes = tuple(c.strip() for c in value.split(",") if c.strip())
    bad = [c for c in codes if not SET_CODE.match(c)]
    if bad:
        raise ValueError(f"not a set code: {', '.join(bad)} (use e.g. RET,CHA or none)")
    return codes


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
    holdings_from_db: bool = True
    holdings_max_age_s: float = Field(default=5.0, ge=0, le=60)
    max_accepts_per_tick: int = 1
    dealer_max_ticks_per_thread: int = 14
    jev_can_accept_early: bool = True
    jev_timeout_s: float = 3.0
    duel_anchor: float = 0.6
    duel_floor_margin: float = 0.05
    duel_endgame_ticks: int = 2
    steer_max_change: float = Field(default=0.5, ge=0, le=1)
    steer_max_ttl_ticks: int = Field(default=240, ge=1)
    allow_flags: bool = False
    max_flags_per_process: int = Field(default=2, ge=0, le=20)
    flag_trusted_dealers: str = "abuela,chato"  # comma-separated dealer ids the offer inspector never flags
    inspect_accepts: bool = True

    @field_validator("flag_trusted_dealers")
    @classmethod
    def _trusted_parse(cls, value: str) -> str:
        if value.strip().lower() == "none":
            return value
        ids = [d.strip() for d in value.split(",")]
        if not all(re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,31}", d) for d in ids):
            raise ValueError(f"flag_trusted_dealers {value!r}: comma-separated dealer ids, e.g. abuela,chato (or none)")
        return value

    @property
    def trusted_dealers(self) -> frozenset[str]:
        return frozenset(d.strip() for d in self.flag_trusted_dealers.split(",") if d.strip() and d.strip() != "none")

    protect_page_sets: str = "none"

    @field_validator("protect_page_sets")
    @classmethod
    def _known_set_codes(cls, value: str) -> str:
        set_codes(value)
        return value

    def protects(self, ref: str, rarity: str | None, copies: int) -> bool:
        """Our only copy of a page card of a protected (new) page: never sold. A copy of unknown rarity
        counts as a page card (fail closed); a duplicate may still be sold."""
        code = ref.split("-", 1)[0].strip().upper() if "-" in ref else ""
        page_card = str(rarity or "").strip().lower() not in OFF_PAGE_RARITIES
        return copies <= 1 and page_card and code in set_codes(self.protect_page_sets)

    def max_price_for(self, rarity: str | None) -> int | None:
        return {
            "common": self.max_price_common,
            "uncommon": self.max_price_uncommon,
            "rare": self.max_price_rare,
            "pack": self.max_price_pack,
        }.get(rarity or "")


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
    "holdings_from_db": "holdings.Holdings.me",
    "holdings_max_age_s": "holdings.Holdings.me (Postgres clock)",
    "max_accepts_per_tick": "guardrails.check + ledger.reserve_accept (shared, atomic)",
    "dealer_max_ticks_per_thread": "agents.dealer.negotiate",
    "jev_can_accept_early": "cli dealer buy → apply_advice; agents.duel_jev.choose",
    "jev_timeout_s": "jev.judge (dealer buy, taker, duels, maker)",
    "duel_anchor": "agents.duelist.duel_move",
    "duel_floor_margin": "agents.duelist.duel_move",
    "duel_endgame_ticks": "agents.duelist.duel_move",
    "steer_max_change": "llm.steering.clamp",
    "steer_max_ttl_ticks": "llm.steering.steering_from_draft",
    "allow_flags": "guardrails.check",
    "max_flags_per_process": "agents.inspector.FlagBook (flag_step: cli dealer buy, the desk)",
    "flag_trusted_dealers": "agents.inspector.FlagBook (flag_step: cli dealer buy, the desk)",
    "inspect_accepts": "agents.accept_gate (taker accepts, cli dealer buy, duel run --play, runtime duel_move)",
    "protect_page_sets": "guardrails.check (album from /me) + strategy.sell_moves",
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
        loaded = load_guardrails(path)
        if "trading_enabled" not in {line.rule_id for line in loaded.lines}:  # empty or truncated mid-save
            stop: str | None = f"{path.name} has no trading_enabled line: holding"
        else:
            stop = None if loaded.rules.trading_enabled else "trading_enabled = false"
    except (GuardrailsError, OSError, ValueError) as e:  # UnicodeDecodeError is a ValueError
        stop = f"{path.name} is invalid ({type(e).__name__}: {str(e)[:160]}): holding; see `uv run bazaar rules`"
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


class LedgerStore(Protocol):
    """What the guardrails read and the agents write: the JSONL file or the shared Postgres table."""

    where: str

    def record(self, kind: str, tick: int, t_hours: float, price: int = 0, item: str = "") -> None: ...
    def spent_since(self, t_hours: float) -> int: ...
    def packs_since(self, t_hours: float) -> Counter[str]: ...
    def accepts_in_tick(self, tick: int) -> int: ...
    def count_in_tick(self, kind: str, tick: int) -> int: ...
    def accept_items(self, tick: int) -> list[str]: ...
    def reserve_accept(self, tick: int, t_hours: float, price: int, item: str, limit: int) -> bool: ...


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


# `cancel` (withdraw one of our offers) and `close_thread` (walk from a thread) move no cash: only the
# kill switch applies to them.
ActionKind = Literal[
    "buy", "sell", "accept_buy", "accept_sell", "bid", "duel_offer", "duel_accept", "flag", "cancel", "close_thread"
]
ACTION_KINDS: tuple[str, ...] = get_args(ActionKind)


@dataclass(frozen=True)
class Action:
    kind: ActionKind
    item: str = ""  # card ref, pack id or duel id
    rarity: str | None = None  # "common" | "uncommon" | "rare" | "pack" | ...
    price: int | None = None
    your_value: float | None = None  # for sells: what we lose by selling that copy


@dataclass(frozen=True)
class Verdict:
    allowed: bool
    violations: tuple[str, ...] = field(default_factory=tuple)
    halted: bool = False  # the kill switch is among the reasons: HOLD (send nothing), never walk

    def __str__(self) -> str:
        return "allowed" if self.allowed else "denied: " + "; ".join(self.violations)


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
    sellable: dict[str, int] | None = None  # copies not already in our open asks (seller.committed_context)


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
        cap = rules.max_price_for(action.rarity)
        if cap is None:
            v.append(f"no max_price for rarity {action.rarity!r}: buying it is not allowed")
        elif action.price > cap:
            v.append(f"price {action.price} > max_price_{action.rarity} {cap}")
        if ctx.cash - action.price < rules.cash_floor:
            v.append(f"cash {ctx.cash} - {action.price} < cash_floor {rules.cash_floor}")
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
    selling = action.kind in ("sell", "accept_sell")
    copies = (ctx.held if ctx.sellable is None else ctx.sellable).get(action.item, 0)
    if selling and rules.protects(action.item, action.rarity, copies):
        v.append(f"{action.item} is our only copy of a page card of a new page (protect_page_sets)")
    if accepting and ctx.accepts_this_tick >= rules.max_accepts_per_tick:
        v.append(f"{ctx.accepts_this_tick} accept(s) already this tick (max_accepts_per_tick)")
    if action.kind == "flag" and not rules.allow_flags:
        v.append("allow_flags = false")
    return Verdict(not v, tuple(v), halted)


def halts(ctx: Context, rules: Guardrails) -> tuple[str, ...]:
    """The kill-switch reasons in this context: the live read when there is one, else the loaded rules."""
    if ctx.stops is not None:
        return ctx.stops
    stops = [] if rules.trading_enabled else ["trading_enabled = false"]
    if ctx.paused:
        stops.append(f"pause file {rules.pause_file} exists")
    return tuple(stops)
