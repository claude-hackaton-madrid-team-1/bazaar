"""Other teams' set multipliers (AF1): what they SAY in a team thread, beside what we INFER from the feed.

Every team holds the same six multipliers, shuffled (RULES.md "Your values are private"), so knowing which set
is a team's ×1.6 tells us what it will pay. The team desk asks once per team per game day, in the words of its
first message in a thread (`ask_line`); the structured offer never changes. Their answers are untrusted words
("words persuade, structure binds"): `parse` keeps only a set named next to a multiplier of the shared multiset,
in a plain statement (no question, no "your", no negation), drops a set or a value claimed twice, and stores the
scrubbed quote. `inferred_rows` turns the rival affinity map (`affinity.affinity_map`) into rows of their own
source: one consistent assignment per team with any signal, so the table has something to show.

Stored in `team_affinity` (schema.sql), one row per (team, set_code, source), read by `bazaar affinity --teams`,
the `team_affinity_board` view (DataGrip) and bazaar-live. Writes run off the tick (`AffinityBook`): a failed or
slow write is logged and never holds a send. Nothing here changes a price.
"""

from __future__ import annotations

import itertools
import math
import re
import threading
import unicodedata
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal
from zoneinfo import ZoneInfo

import psycopg

from bazaar_agent.affinity import AffinityMap, TeamAffinity
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
# "retiro" and "latina" are words too ("me retiro": I walk away): lower case only after their article.
CODES = r"\b(LAV|SAL|MAL|RET|LAT|CHA)\b"
NAMES = {
    "LAV": r"(?i:lavapies)",
    "SAL": r"(?i:salamanca)",
    "MAL": r"(?i:malasana)",
    "RET": r"(?i:el\s+retiro)|Retiro|RETIRO",
    "LAT": r"(?i:la\s+latina)|Latina|LATINA",
    "CHA": r"(?i:chamberi)",
}
SET_TOKEN = re.compile(CODES + "|" + "|".join(f"\\b(?P<{k}>{v})\\b" for k, v in NAMES.items()))
# A sentence ends at ? ! ; a line break, an opening ¿ or ¡, or a full stop before a space (never inside "1.6").
SENTENCE_END = re.compile(r"[?!;\n¿¡]|\.(?=\s|$)")
# A pair in a sentence that talks about OUR sets ("vuestro ×1,6 es LAV, ¿verdad?") or denies one ("not LAT") is
# no claim of theirs.
YOURS = re.compile(r"\b(?:vuestr[oa]s?|tus?|your|yours|ustedes)\b", re.IGNORECASE)
NEGATION = re.compile(r"\b(?:no|not|ni|nor|nunca|never|jamas|tampoco|isnt|arent)\b|n't\b", re.IGNORECASE)
# "×1.6", "x1,3", "*1.1", "1,6", "1.60": one digit 0-2, a dot or comma, one or two digits, inside no longer number.
MULTIPLIER = re.compile(r"(?<![\d.,])(?:[x×*]\s?)?([0-2])[.,](\d{1,2})(?![\d])", re.IGNORECASE)
WINDOW = 16  # the most characters between a set and its multiplier
QUOTE_WINDOW = QUOTE_MAX + 64  # what the quote reads: a key shape starting inside the kept 200 is still whole


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


def _statements(text: str) -> list[str]:
    """The sentences that can carry a claim of theirs: no question, no "your", no negation."""
    out, start, opened_question = [], 0, False
    for m in SENTENCE_END.finditer(text):
        question = opened_question or m.group() == "?"
        out.append((text[start : m.start()], question))
        opened_question, start = m.group() == "¿", m.end()
    out.append((text[start:], opened_question))
    return [s for s, question in out if not question and not YOURS.search(s) and not NEGATION.search(s)]


def parse(text: str | None, multiset: Sequence[float] = ()) -> list[Claim]:
    """The (set, multiplier) pairs a message states, from untrusted text: a set code or barrio name next to a
    multiplier of the shared multiset, inside one plain statement (`_statements`). A set given two values, or a
    value used more often than the multiset holds it, is dropped whole (a contradiction is no answer). At most
    one claim per set."""
    if not text:
        return []
    by_set: dict[str, set[float]] = {}
    for sentence in _statements(folded(text[:READ_MAX])):
        sets, values = _tokens(sentence)
        for code, i in _pair(sets, values):
            value = _plausible(values[i][2], multiset)
            if value is not None:
                by_set.setdefault(code, set()).add(value)
    claims = {code: next(iter(vals)) for code, vals in by_set.items() if len(vals) == 1}
    room = Counter(round(a, 2) for a in multiset)
    used = Counter(round(v, 2) for v in claims.values())
    over = {v for v, n in used.items() if multiset and n > room[v]}
    return [Claim(c, v) for c, v in sorted(claims.items()) if round(v, 2) not in over]


def quote_of(text: str) -> str:
    """Their words as stored, at most `QUOTE_MAX` characters: only the first `QUOTE_WINDOW` read (bounded work,
    the masking patterns are slow on long runs), compatibility forms normalised BEFORE the scrub (a fullwidth
    key shape is caught), control, format and bidi characters, NUL and lone surrogates turned to spaces."""
    window = unicodedata.normalize("NFKC", text[:QUOTE_WINDOW])[:QUOTE_WINDOW]
    kept = "".join(" " if unicodedata.category(ch) in ("Cc", "Cf", "Cs") and ch != "\n" else ch for ch in window)
    return scrub(kept)[:QUOTE_MAX]


def said_rows(
    team: str, text: str | None, tick: int, thread_id: int | None, multiset: Sequence[float] = ()
) -> list[Row]:
    claims = parse(text, multiset)
    if not claims or text is None:
        return []
    confidence = TAGGED_CONFIDENCE if injection_flags(text[:READ_MAX]) else SAID_CONFIDENCE
    quote = quote_of(text)
    return [Row(team, c.set_code, c.multiplier, "said", confidence, tick, thread_id, quote) for c in claims]


def _assignment(ta: TeamAffinity, multiset: Sequence[float]) -> dict[str, float]:
    """The multiset dealt to the team's sets, each multiplier used as often as the multiset holds it, that best
    agrees with the posterior (the most probable product of the per-set marginals)."""

    def score(perm: tuple[float, ...]) -> float:
        return sum(
            math.log(max(1e-9, ta.distribution.get(s, {}).get(a, 0.0))) for s, a in zip(ta.sets, perm, strict=True)
        )

    best = max(sorted(set(itertools.permutations(multiset))), key=score)
    return dict(zip(ta.sets, best, strict=True))


def inferred_rows(amap: AffinityMap, tick: int, multiset: Sequence[float]) -> list[Row]:
    """Per team with any signal, one consistent assignment (`_assignment`), each set with its marginal
    probability. A team we saw nothing of has only the prior: no rows (we know nothing about it)."""
    out = []
    for team, ta in sorted(amap.teams.items()):
        if ta.signals == 0 or len(ta.sets) != len(multiset):
            continue
        for set_code, value in _assignment(ta, multiset).items():
            p = ta.distribution.get(set_code, {}).get(value, 0.0)
            out.append(Row(team, set_code, float(value), "inferred", round(float(p), 4), tick))
    return out


def game_day(now: datetime | None = None) -> str:
    """The game day a question is asked on (the Madrid date): a key, never a schedule."""
    return (now or datetime.now(MADRID)).astimezone(MADRID).date().isoformat()


# ---------------------------------------------------------------- Postgres

COLUMNS = ("team", "set_code", "multiplier", "source", "confidence", "tick", "thread_id", "quote", "updated_at")


UPSERT = (
    "insert into team_affinity (team, set_code, multiplier, source, confidence, tick, thread_id, quote) "
    "values (%s, %s, %s, %s, %s, %s, %s, %s) on conflict (team, set_code, source) do update set "
    "multiplier = excluded.multiplier, confidence = excluded.confidence, tick = excluded.tick, "
    "thread_id = excluded.thread_id, quote = excluded.quote, updated_at = now() "
    "where excluded.tick >= team_affinity.tick"
)


def _upsert(conn: Any, batch: Sequence[Row]) -> None:
    with conn.cursor() as cur:
        cur.executemany(
            UPSERT,
            [(r.team, r.set_code, r.multiplier, r.source, r.confidence, r.tick, r.thread_id, r.quote) for r in batch],
        )
    conn.commit()


def save(conn: Any, rows: Iterable[Row]) -> int:
    """Upsert rows in key order (no deadlock between writers); an older tick never overwrites a newer row.
    A row Postgres refuses (data or constraint) is dropped alone, never holding the others back. Returns the
    number of rows stored; a connection error raises (the book tries the batch again)."""
    batch = sorted({r.key: r for r in rows}.values(), key=lambda r: r.key)
    if not batch:
        return 0
    try:
        _upsert(conn, batch)
        return len(batch)
    except (psycopg.DataError, psycopg.IntegrityError):
        conn.rollback()
    stored = 0
    for row in batch:
        try:
            _upsert(conn, [row])
            stored += 1
        except (psycopg.DataError, psycopg.IntegrityError):
            conn.rollback()
    return stored


def read(conn: Any) -> list[dict[str, Any]]:
    rows = conn.execute(f"select {', '.join(COLUMNS)} from team_affinity order by team, set_code, source").fetchall()
    conn.commit()
    return [dict(zip(COLUMNS, row, strict=True)) for row in rows]


def said_teams(conn: Any) -> set[str]:
    """Teams whose words already named a multiplier: never asked again, after a restart too."""
    rows = conn.execute("select distinct team from team_affinity where source = 'said'").fetchall()
    conn.commit()
    return {str(r[0]) for r in rows}


# ---------------------------------------------------------------- the writer, off the tick

Writer = Callable[[list[Row]], int | None]  # rows stored (None: all of them)
STUCK_TICKS = 10  # a write still running this long is reported once (a hung connection: nothing more is stored)


class AffinityBook:
    """Rows queued during the tick (one per key, the newest), written on a daemon thread after the sends.

    `load_told` (the teams whose 'said' rows are stored) runs once, on its own daemon thread: until it answered,
    `told_ready` is unset and the desk asks nobody (it never waits for it)."""

    def __init__(
        self,
        write: Writer | None,
        log: Callable[[str], None],
        load_told: Callable[[], Iterable[str]] | None = None,
    ) -> None:
        self.write, self.log = write, log
        self._queue: dict[tuple[str, str, str], Row] = {}
        self._lock = threading.Lock()
        self._worker: threading.Thread | None = None
        self._started: int | None = None  # the tick the running write started
        self._stuck_logged = False
        self.told: frozenset[str] = frozenset()
        self.told_ready = threading.Event()
        if load_told is None:
            self.told_ready.set()
        else:
            threading.Thread(target=self._load, args=(load_told,), name="team-affinity-told", daemon=True).start()

    def _load(self, load_told: Callable[[], Iterable[str]]) -> None:
        try:
            self.told = frozenset(load_told())
        except Exception as e:  # noqa: BLE001 — without it we may ask a team twice; never a reason to stop
            self.log(f"team affinity: stored answers unreadable ({type(e).__name__}); asking as if none")
        self.told_ready.set()

    def add(self, rows: Iterable[Row]) -> None:
        if self.write is not None:
            with self._lock:
                self._queue.update({r.key: r for r in rows})

    def flush(self, tick: int) -> None:
        if self.write is None or not self._queue:
            return
        if self._worker is not None and self._worker.is_alive():  # the queue waits for the next tick
            if self._started is not None and tick - self._started >= STUCK_TICKS and not self._stuck_logged:
                self._stuck_logged = True
                self.log(f"tick {tick} team affinity: the write started at tick {self._started} still runs")
            return
        with self._lock:
            batch, self._queue = list(self._queue.values()), {}
        write = self.write

        def run() -> None:
            try:
                stored = write(batch)
                unique = len({r.key for r in batch})
                if stored is not None and stored < unique:
                    self.log(f"tick {tick} team affinity: {unique - stored} row(s) refused by Postgres, dropped")
            except Exception as e:  # noqa: BLE001 — storage is for reading later; never a reason to stop trading
                self.log(f"tick {tick} team affinity: {len(batch)} row(s) not stored ({type(e).__name__})")
                with self._lock:  # tried again at the next flush, unless a newer row for the same key came in
                    for r in batch:
                        self._queue.setdefault(r.key, r)

        self._started, self._stuck_logged = tick, False
        self._worker = threading.Thread(target=run, name="team-affinity-store", daemon=True)
        self._worker.start()
