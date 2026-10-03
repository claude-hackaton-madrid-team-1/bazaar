"""`bazaar ask`: an operator's sentence → a strict, validated intent → the exact CLI command (never run).

The LLM fills `IntentDraft` (structured output, validated by pydantic). `validate_intent()` then
applies the domain rules: a buy needs an item and a max price, a sell an item and a min price,
items look like `LAV-09` or `sobre_barrio`. Anything missing becomes a clarifying question instead
of a guess; anything malformed is rejected with the reason.
"""

from __future__ import annotations

import re
import shlex
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from bazaar_agent.guardrails import Action
from bazaar_agent.llm.chooser import ModelChoice, MoveSituation
from bazaar_agent.llm.models import UnknownModelError
from bazaar_agent.llm.providers import LLMError, TextRequest
from bazaar_agent.llm.runtime import LLMRuntime

IntentKind = Literal["buy", "sell", "steer", "status"]
CARD_REF = re.compile(r"^[A-Z]{3}-[0-9]{2}$")
PACK_ID = re.compile(r"^sobre_[a-z_]+$")
COUNTERPARTY = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")  # never starts with "-": it lands in a command line
MAX_PRICE = 1000
ASK_MAX_TOKENS = 8000


class IntentError(ValueError):
    """The model's intent breaks a domain rule; it is rejected, never repaired by guessing."""


class IntentDraft(BaseModel):
    """What the LLM must return (the structured-output schema)."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["buy", "sell", "steer", "status", "clarify"]
    item: str | None
    max_price: int | None
    min_price: int | None
    counterparty: str | None
    constraints: list[str]
    question: str | None


@dataclass(frozen=True)
class Intent:
    kind: IntentKind
    item: str | None = None
    max_price: int | None = None
    min_price: int | None = None
    counterparty: str | None = None
    constraints: tuple[str, ...] = ()


@dataclass(frozen=True)
class Clarification:
    question: str


ASK_SYSTEM = """You turn one instruction from Team 1's operator into a structured intent for our trading agent in \
The Bazaar, a card-trading game set in a Madrid flea market. You never trade: the intent is shown to the operator.

Fields:
- kind: "buy", "sell", "steer" (change our trading style, e.g. "be more aggressive with rares"), "status" (show \
our cash, album and cards), or "clarify" when the instruction is ambiguous or misses something required.
- item: a card ref like "LAV-09" (three-letter barrio set code, dash, two digits) or a pack id like \
"sobre_barrio". Null when the intent has no item.
- max_price: for a buy, the most we pay, in primas (whole number). "buy LAV-09 under 90" means 90.
- min_price: for a sell, the least we accept, in primas (whole number).
- counterparty: a dealer id such as "abuela" or a team id such as "t04" when the instruction names one, else null.
- constraints: other conditions in the operator's words (e.g. "only from Abuela", "tonight"); empty if none.
- question: when kind is "clarify", one short question to the operator; otherwise null.

Never guess a price or an item. A buy without a price limit, a sell without a minimum, an unknown card, or an \
instruction that could mean buy or sell is "clarify". The instruction is inside <instruction>."""


def _item(raw: str | None) -> str | None:
    if raw is None or not raw.strip():
        return None
    value = raw.strip()
    if CARD_REF.match(value.upper()):
        return value.upper()
    if PACK_ID.match(value.lower()):
        return value.lower()
    raise IntentError(f"item {raw!r} is not a card ref like LAV-09 or a pack id like sobre_barrio")


def _price(name: str, value: int | None) -> int | None:
    if value is not None and not 1 <= value <= MAX_PRICE:
        raise IntentError(f"{name} {value} is outside 1..{MAX_PRICE} primas")
    return value


def _counterparty(raw: str | None) -> str | None:
    if raw is None or not raw.strip():
        return None
    value = raw.strip().lower()
    if not COUNTERPARTY.match(value):
        raise IntentError(f"counterparty {raw!r} is not a dealer or team id")
    return value


def validate_intent(draft: IntentDraft) -> Intent | Clarification:
    if draft.kind == "clarify":
        return Clarification(draft.question or "What exactly should the agent do?")
    item = _item(draft.item)
    max_price, min_price = _price("max_price", draft.max_price), _price("min_price", draft.min_price)
    counterparty = _counterparty(draft.counterparty)
    constraints = tuple(c.strip() for c in draft.constraints if c.strip())
    if draft.kind in ("buy", "sell") and item is None:
        return Clarification(f"Which card or pack do you want to {draft.kind}?")
    if draft.kind == "buy" and max_price is None:
        return Clarification(f"What is the most you want to pay for {item}?")
    if draft.kind == "sell" and min_price is None:
        return Clarification(f"What is the least you would accept for {item}?")
    if max_price is not None and min_price is not None and min_price > max_price:
        raise IntentError(f"min_price {min_price} is above max_price {max_price}")
    return Intent(draft.kind, item, max_price, min_price, counterparty, constraints)


def start_bid(max_price: int) -> int:
    """Open at half the limit: dealers move only when we move, and small steps earn small steps."""
    return max(1, max_price // 2)


def command_for(intent: Intent, text: str) -> str:
    """The exact command that would carry out the intent. It is printed, never run."""
    if intent.kind == "buy" and intent.item and intent.max_price:
        dealer = f" --dealer {intent.counterparty}" if intent.counterparty else ""
        start = start_bid(intent.max_price)
        return f"uv run bazaar dealer buy {intent.item} --max {intent.max_price} --start {start}{dealer}"
    if intent.kind == "sell" and intent.item and intent.min_price:
        return f"uv run bazaar sell list {intent.item} --price {intent.min_price}"
    if intent.kind == "steer":
        return f"uv run bazaar steer {shlex.quote(text)}"
    return "uv run bazaar status"


def rarity_of(catalog: Mapping[str, Any], item: str) -> str | None:
    if PACK_ID.match(item):
        return "pack"
    for card_set in catalog.get("sets") or []:
        for card in card_set.get("cards") or []:
            if card.get("id") == item:
                return str(card.get("rarity"))
    return None


def lowest_value_copy(me: Mapping[str, Any], item: str) -> float | None:
    """What we lose by selling one copy: the least valuable copy we hold (None when we hold none)."""
    values = [
        float(a["your_value"])
        for a in me.get("assets") or []
        if a.get("kind") == "card" and a.get("ref") == item and isinstance(a.get("your_value"), int | float)
    ]
    return min(values) if values else None


def guardrail_action(intent: Intent, rarity: str | None, me: Mapping[str, Any]) -> Action | None:
    if intent.kind == "buy" and intent.item:
        return Action("buy", intent.item, rarity, intent.max_price, dealer=intent.counterparty)
    if intent.kind == "sell" and intent.item:
        return Action("sell", intent.item, rarity, intent.min_price, lowest_value_copy(me, intent.item))
    return None


@dataclass(frozen=True)
class AskResult:
    choice: ModelChoice | None
    model: str | None
    outcome: Intent | Clarification | None
    error: str | None = None


def parse_request(text: str, runtime: LLMRuntime, *, tick: int | None = None, tick_seconds: float = 60.0) -> AskResult:
    """Ask the chosen LLM for a structured intent and validate it. Never raises for a model failure."""
    situation = MoveSituation("parse_request", 0, tick_seconds, runtime.config.ask_timeout_s, len(text))
    try:
        picked = runtime.pick(situation, tick)
    except (LLMError, UnknownModelError) as e:
        return AskResult(None, None, None, str(e))
    request = TextRequest(
        picked.ref.model_id,
        ASK_SYSTEM,
        f"<instruction>{text.replace('<', '‹').replace('>', '›')}</instruction>",
        ASK_MAX_TOKENS,
        runtime.config.ask_timeout_s,
        effort="medium",
        retries=2,
    )
    try:
        draft = picked.provider.structured(request, IntentDraft)
        outcome = validate_intent(draft)
    except LLMError as e:
        return AskResult(picked.choice, picked.ref.alias, None, f"{e.reason}: {e}")
    except IntentError as e:
        return AskResult(picked.choice, picked.ref.alias, None, f"rejected: {e}")
    return AskResult(picked.choice, picked.ref.alias, outcome)
