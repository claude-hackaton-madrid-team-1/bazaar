"""Two-issue duels (Duels II): when to trust the sign of `your_days_weight`, what the rival's days say, and which
delivery day to offer. Night backlog B8. Pure functions over `/api/duels` rows; nothing here sends anything.

The sign. RULES.md only says each side has "a private weight per day"; the official openapi says "Primas per
delivery day". The simulator's `days_meaning` reads "primas you gain (+) or lose (-) per delivery day", but the
simulator is not evidence: Friday's real practice payloads had `days_meaning: null`. Until a REAL payload says
which way the weight points, every day must be valued at the worst case (|weight| against us, PR #60).
`DaysSwitch` latches the first real evidence:

    signed   the text ties a gain to "(+)" / "positive" (the simulator's wording, but from the real game)
    reversed the text ties a cost to "(+)": the opposite of v2's convention, so the switch stays off
    cost     the text only speaks of a cost per day: the worst case is the truth, keep it
    unknown  no text (null), any text read from the simulator, or a text with no sign tied to a gain or a cost
    conflict two real payloads disagree: never signed again (the safe side)

It persists to a small JSON file, so a restart keeps the verdict. The caller passes `real_game` (from
`Settings.simulator`), and decides whether the latch may switch anything on (a guardrail, default off).

The rival's days. Whatever the sign convention, the days a rival puts in its own offers show which end it
prefers. `rival_days` reads that preference (and a rough per-day weight when its price and days move together).
A rival that always sends 0 may simply not handle days, so a 0 preference counts for less than a 10.

Our days. Worst case: always 0, as #60 (every day may cost us). Signed: the end that maximises our weight plus
the rival's estimated one, priced so OUR value stays where the policy put it (`reprice`), never outside our limit.
"""

from __future__ import annotations

import json
import math
import re
import statistics
from collections.abc import Callable, Iterable, Mapping
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Literal

Verdict = Literal["signed", "reversed", "cost", "unknown", "conflict"]
DAYS_MAX = 10  # RULES.md: delivery day 0 to 10
PRIOR_WEIGHT = 2.0  # E|weight| when weights are uniform on -4..4 (the simulator's draw): a rival's unknown magnitude
_GAIN, _LOSS = r"(?:gain|earn)\w*", r"(?:lose|loss|cost|pay)\w*"
_PLUS = r"\(\s*\+\s*\)"
PLUS_GAIN = re.compile(rf"{_GAIN}\s*{_PLUS}|positive\W+(?:\w+\W+){{0,3}}?{_GAIN}", re.IGNORECASE)
PLUS_LOSS = re.compile(rf"{_LOSS}\s*{_PLUS}|positive\W+(?:\w+\W+){{0,3}}?{_LOSS}", re.IGNORECASE)
LOSS = re.compile(_LOSS, re.IGNORECASE)
GAIN = re.compile(_GAIN, re.IGNORECASE)


def _number(value: object) -> float | None:
    if not isinstance(value, int | float) or isinstance(value, bool) or not math.isfinite(value):
        return None
    return float(value)


def two_issue(duel: Mapping[str, Any]) -> bool:
    return "days" in (duel.get("issues") or [])


# ---------------------------------------------------------------- the sign of our weight


def evidence(duel: Mapping[str, Any], real_game: bool) -> Verdict:
    """What one payload says about the sign of `your_days_weight`, in v2's convention (a positive weight is primas
    WE gain per day: `duel_v2.value_of`, the guard, the simulator's utility). Only a real two-issue payload counts.

    signed    a gain is tied to "(+)" or "positive" ("gain (+) or lose (-)", the simulator's words)
    reversed  a cost or loss is tied to "(+)" or "positive": the opposite convention, so the switch stays off
    cost      only a cost or loss, no gain: every day costs, the worst case is the truth
    unknown   anything else, including a gain and a loss with no sign tied to either
    """
    text = duel.get("days_meaning")
    if not real_game or not two_issue(duel) or not isinstance(text, str) or not text.strip():
        return "unknown"
    plus_gain, plus_loss = bool(PLUS_GAIN.search(text)), bool(PLUS_LOSS.search(text))
    if plus_gain != plus_loss:
        return "signed" if plus_gain else "reversed"
    if LOSS.search(text) and not GAIN.search(text):
        return "cost"
    return "unknown"


@dataclass
class DaysSwitch:
    """The first real evidence about the sign, latched and persisted. `signed(allowed)` is what a policy reads."""

    verdict: Verdict = "unknown"
    duel: int | None = None  # the payload that decided it
    text: str | None = None
    path: Path | None = None

    @classmethod
    def load(cls, path: Path) -> DaysSwitch:
        try:
            raw = json.loads(path.read_text())
        except (OSError, ValueError):
            return cls(path=path)
        verdicts: dict[str, Verdict] = {
            "signed": "signed",
            "reversed": "reversed",
            "cost": "cost",
            "conflict": "conflict",
        }
        verdict = verdicts.get(str(raw.get("verdict")), "unknown")
        return cls(verdict=verdict, duel=raw.get("duel"), text=raw.get("text"), path=path)

    def observe(self, duels: Iterable[Mapping[str, Any]], real_game: bool) -> Verdict:
        """Read every payload: the first real evidence latches; a later real payload that disagrees is a conflict."""
        for duel in duels:
            seen = evidence(duel, real_game)
            if seen == "unknown" or self.verdict == "conflict":
                continue
            if self.verdict == "unknown":
                self.verdict, self.duel, self.text = seen, duel.get("duel"), duel.get("days_meaning")
                self._save()
            elif seen != self.verdict:
                self.verdict = "conflict"
                self._save()
        return self.verdict

    def signed(self, allowed: bool) -> bool:
        """Value days with their sign only when allowed (a guardrail) AND a real payload said so."""
        return allowed and self.verdict == "signed"

    def _save(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        record = {k: v for k, v in asdict(self).items() if k != "path"}
        self.path.write_text(json.dumps(record, ensure_ascii=False))


# ---------------------------------------------------------------- the rival's days


@dataclass(frozen=True)
class RivalDays:
    prefers: int | None  # the end it leans to: 10 or 0; None without evidence (or when it sits in the middle)
    weight: float | None  # a rough |weight| (P per day) from price and days moving together; None if not seen
    confidence: float  # 0..1
    offers: int


def rival_days(duel: Mapping[str, Any]) -> RivalDays:
    """The rival's preferred end of 0-10 from its own priced offers. A 0 counts half: a rival that does not
    handle days may send 0 whatever its weight."""
    rival = duel.get("rival")
    offers = [
        (int(m["tick"]), float(p), int(d))
        for m in duel.get("messages") or []
        if m.get("from") == rival
        and (p := _number(m.get("price"))) is not None
        and isinstance(d := m.get("days"), int)
        and not isinstance(d, bool)
    ]
    if not offers:
        return RivalDays(None, None, 0.0, 0)
    mean = statistics.fmean(d for _, _, d in offers)
    prefers = DAYS_MAX if mean >= 7 else 0 if mean <= 3 else None
    side = [d for _, _, d in offers if (d >= 7 if prefers == DAYS_MAX else d <= 3)] if prefers is not None else []
    confidence = min(1.0, len(offers) / 3) * (len(side) / len(offers)) * (0.5 if prefers == 0 else 1.0)
    trades = [
        abs((p2 - p1) / (d2 - d1)) for (_, p1, d1), (_, p2, d2) in zip(offers, offers[1:], strict=False) if d2 != d1
    ]
    weight = statistics.median(trades) if trades else None
    return RivalDays(prefers, weight, round(confidence, 3), len(offers))


# ---------------------------------------------------------------- our days


def value(duel: Mapping[str, Any], price: float, days: int, signed: bool) -> float | None:
    """A price with its days as a price for us: signed (+ weight = a gain per day) or the worst case (#60)."""
    if not two_issue(duel):
        return float(price)
    weight = _number(duel.get("your_days_weight"))
    if weight is None:
        return None
    shift = weight * days if signed else -abs(weight) * days  # what the days add to our side of the deal
    return price + shift if duel.get("role") == "seller" else price - shift


def inside(duel: Mapping[str, Any], worth: float) -> bool:
    limit = duel["your_limit"]
    return worth > limit if duel.get("role") == "seller" else worth < limit


def choose_days(duel: Mapping[str, Any], signed: bool, rival: RivalDays, prior: float = PRIOR_WEIGHT) -> int | None:
    """Our delivery day. Worst case: 0. Signed: the end where our weight plus the rival's (its preference, at its
    estimated magnitude or the prior, scaled by our confidence in it) is larger. None in a price-only duel."""
    if not two_issue(duel):
        return None
    weight = _number(duel.get("your_days_weight"))
    if not signed or weight is None:
        return 0
    theirs = 0.0
    if rival.prefers is not None:
        size = (rival.weight if rival.weight is not None else prior) * rival.confidence
        theirs = size if rival.prefers == DAYS_MAX else -size
    return DAYS_MAX if weight + theirs > 0 else 0


def reprice(duel: Mapping[str, Any], price: int, days_from: int, days_to: int, signed: bool) -> int | None:
    """The price at `days_to` that keeps our value of (`price`, `days_from`), rounded to our side. None when the
    result is not strictly inside our limit or not a valid price."""
    target = value(duel, price, days_from, signed)
    at_zero = value(duel, 0, days_to, signed)
    if target is None or at_zero is None:
        return None
    raw = target - at_zero  # value(p, d) = p + value(0, d): solve for p
    new = math.ceil(raw - 1e-9) if duel.get("role") == "seller" else math.floor(raw + 1e-9)
    worth = value(duel, new, days_to, signed)
    if new < 1 or worth is None or not inside(duel, worth):
        return None
    return new


def days_aware(
    policy: Callable[[dict[str, Any], int, int], Any], signed: bool
) -> Callable[[dict[str, Any], int, int], Any]:
    """Wrap a duel policy: its priced offers go out at `choose_days`, repriced to keep its value for us. Accepts,
    holds and price-only duels pass through. A move that cannot be repriced inside our limit is kept as it was."""

    def wrapped(duel: dict[str, Any], tick: int, started_tick: int) -> Any:
        move = policy(duel, tick, started_tick)
        if getattr(move, "kind", None) != "offer" or not two_issue(duel) or move.price is None:
            return move
        days = choose_days(duel, signed, rival_days(duel))
        current = move.days if isinstance(move.days, int) else 0
        if days is None or days == current:
            return move
        price = reprice(duel, int(move.price), current, days, signed)
        return move if price is None else replace(move, price=price, days=days)

    return wrapped
