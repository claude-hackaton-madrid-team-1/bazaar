"""Other teams' set multipliers (AF1): what they SAY in a team thread, beside what we INFER from the feed.

Every team holds the same six multipliers, shuffled (RULES.md "Your values are private"), so knowing which set
is a team's ×1.6 tells us what it will pay. The team desk asks once per team per game day, in the words of its
first message in a thread (`ask_line`); the structured offer never changes. Their answers are untrusted words
("words persuade, structure binds"): `parse` keeps only a set named next to a multiplier of the shared multiset,
drops a set or a value claimed twice, and stores the scrubbed quote. `inferred_rows` turns the rival affinity map
(`affinity.affinity_map`) into rows of their own source, so the table always has something to show.

Stored in `team_affinity` (schema.sql), one row per (team, set_code, source), read by `bazaar affinity --teams`,
the `team_affinity_board` view (DataGrip) and bazaar-live. Writes run off the tick (`AffinityBook`): a failed or
slow write is logged and never holds a send. Nothing here changes a price.
"""

from __future__ import annotations

import re
import threading
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal
from zoneinfo import ZoneInfo

from bazaar_agent.affinity import AffinityMap
from bazaar_agent.llm.chooser import folded, injection_flags
from bazaar_agent.telemetry import scrub

Source = Literal["said", "inferred"]
QUOTE_MAX = 200  # characters of their message kept with a 'said' row
READ_MAX = 1000  # characters of one message the parser reads (a longer one is cut, never rejected)
SAID_CONFIDENCE = 0.5  # words may lie: a stated multiplier is a hint, never a fact
TAGGED_CONFIDENCE = 0.25  # the same words carrying a prompt-injection shape
TOLERANCE = 0.05  # how far a stated number may sit from a multiplier of the multiset
MADRID = ZoneInfo("Europe/Madrid")

# Codes are matched in capitals only ("mal" and "sal" are Spanish words); names on the accent-free folded text.
CODES = r"\b(LAV|SAL|MAL|RET|LAT|CHA)\b"
NAMES = {
    "LAV": r"lavapies",
    "SAL": r"salamanca",
    "MAL": r"malasana",
    "RET": r"(?:el\s+)?retiro",
    "LAT": r"(?:la\s+)?latina",
    "CHA": r"chamberi",
}
SET_TOKEN = re.compile(CODES + "|" + "|".join(f"(?i:\\b(?P<{k}>{v})\\b)" for k, v in NAMES.items()))
# "×1.6", "x1,3", "*1.1", "1,6", "1.60": one digit 0-2, a dot or comma, one or two digits, inside no longer number.
MULTIPLIER = re.compile(r"(?<![\d.,])(?:[x×*]\s?)?([0-2])[.,](\d{1,2})(?![\d])", re.IGNORECASE)
WINDOW = 16  # the most characters between a set and its multiplier
CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


@dataclass(frozen=True)
class Claim:
    set_code: str
    multiplier: float


@dataclass(frozen=True)
class Row:
    team: str
    set_code: str
    multiplier: float
    source: Source
    confidence: float
    tick: int
    thread_id: int | None = None
    quote: str | None = None

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.team, self.set_code, self.source)


def ask_line(multipliers: Sequence[float]) -> str | None:
    """The question added to our first message in a team thread (Spanish, then English). It names only the top
    of the shared multiset, which every team holds: nothing of ours. None when the multiset is unknown."""
    if not multipliers:
        return None
    top = f"{max(multipliers):.1f}"
    return f"Por cierto, ¿qué barrio es vuestro ×{top.replace('.', ',')}? / By the way, which set is your ×{top}?"


def _tokens(text: str) -> tuple[list[tuple[int, int, str]], list[tuple[int, int, float]]]:
    sets = []
    for m in SET_TOKEN.finditer(text):
        code = m.group(1) or next(k for k in NAMES if m.group(k))
        sets.append((m.start(), m.end(), code))
    values = [(m.start(), m.end(), round(float(f"{m.group(1)}.{m.group(2)}"), 2)) for m in MULTIPLIER.finditer(text)]
    return sets, values


def _plausible(value: float, multiset: Sequence[float]) -> float | None:
    """The multiset's value a stated number stands for (None: not one of them)."""
    if not multiset:
        return value if 0.3 <= value <= 2.0 else None
    best = min(multiset, key=lambda a: abs(a - value))
    return float(best) if abs(best - value) <= TOLERANCE else None


def _pair(sets: list[tuple[int, int, str]], values: list[tuple[int, int, float]]) -> list[tuple[str, int]]:
    """Each set with the multiplier written right after it (else right before it), never across another set."""
    out = []
    starts = [s for s, _, _ in sets]
    for start, end, code in sets:
        after = [v for v in values if 0 <= v[0] - end <= WINDOW and not any(end <= s < v[0] for s in starts)]
        before = [v for v in values if 0 <= start - v[1] <= WINDOW and not any(v[1] <= s < start for s in starts)]
        pick = after[0] if after else (before[-1] if before else None)
        if pick is not None:
            out.append((code, values.index(pick)))
    return out


def parse(text: str | None, multiset: Sequence[float] = ()) -> list[Claim]:
    """The (set, multiplier) pairs a message states, from untrusted text: a set code or barrio name next to a
    multiplier of the shared multiset. A set given two values, or a value used more often than the multiset
    holds it, is dropped whole (a contradiction is no answer). At most one claim per set."""
    if not text:
        return []
    plain = folded(text[:READ_MAX])
    sets, values = _tokens(plain)
    pairs = _pair(sets, values)
    by_set: dict[str, set[float]] = {}
    for code, i in pairs:
        value = _plausible(values[i][2], multiset)
        if value is not None:
            by_set.setdefault(code, set()).add(value)
    claims = {code: next(iter(vals)) for code, vals in by_set.items() if len(vals) == 1}
    room = Counter(round(a, 2) for a in multiset)
    used = Counter(round(v, 2) for v in claims.values())
    over = {v for v, n in used.items() if multiset and n > room[v]}
    return [Claim(c, v) for c, v in sorted(claims.items()) if round(v, 2) not in over]


def quote_of(text: str) -> str:
    """Their words as stored: scrubbed (secrets, key shapes), no control characters, NUL or lone surrogate,
    at most `QUOTE_MAX` characters."""
    clean = CONTROL.sub(" ", text).encode("utf-8", "replace").decode("utf-8")
    return scrub(clean)[:QUOTE_MAX]


def said_rows(
    team: str, text: str | None, tick: int, thread_id: int | None, multiset: Sequence[float] = ()
) -> list[Row]:
    claims = parse(text, multiset)
    if not claims or text is None:
        return []
    confidence = TAGGED_CONFIDENCE if injection_flags(text[:READ_MAX]) else SAID_CONFIDENCE
    quote = quote_of(text)
    return [Row(team, c.set_code, c.multiplier, "said", confidence, tick, thread_id, quote) for c in claims]


def inferred_rows(amap: AffinityMap, tick: int) -> list[Row]:
    """Per team and set, the likeliest multiplier of the posterior and its probability."""
    out = []
    for team, ta in sorted(amap.teams.items()):
        for set_code in ta.sets:
            dist = ta.distribution.get(set_code) or {}
            if not dist:
                continue
            value, p = max(dist.items(), key=lambda kv: (kv[1], kv[0]))
            out.append(Row(team, set_code, float(value), "inferred", round(float(p), 4), tick))
    return out


def game_day(now: datetime | None = None) -> str:
    """The game day a question is asked on (the Madrid date): a key, never a schedule."""
    return (now or datetime.now(MADRID)).astimezone(MADRID).date().isoformat()


# ---------------------------------------------------------------- Postgres

COLUMNS = ("team", "set_code", "multiplier", "source", "confidence", "tick", "thread_id", "quote", "updated_at")


def save(conn: Any, rows: Iterable[Row]) -> int:
    """Upsert rows in key order (no deadlock between writers); an older tick never overwrites a newer row."""
    batch = sorted({r.key: r for r in rows}.values(), key=lambda r: r.key)
    if not batch:
        return 0
    with conn.cursor() as cur:
        cur.executemany(
            "insert into team_affinity (team, set_code, multiplier, source, confidence, tick, thread_id, quote) "
            "values (%s, %s, %s, %s, %s, %s, %s, %s) on conflict (team, set_code, source) do update set "
            "multiplier = excluded.multiplier, confidence = excluded.confidence, tick = excluded.tick, "
            "thread_id = excluded.thread_id, quote = excluded.quote, updated_at = now() "
            "where excluded.tick >= team_affinity.tick",
            [(r.team, r.set_code, r.multiplier, r.source, r.confidence, r.tick, r.thread_id, r.quote) for r in batch],
        )
    conn.commit()
    return len(batch)


def read(conn: Any) -> list[dict[str, Any]]:
    rows = conn.execute(f"select {', '.join(COLUMNS)} from team_affinity order by team, set_code, source").fetchall()
    conn.commit()
    return [dict(zip(COLUMNS, row, strict=True)) for row in rows]


# ---------------------------------------------------------------- the writer, off the tick

Writer = Callable[[list[Row]], None]


class AffinityBook:
    """Rows queued during the tick (one per key, the newest), written on a daemon thread after the sends."""

    def __init__(self, write: Writer | None, log: Callable[[str], None]) -> None:
        self.write, self.log = write, log
        self._queue: dict[tuple[str, str, str], Row] = {}
        self._lock = threading.Lock()
        self._worker: threading.Thread | None = None

    def add(self, rows: Iterable[Row]) -> None:
        if self.write is not None:
            with self._lock:
                self._queue.update({r.key: r for r in rows})

    def flush(self, tick: int) -> None:
        if self.write is None or not self._queue or (self._worker is not None and self._worker.is_alive()):
            return  # the previous write still runs: the queue waits for the next tick
        with self._lock:
            batch, self._queue = list(self._queue.values()), {}
        write = self.write

        def run() -> None:
            try:
                write(batch)
            except Exception as e:  # noqa: BLE001 — storage is for reading later; never a reason to stop trading
                self.log(f"tick {tick} team affinity: {len(batch)} row(s) not stored ({type(e).__name__})")
                with self._lock:  # tried again at the next flush, unless a newer row for the same key came in
                    for r in batch:
                        self._queue.setdefault(r.key, r)

        self._worker = threading.Thread(target=run, name="team-affinity-store", daemon=True)
        self._worker.start()
