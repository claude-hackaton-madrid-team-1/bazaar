"""The limits every request meets (RULES.md, "Limits every request meets").

Bodies are strict JSON up to 64 KB, at most 8 levels deep, finite numbers below 10^12. Prices and
cash are whole primas from 1 to 10,000,000, at most 50 items per offer side. Text loses control,
invisible and direction-changing characters and keeps 1,200 characters (venue names 40).
"""

from __future__ import annotations

import json
import re
import unicodedata
from typing import Any

from bazaar_sim.errors import SimError, invalid

MAX_BODY_BYTES = 64 * 1024
MAX_DEPTH = 8
MAX_NUMBER = 10**12
MIN_PRICE, MAX_PRICE = 1, 10_000_000
MAX_ITEMS = 50
MESSAGE_CHARS = 1200
VENUE_NAME_CHARS = 40
TOPIC_JSON_CHARS = 600
TOPIC_DEPTH = 4
CARD_REF = re.compile(r"^[A-Z]{3}-\d{2}$")
_INVISIBLE = {"Cc", "Cf", "Co", "Cs"}  # control, format (zero-width, bidi), private use, surrogates


def _reject_constant(name: str) -> float:
    raise ValueError(f"{name} is not allowed")


def parse_body(raw: bytes) -> dict[str, Any]:
    """The request body as a dict; an empty body is `{}`. Refusals: 413 too large, 400 not strict JSON."""
    if len(raw) > MAX_BODY_BYTES:
        raise SimError("too_large", f"body over {MAX_BODY_BYTES} bytes", 413)
    if not raw.strip():
        return {}
    try:
        value = json.loads(raw, parse_constant=_reject_constant)
    except (ValueError, UnicodeDecodeError) as e:
        raise invalid(f"body is not strict JSON ({str(e)[:80]})") from None
    if not isinstance(value, dict):
        raise invalid("body must be a JSON object")
    check_shape(value)
    return value


def depth(node: Any) -> int:
    if isinstance(node, dict):
        return 1 + max((depth(v) for v in node.values()), default=0)
    if isinstance(node, list):
        return 1 + max((depth(v) for v in node), default=0)
    return 0


def check_shape(value: Any) -> None:
    if depth(value) > MAX_DEPTH:
        raise invalid(f"body nests deeper than {MAX_DEPTH} levels")
    for number in _numbers(value):
        if abs(number) >= MAX_NUMBER:
            raise invalid("numbers must be below 10^12")


def _numbers(node: Any) -> list[float]:
    if isinstance(node, bool):
        return []
    if isinstance(node, int | float):
        return [float(node)]
    if isinstance(node, dict):
        return [n for v in node.values() for n in _numbers(v)]
    if isinstance(node, list):
        return [n for v in node for n in _numbers(v)]
    return []


def clean_text(value: Any, limit: int = MESSAGE_CHARS) -> str:
    """Cleaned, never refused: control, invisible and direction-changing characters go, then the cut."""
    if value is None:
        return ""
    if not isinstance(value, str):
        raise invalid("text must be a string")
    kept = "".join(ch for ch in value if ch in "\n\t" or unicodedata.category(ch) not in _INVISIBLE)
    return kept[:limit]


def price(value: Any, what: str = "price") -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise invalid(f"{what} must be a whole number of primas")
    if not MIN_PRICE <= value <= MAX_PRICE:
        raise invalid(f"{what} must be between {MIN_PRICE} and {MAX_PRICE:,}")
    return value


def optional_price(value: Any, what: str = "price") -> int | None:
    return None if value is None else price(value, what)


def int_list(value: Any, what: str) -> list[int]:
    if value is None:
        return []
    if not isinstance(value, list) or any(isinstance(v, bool) or not isinstance(v, int) for v in value):
        raise invalid(f"{what} must be a list of integer ids")
    if len(value) > MAX_ITEMS:
        raise invalid(f"at most {MAX_ITEMS} items on one side of an offer")
    if len(set(value)) != len(value):
        raise invalid(f"{what} repeats an id")
    return list(value)


def card_refs(value: Any, what: str = "cards") -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(v, str) or not CARD_REF.match(v) for v in value):
        raise invalid(f"{what} must be a list of card refs like LAV-03")
    if len(value) > MAX_ITEMS:
        raise invalid(f"at most {MAX_ITEMS} items on one side of an offer")
    return list(value)


def side_input(value: Any, what: str) -> tuple[int, list[int], list[str]]:
    """(cash, asset ids, card refs) of one side of a new offer: `{"cash", "assets", "cards"}`."""
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise invalid(f"{what} must be an object")
    cash_raw = value.get("cash")
    cash = 0 if cash_raw in (None, 0) else price(cash_raw, f"{what}.cash")
    assets = int_list(value.get("assets"), f"{what}.assets")
    cards = card_refs(value.get("cards"), f"{what}.cards")
    if len(assets) + len(cards) > MAX_ITEMS:
        raise invalid(f"at most {MAX_ITEMS} items on one side of an offer")
    return cash, assets, cards


def topic_between_teams(topic: Any) -> dict[str, Any] | None:
    if topic is None:
        return None
    if not isinstance(topic, dict):
        raise invalid("topic must be an object")
    if len(json.dumps(topic, ensure_ascii=False)) > TOPIC_JSON_CHARS or depth(topic) > TOPIC_DEPTH:
        raise invalid(
            f"a thread topic between teams is at most {TOPIC_JSON_CHARS} characters of JSON, {TOPIC_DEPTH} levels"
        )
    return topic


def ticks(value: Any, default: int, lo: int = 1, hi: int = 1000) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi:
        raise invalid(f"expires_in_ticks must be a whole number from {lo} to {hi}")
    return value
