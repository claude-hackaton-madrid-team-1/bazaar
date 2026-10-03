"""Guardrails: the rules in GUARDRAILS.md, parsed into a typed model and enforced before any write.

Every write path (dealer bids and accepts, duel moves) calls `check()` first. A denied action is
not sent; the caller turns it into a walk or a hold. An append-only ledger shared by all processes
counts spend per game hour and accepts per tick: the Postgres `ledger` table when DATABASE_URL is
reachable (`ledger_pg.open_ledger`, shared across machines), else `.local/ledger.jsonl` (this machine).
"""

from __future__ import annotations

import fcntl
import json
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol, cast, get_args

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from bazaar_agent.config import REPO_ROOT

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

    def max_price_for(self, rarity: str | None) -> int | None:
        return {
            "common": self.max_price_common,
            "uncommon": self.max_price_uncommon,
            "rare": self.max_price_rare,
            "pack": self.max_price_pack,
        }.get(rarity or "")


# Which code enforces each rule: shown by `bazaar rules`, kept honest by a test.
ENFORCED_BY: dict[str, str] = {
    "trading_enabled": "guardrails.check",
    "pause_file": "guardrails.check",
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


# ---------------------------------------------------------------- the check


DUEL_DAYS_MAX = 10  # RULES.md: two-issue duels trade delivery days 0 to 10


def duel_days_ok(days: float) -> bool:
    """Inside the rules' 0 to 10 days. Anything else (negative, NaN) would turn the days penalty into a bonus."""
    return 0 <= days <= DUEL_DAYS_MAX


ActionKind = Literal["buy", "sell", "accept_buy", "accept_sell", "bid", "duel_offer", "duel_accept", "flag"]
ACTION_KINDS: tuple[str, ...] = get_args(ActionKind)


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


@dataclass(frozen=True)
class Verdict:
    allowed: bool
    violations: tuple[str, ...] = field(default_factory=tuple)

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
    )


def action_kind(kind: str) -> ActionKind:
    if kind not in ACTION_KINDS:
        raise ValueError(f"unknown action kind {kind!r}: use one of {', '.join(ACTION_KINDS)}")
    return cast(ActionKind, kind)


def check(action: Action, ctx: Context, rules: Guardrails) -> Verdict:
    """Every rule that the action breaks. Empty → allowed."""
    v: list[str] = []
    if not rules.trading_enabled:
        v.append("trading_enabled = false")
    if ctx.paused:
        v.append(f"pause file {rules.pause_file} exists")
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
    if accepting and ctx.accepts_this_tick >= rules.max_accepts_per_tick:
        v.append(f"{ctx.accepts_this_tick} accept(s) already this tick (max_accepts_per_tick)")
    if action.kind == "flag" and not rules.allow_flags:
        v.append("allow_flags = false")
    if action.kind in ("duel_offer", "duel_accept") and rules.duel_inside_limit:
        v.extend(_duel_limit_violations(action, rules.duel_policy == "v2" and rules.duel_days_signed))
    return Verdict(not v, tuple(v))


def _duel_limit_violations(action: Action, signed: bool = False) -> list[str]:
    """A duel deal must be strictly better than our limit (a seller above its cost, a buyer below its value),
    after its days at |weight| each against us: the same worst case as `duelist.worth`, recomputed here.
    `signed` (`duel_days_signed`, v2 only): the weight is primas gained (+) or lost (−) per day instead."""
    if action.price is None or action.limit is None or action.role not in ("seller", "buyer"):
        return ["cannot value the duel move (price, limit or role missing): duel_inside_limit"]
    if action.days is not None and action.days_weight is None:
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
