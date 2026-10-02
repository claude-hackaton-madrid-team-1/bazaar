"""`bazaar steer`: an operator's style instruction → bounded parameter deltas with an expiry in ticks.

The LLM may only name parameters in `STEERABLE` and propose deltas. `clamp()` keeps every steered
value within `steer_max_change` of its base (GUARDRAILS.md) and inside the parameter's hard range;
the duel anchor never drops below the floor margin. Steering lives in `.local/steering.json`, one
at a time (a new one replaces the old), and expires at a game tick, never at a wall-clock time.
Price caps, the cash floor and every other guardrail are never steerable; `duel_floor_margin` (a
safety margin) may only be tightened. `duel run` applies the duel parameters every tick, and
`bazaar strategy` applies the STRATEGY.md ones when it ranks moves.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from bazaar_agent.guardrails import Guardrails, GuardrailsError
from bazaar_agent.llm.chooser import ModelChoice, MoveSituation
from bazaar_agent.llm.intent import Clarification
from bazaar_agent.llm.models import UnknownModelError
from bazaar_agent.llm.providers import LLMError, TextRequest
from bazaar_agent.llm.runtime import LLMRuntime
from bazaar_agent.strategy import STRATEGY_FILE, StrategyParams, load_strategy

STEERING_FILE = "steering.json"
STEER_MAX_TOKENS = 8000

SteerParam = Literal[
    "scarcity_weight",
    "page_bonus_weight",
    "min_buy_surplus",
    "sell_need_share",
    "sell_min_surplus",
    "duel_anchor",
    "duel_floor_margin",
]


@dataclass(frozen=True)
class Bound:
    low: float
    high: float
    integer: bool
    source: str  # where the base value lives
    meaning: str
    used_by: str  # the code that reads the steered value
    tighten_only: bool = False  # a safety margin: steering may raise it, never lower it


USED_BY_STRATEGY = "bazaar strategy"
STEERABLE: Mapping[str, Bound] = {
    "scarcity_weight": Bound(
        0.0, 3.0, False, "STRATEGY.md", "how strongly scarcity raises a move's priority", USED_BY_STRATEGY
    ),
    "page_bonus_weight": Bound(
        0.0, 2.0, False, "STRATEGY.md", "how much of a missing card's page bonus counts", USED_BY_STRATEGY
    ),
    "min_buy_surplus": Bound(
        0,
        20,
        True,
        "STRATEGY.md",
        "buy only when value beats price by this many primas (lower = bolder)",
        USED_BY_STRATEGY,
    ),
    "sell_need_share": Bound(
        0.3, 1.0, False, "STRATEGY.md", "ask a buyer this share of the card's value to them", USED_BY_STRATEGY
    ),
    "sell_min_surplus": Bound(
        0, 30, True, "STRATEGY.md", "sell only when price beats our value by this many primas", USED_BY_STRATEGY
    ),
    "duel_anchor": Bound(
        0.1, 1.0, False, "GUARDRAILS.md", "open a duel this far beyond our limit (higher = tougher)", "duel run"
    ),
    "duel_floor_margin": Bound(
        0.0, 0.3, False, "GUARDRAILS.md", "never settle closer than this to our limit (tighten only)", "duel run", True
    ),
}
# The committed STRATEGY.md values, used only when that file is missing or invalid.
STRATEGY_DEFAULTS: Mapping[str, float] = {
    "scarcity_weight": 1.0,
    "page_bonus_weight": 1.0,
    "min_buy_surplus": 2,
    "sell_need_share": 0.6,
    "sell_min_surplus": 5,
}


class SteerDelta(BaseModel):
    model_config = ConfigDict(extra="forbid")

    param: SteerParam
    delta: float = Field(allow_inf_nan=False)


class SteerDraft(BaseModel):
    """What the LLM must return (the structured-output schema)."""

    model_config = ConfigDict(extra="forbid")

    deltas: list[SteerDelta]
    ttl_ticks: int
    summary: str
    question: str | None


@dataclass(frozen=True)
class Steering:
    text: str
    summary: str
    deltas: Mapping[str, float]
    created_tick: int
    expires_tick: int
    model: str

    def active(self, tick: int) -> bool:
        return self.created_tick <= tick < self.expires_tick


# ---------------------------------------------------------------- base values and the clamp


def base_params(rules: Guardrails, strategy_path: Path = STRATEGY_FILE) -> dict[str, float]:
    """Every steerable parameter's current value: STRATEGY.md (or its defaults) and GUARDRAILS.md."""
    values = dict(STRATEGY_DEFAULTS)
    try:
        loaded = load_strategy(strategy_path).params
        values = {name: float(getattr(loaded, name)) for name in STRATEGY_DEFAULTS}
    except GuardrailsError:
        pass  # `bazaar strategy` reports a bad STRATEGY.md itself; steering previews the defaults
    return {**values, "duel_anchor": rules.duel_anchor, "duel_floor_margin": rules.duel_floor_margin}


def clamp(param: str, base: float, delta: float, rules: Guardrails) -> float:
    """base + delta, kept within `steer_max_change` × base and the parameter's hard range.

    A safety margin only tightens; a whole-number parameter is rounded inside the fence, never out
    of it; a non-finite delta changes nothing.
    """
    bound = STEERABLE[param]
    if not math.isfinite(delta):
        return base
    reach = rules.steer_max_change * max(abs(base), 1.0 if bound.integer else 0.1)
    low, high = max(bound.low, base - reach), min(bound.high, base + reach)
    if bound.tighten_only:
        low = max(low, base)
    value = min(max(base + delta, low), high)
    if bound.integer:
        return float(min(max(round(value), math.ceil(low)), math.floor(high)))
    return round(value, 4)


def apply_steering(
    params: Mapping[str, float], steering: Steering | None, tick: int, rules: Guardrails
) -> dict[str, float]:
    """A new dict of parameters with the active steering applied and clamped; unchanged when expired."""
    result = dict(params)
    if steering is None or not steering.active(tick):
        return result
    for param, delta in steering.deltas.items():
        if param in STEERABLE and param in result:
            result[param] = clamp(param, float(params[param]), float(delta), rules)
    if "duel_anchor" in result and "duel_floor_margin" in result:
        result["duel_anchor"] = max(result["duel_anchor"], result["duel_floor_margin"])
    return result


def steered_duel_params(rules: Guardrails, steering_path: Path, tick: int) -> tuple[float, float]:
    """(anchor, floor margin) for `duel run` at this tick: GUARDRAILS.md with any active steering applied."""
    base = {"duel_anchor": rules.duel_anchor, "duel_floor_margin": rules.duel_floor_margin}
    steered = apply_steering(base, load_steering(steering_path), tick, rules)
    return steered["duel_anchor"], steered["duel_floor_margin"]


def steered_strategy_params(
    params: StrategyParams, rules: Guardrails, steering_path: Path, tick: int
) -> StrategyParams:
    """STRATEGY.md parameters with any active steering applied and clamped (a new, frozen copy)."""
    base = {name: float(getattr(params, name)) for name in STRATEGY_DEFAULTS}
    steered = apply_steering(base, load_steering(steering_path), tick, rules)
    changed = {name: value for name, value in steered.items() if value != base[name]}
    return params.model_copy(update=changed) if changed else params


def steering_from_draft(
    draft: SteerDraft, text: str, tick: int, rules: Guardrails, model: str
) -> Steering | Clarification:
    deltas: dict[str, float] = {}
    for d in draft.deltas:
        deltas[d.param] = deltas.get(d.param, 0.0) + d.delta
    deltas = {param: delta for param, delta in deltas.items() if delta != 0}
    if not deltas:
        return Clarification(draft.question or "Which part of the style should change, and in which direction?")
    ttl = min(max(1, draft.ttl_ticks), rules.steer_max_ttl_ticks)
    return Steering(text, draft.summary.strip(), deltas, tick, tick + ttl, model)


# ---------------------------------------------------------------- storage


def save_steering(path: Path, steering: Steering) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {**asdict(steering), "deltas": dict(steering.deltas)}
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_steering(path: Path) -> Steering | None:
    """The stored steering, or None when there is none. A corrupt file is treated as none."""
    if not path.is_file():
        return None
    try:
        data: Any = json.loads(path.read_text(encoding="utf-8"))
        deltas = {str(k): float(v) for k, v in dict(data["deltas"]).items() if str(k) in STEERABLE}
        if not all(math.isfinite(v) for v in deltas.values()):
            return None
        return Steering(
            str(data["text"]),
            str(data["summary"]),
            deltas,
            int(data["created_tick"]),
            int(data["expires_tick"]),
            str(data["model"]),
        )
    except (ValueError, KeyError, TypeError):
        return None


def clear_steering(path: Path) -> bool:
    if path.is_file():
        path.unlink()
        return True
    return False


# ---------------------------------------------------------------- the LLM call


STEER_SYSTEM = """You map one style instruction from Team 1's operator to small, bounded changes of our trading \
agent's parameters in The Bazaar, a card-trading game set in a Madrid flea market. You never trade.

Return:
- deltas: the parameters to change and by how much (a positive delta raises the value). Use only the parameters \
listed in the request, change only those the instruction is about, and keep each change modest: the runtime \
clamps every value to its allowed range anyway.
- ttl_ticks: how many game ticks the change should last, from what the instruction implies (the request gives \
the tick length and the local time; the venue closes at 23:00).
- summary: one sentence saying what changes and why.
- question: when the instruction is too vague to map to these parameters, return no deltas and one short \
question; otherwise null.

The instruction is inside <instruction>."""


def steer_prompt(text: str, base: Mapping[str, float], tick_seconds: float, now_label: str) -> str:
    rows = "\n".join(
        f"- {name} = {base[name]} (range {b.low}..{b.high}{', whole number' if b.integer else ''}): {b.meaning}"
        for name, b in STEERABLE.items()
        if name in base
    )
    safe = text.replace("<", "‹").replace(">", "›")
    return (
        f"Parameters (current value, allowed range, meaning):\n{rows}\n"
        f"Tick length: {tick_seconds:g} s. Local time: {now_label}.\n"
        f"<instruction>{safe}</instruction>"
    )


@dataclass(frozen=True)
class SteerResult:
    choice: ModelChoice | None
    model: str | None
    outcome: Steering | Clarification | None
    error: str | None = None


def steer_request(
    text: str,
    runtime: LLMRuntime,
    rules: Guardrails,
    base: Mapping[str, float],
    *,
    tick: int,
    tick_seconds: float,
    now_label: str,
) -> SteerResult:
    situation = MoveSituation("steer", 0, tick_seconds, runtime.config.steer_timeout_s, len(text))
    try:
        picked = runtime.pick(situation, tick)
    except (LLMError, UnknownModelError) as e:
        return SteerResult(None, None, None, str(e))
    request = TextRequest(
        picked.ref.model_id,
        STEER_SYSTEM,
        steer_prompt(text, base, tick_seconds, now_label),
        STEER_MAX_TOKENS,
        runtime.config.steer_timeout_s,
        effort="medium",
        retries=2,
    )
    try:
        draft = picked.provider.structured(request, SteerDraft)
    except LLMError as e:
        return SteerResult(picked.choice, picked.ref.alias, None, f"{e.reason}: {e}")
    return SteerResult(picked.choice, picked.ref.alias, steering_from_draft(draft, text, tick, rules, picked.ref.alias))
