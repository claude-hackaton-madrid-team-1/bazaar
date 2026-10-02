"""Masking for text that leaves the machine for the Jev judge.

Python port of `vendor/jev-sdk/src/mask.ts` plus the redaction list it reuses from
`public-text-sanitizer.ts`. Masking is pure and total: the masked text is what the request sends
AND what the decision log stores. The raw text is never written anywhere.

The regexes are translated from JavaScript `u`-flag patterns. JavaScript `\\b` and `\\w` are ASCII
there, so every pattern compiles with `re.ASCII`; JavaScript `\\s` is Unicode whitespace, so it is
spelled out as `_WS` instead of relying on either engine's default. The JSON helpers at the bottom
write exactly what `JSON.stringify` writes, so a digest computed here matches the upstream one.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from collections.abc import Iterable, Mapping
from decimal import Decimal

type JsonValue = None | bool | int | float | str | list[JsonValue] | dict[str, JsonValue]

JEV_REDACTION = "[redacted]"
JEV_QUESTION_TYPES = ("noul", "choice", "score")
JEV_STAKES = ("passive", "design", "critical")

# ECMAScript WhiteSpace + LineTerminator, which is what `\s` and `String.prototype.trim` use.
_WS = r"\t\n\v\f\r \u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000\ufeff"
_WS_NOT_NEWLINE = r"\t\v\f\r \u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000\ufeff"
JS_WHITESPACE = (
    "\t\n\v\f\r \u00a0\u1680"
    + "".join(chr(code) for code in range(0x2000, 0x200B))
    + "\u2028\u2029\u202f\u205f\u3000\ufeff"
)
_S = f"[{_WS}]"
_NS = f"[^{_WS}]"

_CI = re.ASCII | re.IGNORECASE

# Credential-bearing JSON fields are masked before inspecting their values.
_CREDENTIAL_FIELD_NAME = re.compile(
    # Anywhere in the name, not only at the end: `token_value` and `client_secret_id` hold credentials too.
    r"(?:api[_-]?key|secret|token|password|passphrase|private[_-]?key|signing[_-]?key|authorization|credential|cookie)",
    _CI,
)

# A credential key may carry a prefix (`client_secret`, `refreshToken`) and sit inside JSON quotes,
# escaped or not, before its separator and its value.
_CREDENTIAL_KEY_VALUE = (
    r"\b[a-z0-9_-]*(?:api[_-]?key|secret|token|password|passphrase|private[_-]?key|signing[_-]?key)"
    rf"[\"'\\]*{_S}*[:=]{_S}*[\"'\\]*[^{_WS}\"',;\\]+"
)

# The shared list from public-text-sanitizer.ts, in its order. `$` there is end of input, so `\Z`.
_PROHIBITED_PUBLIC_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "private key",
        re.compile(r"-----BEGIN ([A-Z ]*PRIVATE KEY)-----[\s\S]*?(?:-----END \1-----|\Z)", _CI),
    ),
    ("account id", re.compile(r"\bacct_(?:[a-z0-9_-]+|…+|\.\.\.+)", _CI)),
    ("party id", re.compile(r"\b[A-Za-z0-9][A-Za-z0-9._-]*::[A-Za-z0-9][A-Za-z0-9._-]*", re.ASCII)),
    (
        "contract id",
        re.compile(r"\b(?:00[a-f0-9]{40,}|(?:cid|contract(?:_?id)?)[_:=/-][a-z0-9][a-z0-9._:/+-]{7,})\b", _CI),
    ),
    (
        "internal hostname",
        re.compile(
            r"\b(?:localhost(?::[0-9]{2,5})?|(?:10|127)\.[0-9.]+(?::[0-9]{2,5})?"
            r"|192\.168\.[0-9.]+(?::[0-9]{2,5})?|172\.(?:1[6-9]|2[0-9]|3[01])\.[0-9.]+(?::[0-9]{2,5})?"
            r"|(?:[a-z0-9-]+\.)+(?:internal|local))\b",
            _CI,
        ),
    ),
    (
        "database name",
        re.compile(rf"\b(?:(?:database|db)(?:_?name)?{_S}*[:=]{_S}*[\"']?[a-z][a-z0-9_-]{{5,}})\b", _CI),
    ),
    (
        "command id",
        re.compile(
            rf"\b(?:cmd_[a-z0-9][a-z0-9_-]{{5,}}"
            rf"|command(?:_?id)?{_S}*[:=]{_S}*[\"']?[a-z0-9][a-z0-9._:-]{{7,}})\b",
            _CI,
        ),
    ),
    (
        "credential-shaped value",
        re.compile(
            rf"(?:\bBearer{_S}+[a-z0-9._~+/-]+=*"
            r"|\beyJ[a-z0-9_-]{8,}\.[a-z0-9_-]{8,}\.[a-z0-9_-]{8,}"
            r"|-----BEGIN [A-Z ]*PRIVATE KEY-----"
            rf"|{_CREDENTIAL_KEY_VALUE}"
            r"|\b(?:sk|pk)_(?:live|test|prod)_[a-z0-9_-]{8,}"
            rf"|\b[a-z][a-z0-9+.-]*://[^{_WS}/:]+:[^{_WS}/@]+@[^{_WS}/]+)",
            _CI,
        ),
    ),
)

# Values a judge input carries that a public report does not. Each one is replaced whole.
_JEV_ONLY_PATTERNS: tuple[re.Pattern[str], ...] = (
    # An e-mail address identifies a person, so it never reaches the judge.
    re.compile(r"\b[a-z0-9._%+-]+@[a-z0-9-]+(?:\.[a-z0-9-]+)+\b", _CI),
    # A connection string, with or without credentials in it.
    re.compile(
        r"\b(?:postgres(?:ql)?|redis|rediss|mysql|mongodb(?:\+srv)?|amqp|amqps|nats|grpc|grpcs)://" rf"{_NS}+",
        _CI,
    ),
    # Common API-key shapes beyond the shared sanitizer. Case-sensitive upstream (no `i` flag).
    re.compile(
        r"\b(?:[a-z][a-z0-9]{1,20}_(?:ak|bk)_[A-Za-z0-9_-]{8,}|sk-[A-Za-z0-9_-]{16,}"
        r"|gh[pousr]_[A-Za-z0-9]{16,}|github_pat_[A-Za-z0-9_]{16,}|xox[baprs]-[A-Za-z0-9-]{10,}"
        r"|tskey-[A-Za-z0-9-]{10,})\b",
        re.ASCII,
    ),
)

_CONTROL = re.compile(r"[\x00-\x09\x0b-\x1f\x7f]")
_SPACE_RUN = re.compile(f"[{_WS_NOT_NEWLINE}]+")
_LONE_SURROGATE = re.compile("[\ud800-\udfff]")


def redact_prohibited_values(text: str) -> str:
    """The redaction pass of the shared public-text list, alone."""
    for _label, pattern in _PROHIBITED_PUBLIC_PATTERNS:
        text = pattern.sub(JEV_REDACTION, text)
    return text


def prohibited_public_value_label(value: str) -> str | None:
    """The label of the first prohibited value in `value`, or None when it carries none."""
    for label, pattern in _PROHIBITED_PUBLIC_PATTERNS:
        if pattern.search(value) is not None:
            return label
    return None


def mask_text(text: str) -> str:
    """The masked form of one string.

    Control characters other than a newline become a space, runs of spaces and tabs collapse to one
    space, and newlines survive.
    """
    redacted = unicodedata.normalize("NFKC", text)
    for pattern in _JEV_ONLY_PATTERNS:
        redacted = pattern.sub(JEV_REDACTION, redacted)
    printable = _CONTROL.sub(" ", redact_prohibited_values(redacted))
    masked = _SPACE_RUN.sub(" ", printable).strip(JS_WHITESPACE)
    # A value the shared list still recognises means one pattern matched a shape the other left
    # behind. Fail closed on the whole string rather than ship the part that survived.
    if prohibited_public_value_label(masked) is not None:
        return JEV_REDACTION
    return masked


def _masked_key(key: str, index: int) -> str:
    """A field name is sent too: one that looks like a credential is replaced, numbered to stay unique."""
    return key if mask_text(key) == key.strip() or not key.strip() else f"{JEV_REDACTION}#{index}"


def mask_state(state: object) -> JsonValue:
    """The masked form of a state: every string is masked, every number and boolean is kept."""
    if state is None or isinstance(state, bool | int | float):
        return state  # type: ignore[return-value]
    if isinstance(state, str):
        return mask_text(state)
    if isinstance(state, list | tuple):
        return [mask_state(entry) for entry in state]
    if isinstance(state, Mapping):
        return {
            _masked_key(str(key), index): (
                JEV_REDACTION if _CREDENTIAL_FIELD_NAME.search(str(key)) else mask_state(entry)
            )
            for index, (key, entry) in enumerate(state.items())
        }
    raise TypeError(f"a Jev state holds JSON values only, not {type(state).__name__}")


def mask_questions(questions: Mapping[str, Mapping[str, object]]) -> dict[str, dict[str, JsonValue]]:
    """The masked form of a question map. `type` is kept as written and `stakes` is dropped."""
    masked: dict[str, dict[str, JsonValue]] = {}
    for question_id, question in questions.items():
        question_type = question.get("type")
        entry: dict[str, JsonValue] = {
            "type": question_type if isinstance(question_type, str) else None,
            "instructions": mask_state(question.get("instructions")),
        }
        if "criteria" in question:
            entry["criteria"] = mask_state(question["criteria"])
        masked[question_id] = entry
    return masked


def masked_digest(masked: str) -> str:
    """The sha256 hex of masked text, for a log line that identifies a state without holding it."""
    # Node encodes a lone surrogate as U+FFFD; match it rather than raise.
    return hashlib.sha256(_LONE_SURROGATE.sub("\ufffd", masked).encode("utf-8")).hexdigest()


def utf16_length(text: str) -> int:
    """`String.prototype.length`: UTF-16 code units, which is what the upstream budget counts."""
    return len(text.encode("utf-16-le", "surrogatepass")) // 2


def js_number(value: float) -> str:
    """`String(number)` for a finite number, so a score or a digest reads the same as upstream."""
    if isinstance(value, int):
        return str(value)
    if value == 0:
        return "0"
    if value.is_integer() and abs(value) < 1e21:
        return str(int(value))
    sign = "-" if value < 0 else ""
    _, digit_tuple, exponent = Decimal(repr(abs(value))).as_tuple()
    digits = "".join(str(digit) for digit in digit_tuple)
    count, point = len(digits), int(exponent) + len(digits)
    if count <= point <= 21:
        return sign + digits + "0" * (point - count)
    if 0 < point <= 21:
        return f"{sign}{digits[:point]}.{digits[point:]}"
    if -6 < point <= 0:
        return f"{sign}0.{'0' * -point}{digits}"
    power = point - 1
    mantissa = digits if count == 1 else f"{digits[0]}.{digits[1:]}"
    return f"{sign}{mantissa}e{'+' if power >= 0 else '-'}{abs(power)}"


def _js_string(text: str) -> str:
    written = json.dumps(text, ensure_ascii=False)
    return _LONE_SURROGATE.sub(lambda match: f"\\u{ord(match.group()):04x}", written)


def _is_array_index(key: str) -> bool:
    return key.isascii() and key.isdigit() and (key == "0" or key[0] != "0") and int(key) < 2**32 - 1


def _js_key_order(mapping: Mapping[object, object]) -> Iterable[tuple[str, object]]:
    """JavaScript object key order: array-index keys ascending, then the rest as inserted."""
    entries = [(str(key), entry) for key, entry in mapping.items()]
    indexed = sorted((entry for entry in entries if _is_array_index(entry[0])), key=lambda e: int(e[0]))
    return [*indexed, *(entry for entry in entries if not _is_array_index(entry[0]))]


def js_json_dumps(value: object) -> str:
    """`JSON.stringify(value)` for JSON-shaped Python values: compact, JS key order, JS numbers."""
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return js_number(value) if math.isfinite(value) else "null"
    if isinstance(value, str):
        return _js_string(value)
    if isinstance(value, list | tuple):
        return "[" + ",".join(js_json_dumps(entry) for entry in value) + "]"
    if isinstance(value, Mapping):
        pairs = (f"{_js_string(key)}:{js_json_dumps(entry)}" for key, entry in _js_key_order(value))
        return "{" + ",".join(pairs) + "}"
    raise TypeError(f"cannot write {type(value).__name__} as JSON")
