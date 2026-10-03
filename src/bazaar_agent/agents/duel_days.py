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

`signed` switches anything on only when two real signals agree (a signed text and a signed score, or the scores of
two different finished deals): one misread signal must never drop the guard's worst case (#150 security review).
Both signals must come from the CURRENT session (#150 security r2 P2): `?done=true` lists every duel we ever finished,
and Saturday and Sunday bring rule variants, so an earlier session's deals never vouch for a later one. Each signal
records its payload's `session`; the current session is the highest `session` among the live duels this process
last read (never taken from the file); none known, nothing is corroborated. A conflict stays for good, every session.
It persists to a small JSON file, so a restart keeps the verdict; an unreadable file reads as a conflict.
The caller passes `real_game` (from
`Settings.simulator`), and decides whether the latch may switch anything on (a guardrail, default off).

The rival's days. Whatever the sign convention, the days a rival puts in its own offers show which end it
prefers. `rival_days` reads that preference.
A rival that always sends 0 may simply not handle days, so a 0 preference counts for less than a 10.

The latch is a local file. On Railway, `duel run` and the runtime are separate services: without a shared volume
each keeps its own, and a redeploy re-arms it. There, set `duel_days_signed` by hand once a person has read the
first real payload.

Our days. Worst case: always 0, as #60 (every day may cost us). Signed: the end that maximises our weight plus
the rival's estimated one, priced so OUR value stays where the policy put it (`reprice`), never outside our limit.
"""

from __future__ import annotations

import json
import math
import os
import re
import statistics
from collections.abc import Callable, Iterable, Mapping
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

Verdict = Literal["signed", "reversed", "cost", "unknown", "conflict"]
DAYS_MAX = 10  # RULES.md: delivery day 0 to 10
PRIOR_WEIGHT = 2.0  # E|weight| when weights are uniform on -4..4 (the simulator's draw): a rival's unknown magnitude
_GAIN = r"\b(?:gain|gains|gained|earn|earns|earned)\b"
_LOSS = r"\b(?:lose|loses|lost|loss|losses|cost|costs)\b"
_PLUS = r"\(\s*\+\s*\)"
PLUS_GAIN = re.compile(rf"{_GAIN}\s*{_PLUS}|\bpositive\W+(?:\w+\W+){{0,3}}?{_GAIN}", re.IGNORECASE)
PLUS_LOSS = re.compile(rf"{_LOSS}\s*{_PLUS}|\bpositive\W+(?:\w+\W+){{0,3}}?{_LOSS}", re.IGNORECASE)
LOSS = re.compile(_LOSS, re.IGNORECASE)
GAIN = re.compile(_GAIN, re.IGNORECASE)
YOU = re.compile(r"\b(?:you|your|yours)\b", re.IGNORECASE)  # it must speak of OUR weight
OTHER = re.compile(
    r"\b(?:buyer|buyers|seller|sellers|rival|rivals|counterparty|opponent|other side|other party|they|their|them)\b",
    re.IGNORECASE,
)
NEGATION = re.compile(r"\b(?:not|no|never|without|neither|nor)\b|n't\b", re.IGNORECASE)
# A direction or a comparative can invert "gain (+)" ("per day earlier", "earn less"): such a text is for a person to
# read, never for the latch (#150 review P1). Both directions count, so only the plain "per delivery day" form latches.
DIRECTION = re.compile(
    r"\b(?:earl(?:y|ier|iest)|soon(?:er|est)?|fast(?:er|est)?|quick(?:er|est)?|forward|advanced?|ahead|"
    r"lat(?:e|er|est)|delay(?:s|ed)?|longer|shorter|less|fewer|lower|more|extra|reduc(?:e|es|ed)|"
    r"decreas(?:e|es|ed)|increas(?:e|es|ed)|minus|inverse|invert(?:s|ed)?|opposite|revers(?:e|es|ed)|"
    r"before|after|prior|until|remain(?:s|ing)?|left|backwards?|back|counted|counting|countdown)\b",
    re.IGNORECASE,
)


def _int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _union(a: Iterable[int], b: Iterable[int]) -> list[int]:
    return sorted({*a, *b})  # sorted: two processes write the same file the same way (no rewrite ping-pong)


def _union_pairs(a: Iterable[list[int]], b: Iterable[list[int]]) -> list[list[int]]:
    return [list(pair) for pair in sorted({(s, d) for s, d in (*a, *b)})]


def _number(value: object) -> float | None:
    if not isinstance(value, int | float) or isinstance(value, bool) or not math.isfinite(value):
        return None
    return float(value)


def two_issue(duel: Mapping[str, Any]) -> bool:
    issues = duel.get("issues")
    return isinstance(issues, list | tuple) and "days" in issues  # a malformed field is not two-issue (security r2)


# ---------------------------------------------------------------- the sign of our weight


def evidence(duel: Mapping[str, Any], real_game: bool) -> Verdict:
    """What one payload says about the sign of `your_days_weight`, in v2's convention (a positive weight is primas
    WE gain per day: `duel_v2.value_of`, the guard, the simulator's utility). Only a real two-issue payload counts.

    signed    a gain is tied to "(+)" or "positive" ("gain (+) or lose (-)", the simulator's words)
    reversed  a cost or loss is tied to "(+)" or "positive": the opposite convention, so the switch stays off
    cost      only a cost or loss, no gain: every day costs, the worst case is the truth
    unknown   anything else: a gain and a loss with no sign tied to either, a text that does not speak of "you",
              names the other side (buyer, seller, rival...), negates (r1's review) or has a direction word or a
              comparative (earlier, sooner, later, less, fewer...: #150's review)
    """
    text = duel.get("days_meaning")
    if not real_game or not two_issue(duel) or not isinstance(text, str) or not text.strip():
        return "unknown"
    if not YOU.search(text) or OTHER.search(text) or NEGATION.search(text) or DIRECTION.search(text):
        return "unknown"  # not about OUR weight, the other side's, negated or with a direction: never read a sign
    plus_gain, plus_loss = bool(PLUS_GAIN.search(text)), bool(PLUS_LOSS.search(text))
    if plus_gain != plus_loss:
        return "signed" if plus_gain else "reversed"
    if LOSS.search(text) and not GAIN.search(text):
        return "cost"
    return "unknown"


SCORE_TOLERANCE = 0.5  # P: a result comes rounded to 0.1 and is divided by what decay kept (≥ 0.4 in practice)


def scored_evidence(duel: Mapping[str, Any], real_game: bool) -> Verdict:
    """What a FINISHED real two-issue deal with days says, from how the game scored it (W2b's suggestion): the
    practice payloads score `result = surplus × (1 - decay) ** rounds`, so `result / kept - (price vs limit)` is
    what the days added. `signed` when that is +weight × days with weight > 0; `cost` when it is -|weight| × days
    with weight > 0 (the worst case is the truth); `reversed` when it is -weight × days with weight < 0; anything
    else, a negative weight that cannot tell signed from cost included, is `unknown`."""
    if not real_game or not two_issue(duel) or duel.get("status") != "deal":
        return "unknown"
    price, days, result = _number(duel.get("price")), _number(duel.get("days")), _number(duel.get("result"))
    weight, limit = _number(duel.get("your_days_weight")), _number(duel.get("your_limit"))
    rounds, decay = _number(duel.get("rounds")), _number(duel.get("decay_per_round"))
    if price is None or days is None or result is None or weight is None or limit is None:
        return "unknown"
    if rounds is None or decay is None or not days or not weight:
        return "unknown"
    kept = (1 - decay) ** rounds
    if kept <= 0:
        return "unknown"
    base = price - limit if duel.get("role") == "seller" else limit - price
    added = result / kept - base
    fits = {
        name
        for name, expected in (("signed", weight * days), ("cost", -abs(weight) * days), ("reversed", -weight * days))
        if abs(added - expected) <= SCORE_TOLERANCE
    }
    if not fits:  # the game scored the days some other way: our sign model is wrong, never trust it (#150 r2)
        return "conflict"
    if weight > 0:
        return "signed" if fits == {"signed"} else "cost" if fits == {"cost", "reversed"} else "unknown"
    return "reversed" if fits == {"reversed"} else "unknown"


def _merge(a: Verdict, b: Verdict) -> Verdict:
    """Two verdicts into one: a conflict sticks, unknown yields, two known ones that differ conflict."""
    if "conflict" in (a, b):
        return "conflict"
    if a == "unknown":
        return b
    if b == "unknown" or a == b:
        return a
    return "conflict"


@dataclass
class DaysSwitch:
    """The first real evidence about the sign, latched and persisted. `signed(allowed)` is what a policy reads.

    `signed` needs two real signals that agree (#150 security P1: one misread text or score would drop the guard's
    worst case for every duel): a signed text and a signed score, or the scores of two different finished deals,
    both from the current session (#150 security r2 P2). A file from before sessions were recorded keeps its verdict
    but none of its signals, so it is never corroborated."""

    verdict: Verdict = "unknown"
    duel: int | None = None  # the payload that decided it
    text: str | None = None
    path: Path | None = None
    texts: list[int] = field(default_factory=list)  # sessions whose real `days_meaning` text said signed
    scored: list[list[int]] = field(default_factory=list)  # [session, duel] of finished real deals scored signed
    session: int | None = None  # the current session: from the live duels last read, never saved to the file

    @classmethod
    def load(cls, path: Path) -> DaysSwitch:
        try:
            raw = json.loads(path.read_text())
        except FileNotFoundError:
            return cls(path=path)
        except (OSError, ValueError):  # unreadable: never silently undo a recorded conflict (a person deletes it)
            return cls(verdict="conflict", path=path)
        if not isinstance(raw, dict):
            return cls(verdict="conflict", path=path)
        verdicts: dict[str, Verdict] = {
            "signed": "signed",
            "reversed": "reversed",
            "cost": "cost",
            "conflict": "conflict",
        }
        verdict = verdicts.get(str(raw.get("verdict")), "unknown")
        texts, pairs = raw.get("texts"), raw.get("scored")  # an old file's `text_signed` and bare ids: dropped
        sessions = [s for s in texts if _int(s) is not None] if isinstance(texts, list) else []
        scored = (
            [p for p in pairs if isinstance(p, list) and len(p) == 2 and all(_int(x) is not None for x in p)]
            if isinstance(pairs, list)
            else []
        )
        return cls(verdict, raw.get("duel"), raw.get("text"), path, _union(sessions, []), _union_pairs(scored, []))

    def observe(self, duels: Iterable[Mapping[str, Any]], real_game: bool) -> Verdict:
        """Read every payload, live or finished: its text and, for a finished deal, its score. The first real
        evidence latches; later evidence that disagrees is a conflict, for good. Another process's verdict in the
        file is merged in first, so `duel run` and the runtime never undo each other. Rows whose `status` is live
        set the current session: the highest int `session` among them (None when none has one)."""
        self.refresh()
        before, before_corroborated = self.verdict, self.corroborated
        rows = list(duels)
        live = [duel for duel in rows if duel.get("status") == "live"]
        if live:
            self.session = max((s for duel in live if (s := _int(duel.get("session"))) is not None), default=None)
        for duel in rows:
            said, scored = evidence(duel, real_game), scored_evidence(duel, real_game)
            self._count(duel, said, scored)
            for seen in (said, scored):
                if seen == "unknown":
                    continue
                merged = _merge(self.verdict, seen)
                if self.verdict == "unknown":
                    self.duel, self.text = duel.get("duel"), duel.get("days_meaning")
                self.verdict = merged
        disk = self._on_disk()
        lagging = disk != self._record() and (disk is not None or self.verdict != "unknown")
        if (self.verdict, self.corroborated) != (before, before_corroborated) or lagging:
            self._save()  # also when the file lags what we merged (a conflict another process must see: #150 r2)
        return self.verdict

    def _count(self, duel: Mapping[str, Any], said: Verdict, scored: Verdict) -> None:
        """Record a signed text or score under its payload's session; a row without an int `session` never counts."""
        session, did = _int(duel.get("session")), _int(duel.get("duel"))
        if session is None:
            return
        if said == "signed":
            self.texts = _union(self.texts, [session])
        if scored == "signed" and did is not None:
            self.scored = _union_pairs(self.scored, [[session, did]])

    def refresh(self) -> None:
        """Merge the verdict on disk (another process may have written it) into this one: the per-session signals
        as unions. The current session is never read from the file: only this process's live duels set it."""
        if self.path is None:
            return
        disk = DaysSwitch.load(self.path)
        if disk.verdict != "unknown" and self.verdict == "unknown":
            self.duel, self.text = disk.duel, disk.text
        self.verdict = _merge(self.verdict, disk.verdict)
        self.texts = _union(self.texts, disk.texts)
        self.scored = _union_pairs(self.scored, disk.scored)

    @property
    def corroborated(self) -> bool:
        """Two real signals of the current session agree on signed: a text and a score, or two deals' scores."""
        if self.verdict != "signed" or self.session is None:
            return False
        deals = {did for session, did in self.scored if session == self.session}
        return len(deals) >= 2 or (bool(deals) and self.session in self.texts)

    def signed(self, allowed: bool) -> bool:
        """Value days with their sign only when allowed (a guardrail) AND two real signals of this session said so."""
        return allowed and self.corroborated

    def _record(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if k not in ("path", "session")}

    def _on_disk(self) -> dict[str, Any] | None:
        if self.path is None:
            return None
        try:
            raw = json.loads(self.path.read_text())
        except (OSError, ValueError):
            return None
        return raw if isinstance(raw, dict) else None

    def _save(self) -> None:
        """Merge with the file, then replace it atomically (a temp file and `os.replace`)."""
        if self.path is None:
            return
        self.refresh()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        record = self._record()
        tmp = self.path.with_name(f".{self.path.name}.{os.getpid()}.tmp")
        with tmp.open("w", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False))
            handle.flush()
            os.fsync(handle.fileno())  # durable before the rename: a crash never leaves a half-written verdict
        os.replace(tmp, self.path)


LATCH_FILE = Path("duels") / "days_sign.json"  # under Settings.data_dir (.local, never committed)
REAL_HOST = "bazaar.causaprima.ai"  # config.DEFAULT_URL: the official game


def real_game(base_url: str) -> bool:
    """True only against the official game's host: a simulator, local or deployed, is never evidence."""
    return urlparse(base_url).hostname == REAL_HOST


def latch(data_dir: Path) -> DaysSwitch:
    return DaysSwitch.load(data_dir / LATCH_FILE)


def effective_rules(rules: Any, switch: DaysSwitch) -> Any:
    """The rules a duel tick runs with, one object for the policy and the guard alike:
    - `duel_days_signed` on when `duel_days_auto` allows it AND two real signals of the current session confirmed
      the sign (`observe` the live duels first: a switch that has read none knows no session and stays off);
    - `duel_days_signed` OFF, even when set by hand, once real evidence says otherwise (reversed, cost, conflict);
    - otherwise the very same rules."""
    if switch.verdict in ("reversed", "cost", "conflict"):
        return rules.model_copy(update={"duel_days_signed": False}) if rules.duel_days_signed else rules
    if rules.duel_days_signed or not switch.signed(bool(getattr(rules, "duel_days_auto", False))):
        return rules
    return rules.model_copy(update={"duel_days_signed": True})


def reads_done(rules: Any, switch: DaysSwitch, real: bool) -> bool:
    """Whether `duel run` reads `?done=true` for scored evidence: only on the real game, only for v2 with
    `duel_days_auto`, and while the verdict can still change (unknown) or still needs its cross-check (signed)."""
    v2 = getattr(rules, "duel_policy", "v1") == "v2"
    return real and v2 and bool(getattr(rules, "duel_days_auto", False)) and switch.verdict in ("unknown", "signed")


# ---------------------------------------------------------------- the rival's days


@dataclass(frozen=True)
class RivalDays:
    prefers: int | None  # the end it leans to: 10 or 0; None without evidence (or when it sits in the middle)
    confidence: float  # 0..1
    offers: int


def rival_days(duel: Mapping[str, Any]) -> RivalDays:
    """The rival's preferred end of 0-10 from its own priced offers. A 0 counts half: a rival that does not
    handle days may send 0 whatever its weight. (No weight estimate: its price moves mix concession with days.)"""
    rival = duel.get("rival")
    days = [
        d
        for m in duel.get("messages") or []
        if m.get("from") == rival
        and _number(m.get("price")) is not None
        and isinstance(d := m.get("days"), int)
        and not isinstance(d, bool)
    ]
    if not days:
        return RivalDays(None, 0.0, 0)
    mean = statistics.fmean(days)
    prefers = DAYS_MAX if mean >= 7 else 0 if mean <= 3 else None
    side = [d for d in days if (d >= 7 if prefers == DAYS_MAX else d <= 3)] if prefers is not None else []
    confidence = min(1.0, len(days) / 3) * (len(side) / len(days)) * (0.5 if prefers == 0 else 1.0)
    return RivalDays(prefers, round(confidence, 3), len(days))


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
    """Our delivery day. Worst case: 0. Signed: the end where our weight plus the rival's (its preference, at the
    prior magnitude, scaled by our confidence in it) is larger. None in a price-only duel."""
    if not two_issue(duel):
        return None
    weight = _number(duel.get("your_days_weight"))
    if not signed or weight is None:
        return 0
    theirs = 0.0
    if rival.prefers is not None:
        size = prior * rival.confidence
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
