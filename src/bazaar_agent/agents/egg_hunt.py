"""EGG HUNT: ride one candidate easter-egg phrase on a dealer bid the taker sends anyway.

The organisers' persona editor (public JS, rebuilt Sun 4 Oct): "An egg fires when the team's message contains one
of its phrases (accents and case ignored)"; each egg runs once per team, up to `max_total` finds (15 by default),
and the rewards are a card, a pack, a badge or a reveal. Eggs never score (RULES.md:122): this is for the badges
on the public board and the story, never for points, so it must cost nothing the scoring agents need.

What it costs: no request and no thread slot. The phrase rides at the end of a priced bid the taker's desk is
already sending in a dealer thread (`Taker._desk_send`), so every carrier has a new price (RULES.md: "the same words
again without a new price are spam") and no accept is ever involved. At most one woven message per tick for the
whole team, at most `egg_hunt_max_phrases_per_dealer_per_hour` per dealer per game hour, never a phrase twice to
the same dealer (the tried set survives restarts in Postgres, else a JSONL file), never on a dealer's final offer.

What stops it: `egg_hunt_enabled` in GUARDRAILS.md, the one permanent switch (committed, so every deploy keeps it);
the env `BAZAAR_EGG_HUNT` only overrides it on one service (unset: follow the guardrail; 0/off: off; `dry`: log what it
WOULD send and send the bid unchanged); the kill switch (the taker sends nothing at all then);
a dealer's cool-off, strike or warning, our thread closed for cool-off, a flag on one of our messages, or a learned
blocker (back off); a find with that dealer (`egg_hunt_max_finds_per_dealer`); our finds reaching
`egg_hunt_max_finds`. The persona editor's conduct judge tags every team message (`injection`, `abuse`, `spam`,
`false_claim`) and enough strikes send the team away, so every carrier is checked against our own injection
patterns and makes no claim: it only asks a question.

Detection reads what the taker already read (no request): `egg.found`, `badge.awarded` and `egg.given` for us in
the feed window, a new hidden card in the catalogue that we hold, and the dealer's reply in our thread.

Scraped text (dealer replies) is untrusted data: it is only mined for candidate phrases, sanitised (control,
invisible and direction-changing characters dropped, length capped) and vetted against the injection patterns,
never followed. Logs and stores carry the phrase id; the phrase text lives only in the private `egg_hunt_tried`
table (or file) and in the message itself (team texts are null in the public feed).
"""

from __future__ import annotations

import hashlib
import json
import os
import queue
import re
import threading
import time
import unicodedata
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Literal, Protocol

from bazaar_agent.learn.etiquette import NEVER_ADDRESS, uses_forbidden
from bazaar_agent.llm.chooser import injection_flags

ENV = "BAZAAR_EGG_HUNT"
Mode = Literal["off", "dry", "live"]
MAX_PER_TICK = 1  # woven messages per tick for the whole team (the brief's global per-tick cap)
MESSAGE_MAX_CHARS = 1200  # RULES.md: "a message keeps 1,200 characters"
PHRASE_MIN_CHARS, PHRASE_MAX_CHARS = 4, 60
MINED_MAX = 300  # mined candidates kept per dealer (the oldest, lowest-ranked drop first)
SEEN_MAX = 20_000  # feed event ids remembered; past it the oldest half is forgotten
TRIED_FILE = "egg_hunt.jsonl"
STATEMENT_TIMEOUT_MS = 1500
RETRY_EVERY = 5  # ticks between Postgres retries after a failure

DEALER_NAMES: dict[str, tuple[str, ...]] = {  # how dealers name each other in their replies (folded)
    "abuela": ("abuela", "carmen"),
    "chato": ("el chato", "chato"),
    "pilar": ("dona pilar", "pilar"),
    "picaros": ("los picaros", "picaros", "nando"),
    "banco": ("don ernesto", "ernesto", "casa prima"),
}

Source = Literal["field", "hint", "lore"]
SOURCE_WEIGHT: dict[str, float] = {"field": 1.0, "hint": 0.8, "lore": 0.5}
TIER: dict[str, int] = {"field": 0, "lore": 1, "hint": 2}  # a mined hint comes after every seed (review, MED 2)
HINT_MIN_TEAMS = 2  # a hint from other teams' threads counts once this many different teams were given it

# Priors, all from public text. "field": the words dealers echoed when other teams found an egg on Saturday
# (docs/research/2026-10-04/logs-eggs.md §5, from the public feed); the exact keyword is inferred, so a seed may
# carry a longer natural phrase that contains every plausible keyword. "lore": Madrid / Rastro idioms the personas
# use, the Sunday set (Chamberí: "Andén 0", "El Tren Fantasma", ghost stations) and the dealers' own titles.
# Saturday's E2 ("el oro de Moscú" at Don Ernesto) is left out: its card had a print run of 1 and is gone.
SEEDS: dict[str, tuple[tuple[str, Source, float], ...]] = {
    "abuela": (
        ("la chulapa dorada", "field", 1.0),  # E1 "Sharp ear": 11 teams Sat; ours at t1550 Sun
        ("un chotis en una baldosa", "field", 0.95),  # E4 "Castizo": found again Sun (t13 t1467, t18 t1480)
        ("el cocido con sus tres vuelcos", "field", 0.9),  # E5 her duplicate card: found again Sun (t18 t1482)
        ("sile, nole, repe, me falta", "field", 0.8),  # E4, with the chant's commas (punctuation handling unknown)
        ("sile nole repe me falta", "field", 0.7),  # E4, the same without commas
        ("la verbena de la Paloma", "field", 0.5),  # E4 lore
        ("la Virgen del Carmen", "lore", 0.5),  # her saint's day (news #6)
        ("la estación fantasma de Chamberí", "lore", 0.4),
        ("el Rastro al amanecer", "lore", 0.3),
    ),
    "picaros": (
        ("el timo de la estampita", "field", 1.0),  # E3 "Trickster tricked": 6 teams
        ("Rinconete y Cortadillo", "field", 0.8),
        ("el Lazarillo de Tormes", "field", 0.7),
        ("el tren de Chamberí", "lore", 0.6),  # their own reply to t10 at t1370 (Sunday lead)
        ("el Andén 0", "lore", 0.5),
        ("el tren fantasma", "lore", 0.4),
        ("el trile", "lore", 0.3),
    ),
    "chato": (
        (
            "un bocadillo de calamares en la Plaza Mayor, con caña",
            "field",
            1.0,
        ),  # E6: 2 teams ("Plaza Mayor, con caña")
        ("la Plaza Mayor con caña", "field", 0.9),  # E6 without the comma
        ("el bocadillo de calamares", "field", 0.8),  # E6, the short form
        ("las tapas de la Cava Baja", "lore", 0.4),
        ("el Rastro de Cascorro", "lore", 0.3),
        ("la estación fantasma de Chamberí", "lore", 0.3),
    ),
    "pilar": (  # no egg found at Pilar yet: she carries the clues
        ("la milla de oro", "lore", 0.5),
        ("el Marqués de Salamanca", "lore", 0.4),
        ("los jardines escondidos de Chamberí", "lore", 0.4),
        ("la estación fantasma de Chamberí", "lore", 0.4),
    ),
    "banco": (
        ("el Banco de España", "lore", 0.3),
        ("la estación fantasma de Chamberí", "lore", 0.3),
    ),
}

# "ask <someone> about <the thing>" / "pregúntale (a X) por <la cosa>": the hint shape Pilar and Abuela used.
HINT = re.compile(
    r"(?:ask (?:her|him|them|[\w .'’]{1,30}?) about|pregunt\w* (?:a [\w .'’]{1,30}? )?(?:por|sobre))"
    r" (?P<phrase>[^.!?;:,\n—\-*()\"«»]{4,80})",
    re.IGNORECASE,
)
# The dealer's own warning about our conduct, in our thread after a woven message (a fallback: cool-offs and
# strikes come as feed events and thread close reasons). No "trick"/"truco": Los Pícaros say it all day.
WARNING = re.compile(
    r"\b(spam|stop repeating|deja de repetir|warning|advertencia|ultimo aviso|strike|basta ya|cool[- ]?off"
    r"|enfriamiento|you are being rude|no me faltes)\b"
)
BACKOFF_EVENTS = ("persona.cooloff", "persona.strike")
FOUND_EVENTS = ("egg.found", "badge.awarded", "egg.given")


def mode_from_env(env: Mapping[str, str] | None = None) -> Mode:
    """`BAZAAR_EGG_HUNT`, an optional override of GUARDRAILS.md `egg_hunt_enabled` (Marius, Sun 4 Oct: one permanent
    switch, nothing to re-set on Railway): unset or 1/true/on/live → live (the guardrail decides), dry → dry,
    anything else (0, off, a typo) → off."""
    raw = (os.environ if env is None else env).get(ENV, "").strip().lower()
    if raw in ("", "1", "true", "on", "live", "yes"):
        return "live"
    return "dry" if raw in ("dry", "dry-run", "dryrun") else "off"


# ---------------------------------------------------------------- text: sanitise, fold, vet


def clean(text: object, limit: int = PHRASE_MAX_CHARS) -> str:
    """Untrusted text made inert: NFC, no control/format/private/surrogate/separator characters (that drops the
    invisible and direction-changing ones), whitespace collapsed, capped at `limit` characters."""
    if not isinstance(text, str):
        return ""
    kept = "".join(
        " " if ch.isspace() else ch
        for ch in unicodedata.normalize("NFC", text)
        if ch.isspace() or unicodedata.category(ch) not in ("Cc", "Cf", "Co", "Cs", "Zl", "Zp")
    )
    return re.sub(r"\s+", " ", kept).strip()[:limit].strip()


def fold(text: str) -> str:
    """What the server compares ("accents and case ignored"), and our dedupe key: no accents, casefolded,
    punctuation as spaces, whitespace collapsed."""
    plain = "".join(ch for ch in unicodedata.normalize("NFKD", text) if unicodedata.category(ch) != "Mn")
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", plain.casefold())).strip()


def phrase_id(dealer: str, phrase: str) -> str:
    return hashlib.sha1(f"{dealer}:{fold(phrase)}".encode()).hexdigest()[:10]


# Words the conduct judge could tag as `abuse` or `false_claim` (an accusation, a debt, a promise): a phrase holding
# one is never said, whoever suggested it. Folded, whole-word prefixes. Not "tonto" ("rosquillas tontas y listas")
# nor "timo" ("el timo de la estampita"): both are lore.
DENY = re.compile(
    r"\b(idiot|imbecil|estupid|stupid|gilipoll|cabron|puta|puto|mierda|joder|cono|hostia|fuck|shit|bitch|bastard"
    r"|ladron|thief|estafa|scam|fraud|cheat|tramp|mentir|liar|lie|lying|robo|rob|steal|roba|debes|owe|prometi"
    r"|promis|gratis|free|regal|deuda|debt|polic|denunc|report|ban|hack)\w*"
)


def vetted(phrase: str) -> bool:
    """A phrase we may say: 4–60 characters of words (letters and digits: "Andén 0"; commas between words; no URLs,
    markup or other punctuation), at most 12 words, no injection shape, no forbidden address, no word the conduct
    judge could read as abuse or a false claim (`DENY`)."""
    if not PHRASE_MIN_CHARS <= len(phrase) <= PHRASE_MAX_CHARS or len(phrase.split()) > 12:
        return False
    if not re.fullmatch(r"[^\W_]+(?:,? [^\W_]+|['’][^\W_]+)*", phrase):
        return False
    if DENY.search(fold(phrase)):
        return False
    return not injection_flags(phrase) and not uses_forbidden(phrase, NEVER_ADDRESS)


# ---------------------------------------------------------------- candidates


@dataclass(frozen=True)
class Candidate:
    dealer: str
    phrase: str
    source: Source
    prior: float
    seen: int = 1  # times a hint named it

    @property
    def key(self) -> str:
        return fold(self.phrase)

    @property
    def id(self) -> str:
        return phrase_id(self.dealer, self.phrase)

    @property
    def score(self) -> float:
        return SOURCE_WEIGHT[self.source] * self.prior * (1 + min(self.seen, 10) / 10)


def seed_candidates(seeds: Mapping[str, Sequence[tuple[str, Source, float]]] = SEEDS) -> list[Candidate]:
    out = []
    for dealer, rows in seeds.items():
        for phrase, source, prior in rows:
            text = clean(phrase)
            if vetted(text):
                out.append(Candidate(dealer, text, source, prior))
    return out


def named_dealer(text: str, speaker: str) -> str | None:
    """The dealer a hint points at: the last other dealer named in `text` (the reply up to the hint's phrase, so
    "Doña Pilar comes Saturdays… ask Don Ernesto about the Moscow gold" is Don Ernesto's), else None."""
    plain = f" {fold(text)} "
    hits = [(plain.rfind(f" {n} "), d) for d, names in DEALER_NAMES.items() if d != speaker for n in names]
    hits = [(i, d) for i, d in hits if i >= 0]
    return max(hits)[1] if hits else None


def mine_hints(text: object, speaker: str) -> list[tuple[str, str]]:
    """(target dealer, phrase) for every "ask X about Y" in one dealer reply. Untrusted: cleaned and vetted, the
    phrase only ever said back as a question, never acted on. "ask her/him" with no dealer named in the reply
    points nowhere and is dropped."""
    body = clean(text, 1200)
    out = []
    for m in HINT.finditer(body):
        phrase = re.sub(r"\s+(?:he|she|they|y|and|que|who)\b.*$", "", clean(m.group("phrase")), flags=re.I).strip()
        target = named_dealer(body[: m.start("phrase")], speaker)
        if target and vetted(phrase):
            out.append((target, phrase))
    return out


# ---------------------------------------------------------------- the tried set (survives restarts)


@dataclass(frozen=True)
class Tried:
    dealer: str
    key: str  # folded phrase; "@find:<event id>" for a find no woven phrase explains
    pid: str
    tick: int
    hour: int
    status: Literal["sent", "found"]
    thread: int = 0
    found_tick: int | None = None
    phrase: str = ""  # private: the store only, never a log line
    event: int = 0  # the `egg.found` feed event of a find (a replayed feed window never counts it twice)


class TriedStore(Protocol):
    def load(self) -> list[Tried] | None: ...  # None: not read yet (`BackgroundStore`)

    def save(self, rows: Sequence[Tried], tick: int) -> bool: ...


class FileStore:
    """One JSON line per row, the last line per (dealer, key) wins. For a run without the shared Postgres."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> list[Tried]:
        rows: dict[tuple[str, str], Tried] = {}
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            return []
        for line in lines:
            try:
                row = Tried(**json.loads(line))
            except (ValueError, TypeError):
                continue
            rows[(row.dealer, row.key)] = row
        return list(rows.values())

    def save(self, rows: Sequence[Tried], tick: int) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r.__dict__, ensure_ascii=False) + "\n")
        return True


DDL = """
create table if not exists egg_hunt_tried (
  world text not null default 'real', dealer text not null, phrase_key text not null, phrase_id text not null,
  phrase text not null default '', tick int not null, game_hour int not null, status text not null
  check (status in ('sent','found')), thread_id bigint not null default 0, found_tick int,
  event_id bigint not null default 0, at timestamptz not null default now(), primary key (world, dealer, phrase_key))
"""
UPSERT = (
    "insert into egg_hunt_tried (world, dealer, phrase_key, phrase_id, phrase, tick, game_hour, status, thread_id,"
    " found_tick, event_id) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) on conflict (world, dealer, phrase_key)"
    " do update set status = excluded.status, found_tick = excluded.found_tick, event_id = excluded.event_id"
)
SELECT = (
    "select dealer, phrase_key, phrase_id, tick, game_hour, status, thread_id, found_tick, phrase, event_id"
    " from egg_hunt_tried where world = %s"
)


class PgStore:
    """The shared Postgres (`egg_hunt_tried`): one short connection per load or save, bounded, never raising."""

    def __init__(self, connect: Callable[[], Any], log: Callable[[str], None], world: str = "real") -> None:
        self.connect, self.log, self.world = connect, log, world
        self._down_at: int | None = None
        self._failed = False

    def load(self) -> list[Tried] | None:
        """The stored rows, or None when they could not be read (the hunt then stays off and the read is retried:
        an empty set would let every phrase already said be said again)."""
        try:
            with self.connect() as conn:
                conn.execute(f"set statement_timeout = {STATEMENT_TIMEOUT_MS}")
                conn.execute(DDL)
                got = conn.execute(SELECT, (self.world,)).fetchall()
                if hasattr(conn, "commit"):
                    conn.commit()
        except Exception as e:  # not read: the caller retries, the hunt stays off meanwhile
            self.log(f"egg_hunt: tried set not read ({type(e).__name__}); the hunt stays off, retrying")
            return None
        out = []
        for dealer, key, pid, tick, hour, status, thread, found, phrase, event in got:
            row = Tried(dealer, key, pid, int(tick), int(hour), status, int(thread or 0), found, phrase or "")
            out.append(replace(row, event=int(event or 0)))
        return out

    def save(self, rows: Sequence[Tried], tick: int) -> bool:
        if not rows:
            return True
        if self._down_at is not None and tick - self._down_at < RETRY_EVERY:
            return False
        args = [
            (self.world, r.dealer, r.key, r.pid, r.phrase, r.tick, r.hour, r.status, r.thread, r.found_tick, r.event)
            for r in rows
        ]
        try:
            with self.connect() as conn:
                conn.execute(f"set statement_timeout = {STATEMENT_TIMEOUT_MS}")
                conn.execute(DDL)
                for a in args:
                    conn.execute(UPSERT, a)
                if hasattr(conn, "commit"):
                    conn.commit()
        except Exception as e:  # kept in memory, retried
            self._down_at = tick
            if not self._failed:
                self.log(f"egg_hunt: Postgres write failed ({type(e).__name__}); kept, retry in {RETRY_EVERY} ticks")
            self._failed = True
            return False
        self._down_at, self._failed = None, False
        return True


class BackgroundStore:
    """The tried set's I/O off the tick (Sunday's ticks are 15 s): one daemon thread reads `inner` once, then writes
    every batch in order, retrying a failed one every `retry_s` seconds with its rows kept. `load` never blocks (None
    until the read is done, and the hunt waits for it: a phrase is never repeated because the set was not read yet);
    `save` only queues. A failed read is retried every `retry_s` seconds, and the hunt stays off until it succeeds.
    A batch still queued when the process dies is lost: at worst those phrases are said again."""

    def __init__(
        self,
        inner: TriedStore,
        log: Callable[[str], None],
        retry_s: float = 5.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.inner, self.log, self.retry_s, self.sleep = inner, log, retry_s, sleep
        self._rows: list[Tried] | None = None
        self._queue: queue.Queue[list[Tried]] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def _start(self) -> None:
        with self._lock:
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, name="egg-hunt-store", daemon=True)
                self._thread.start()

    def load(self) -> list[Tried] | None:
        self._start()
        return self._rows

    def save(self, rows: Sequence[Tried], tick: int) -> bool:
        self._start()
        self._queue.put(list(rows))
        return True

    def drain(self, timeout: float = 5.0) -> bool:
        """Every queued batch written (tests, and a clean shutdown): False after `timeout` seconds."""
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if self._rows is not None and self._queue.unfinished_tasks == 0:
                return True
            time.sleep(0.01)
        return False

    def _run(self) -> None:
        rows: list[Tried] | None = None
        while rows is None:  # until the read succeeds: the hunt stays off meanwhile (`load` answers None)
            try:
                rows = self.inner.load()
            except Exception as e:
                self.log(f"egg_hunt: tried set not read ({type(e).__name__}); the hunt stays off, retrying")
            if rows is None:
                self.sleep(self.retry_s)
        self._rows = list(rows)
        while True:
            batch = self._queue.get()
            tries = 0
            while True:
                try:
                    if self.inner.save(batch, tries * RETRY_EVERY):
                        break
                except Exception as e:
                    self.log(f"egg_hunt: store write failed ({type(e).__name__}); retrying")
                tries += 1
                self.sleep(self.retry_s)
            self._queue.task_done()


# ---------------------------------------------------------------- the hunter


@dataclass(frozen=True)
class Weave:
    dealer: str
    thread: int
    candidate: Candidate
    text: str  # the bid's words with the question appended
    live: bool  # False: dry, the bid goes out unchanged


@dataclass
class _Pending:
    weave: Weave
    tick: int
    message: int | None


@dataclass
class EggHunter:
    store: TriedStore
    log: Callable[[str], None]
    mode_fn: Callable[[], Mode] = field(default=lambda: mode_from_env())
    seeds: Mapping[str, Sequence[tuple[str, Source, float]]] = field(default_factory=lambda: SEEDS)

    def __post_init__(self) -> None:
        self.tried: dict[tuple[str, str], Tried] = {}
        self._loaded = False  # the store is read on the first tick the hunt is on: off, it never touches Postgres
        self.mined: dict[tuple[str, str], Candidate] = {}
        self._seeds = seed_candidates(self.seeds)
        self.pending: dict[str, _Pending] = {}
        self.backoff: dict[str, tuple[int, str]] = {}  # dealer ("*": all) -> (until tick, why)
        self.messages: set[int] = set()  # ids of our woven messages (a flag on one stops the hunt)
        self.dry: dict[tuple[str, str], int] = {}  # dry run: (dealer, key) -> tick (memory only, never stored)
        self._unsaved: list[Tried] = []
        self._seen: set[int] = set()
        self._read: set[int] = set()  # dealer message ids already checked for a warning
        self._hint_teams: dict[tuple[str, str], set[str]] = {}  # mined hint -> teams whose threads gave it
        self._hidden: set[str] | None = None
        self._woven: tuple[int | None, int] = (None, 0)  # (tick, woven messages that tick)
        self._said: dict[tuple[str, str], int] = {}  # (dealer, reason) -> game hour a skip was last logged

    # ------------------------------------------------------------ switches and budget

    def mode(self, rules: Any) -> Mode:
        mode = self.mode_fn() if getattr(rules, "egg_hunt_enabled", False) else "off"
        if mode != "off" and not self._loaded:
            rows = self.store.load()
            if rows is None:  # still being read off the tick: no phrase until we know what was tried
                return "off"
            self._loaded = True
            for row in rows:
                self.tried.setdefault((row.dealer, row.key), row)
        return mode

    def finds(self, dealer: str | None = None) -> int:
        return sum(1 for r in self.tried.values() if r.status == "found" and dealer in (None, r.dealer))

    def sent_this_hour(self, dealer: str, hour: int) -> int:
        live = sum(1 for r in self.tried.values() if r.dealer == dealer and r.hour == hour and r.pid != "-")
        return live + sum(1 for (d, _), h in self.dry.items() if d == dealer and h == hour)

    def candidates(self, dealer: str) -> list[Candidate]:
        """This dealer's untried candidates, best first (a phrase tried with it, live or in this dry run, never
        comes back)."""
        pool: dict[str, Candidate] = {}
        for c in [*self._seeds, *self.mined.values()]:
            if c.dealer != dealer or (dealer, c.key) in self.tried or (dealer, c.key) in self.dry:
                continue
            if c.source == "hint" and not self._trusted_hint(dealer, c.key):
                continue
            old = pool.get(c.key)
            pool[c.key] = c if old is None or c.score > old.score else old
        return sorted(pool.values(), key=lambda c: (TIER[c.source], -c.score, c.key))

    def blocked(self, rules: Any, dealer: str, tick: int, hour: int) -> str | None:
        """Why no phrase goes to `dealer` this tick (None: one may)."""
        allowed = {d.strip() for d in str(getattr(rules, "egg_hunt_dealers", "")).split(",") if d.strip()}
        if dealer not in allowed:
            return "dealer not in egg_hunt_dealers"
        if self.finds() >= rules.egg_hunt_max_finds:
            return f"cap: {self.finds()} finds"
        if self.finds(dealer) >= rules.egg_hunt_max_finds_per_dealer:
            return "egg found with this dealer"
        for who in (dealer, "*"):
            until, why = self.backoff.get(who, (0, ""))
            if tick < until:
                return f"backoff until tick {until} ({why})"
        if self._woven[0] == tick and self._woven[1] >= MAX_PER_TICK:
            return "one woven message per tick"
        if (p := self.pending.get(dealer)) is not None and tick - p.tick < rules.egg_hunt_dealer_gap_ticks:
            return f"waiting for the reply to {p.weave.candidate.id}"
        if self.sent_this_hour(dealer, hour) >= rules.egg_hunt_max_phrases_per_dealer_per_hour:
            return "hourly budget used"
        return None

    # ------------------------------------------------------------ the weave (inside the taker's bid)

    def weave(
        self,
        rules: Any,
        *,
        tick: int,
        hour: int,
        dealer: str,
        thread: int,
        text: str,
        final: bool = False,
        blocker: str | None = None,
        never_address: Iterable[str] = (),
    ) -> Weave | None:
        """The bid's words with one candidate phrase asked about at the end, or None (the bid goes out as it was).
        Never raises: any failure is a skip."""
        try:
            mode = self.mode(rules)
            if mode == "off":
                return None
            why = (
                "her final offer stands"
                if final
                else (f"learned blocker: {blocker}" if blocker else self.blocked(rules, dealer, tick, hour))
            )
            if why is not None:
                self._skip(dealer, why, hour, tick)
                return None
            for cand in self.candidates(dealer):
                woven = f"{text.rstrip()} {carrier(dealer, cand.phrase)}"
                if len(woven) > MESSAGE_MAX_CHARS or injection_flags(woven):
                    continue
                if uses_forbidden(woven, (*NEVER_ADDRESS, *never_address)):
                    continue
                self._woven = (tick, self._woven[1] + 1 if self._woven[0] == tick else 1)
                if mode == "dry":
                    self.dry[(dealer, cand.key)] = hour
                    self.log(f"tick {tick} egg_hunt would-send dealer={dealer} phrase={cand.id} text={woven!r}")
                return Weave(dealer, thread, cand, woven, mode == "live")
            self._skip(dealer, "no untried candidate", hour, tick)
        except Exception as e:  # the hunt never costs the bid
            self.log(f"tick {tick} egg_hunt skipped dealer={dealer} reason=error {type(e).__name__}")
        return None

    def sent(self, w: Weave, tick: int, hour: int, message: int | None) -> None:
        """The woven bid landed: the phrase is tried with this dealer for good (stored after the sends)."""
        if not w.live:
            return
        c = w.candidate
        row = Tried(c.dealer, c.key, c.id, tick, hour, "sent", w.thread, phrase=c.phrase)
        self.tried[(c.dealer, c.key)] = row
        self._unsaved.append(row)
        self.pending[w.dealer] = _Pending(w, tick, message)
        if message is not None:
            self.messages.add(message)
        self.log(f"tick {tick} egg_hunt sent dealer={w.dealer} phrase={c.id} source={c.source} thread={w.thread}")

    def _skip(self, dealer: str, why: str, hour: int, tick: int) -> None:
        reason = why.split(" (")[0].split(":")[0]
        if self._said.get((dealer, reason)) != hour:  # once per dealer, reason and game hour
            self._said[(dealer, reason)] = hour
            self.log(f"tick {tick} egg_hunt skipped dealer={dealer} reason={why}")

    # ------------------------------------------------------------ what the taker already read

    def observe(
        self,
        rules: Any,
        *,
        tick: int,
        hour: int,
        us: str | None,
        events: Iterable[Mapping[str, Any]],
        threads: Iterable[Mapping[str, Any]] = (),
        catalog: Mapping[str, Any] | None = None,
        held: Iterable[str] = (),
    ) -> None:
        """Mine hints, detect finds and back-off signals. No request; never raises."""
        if self.mode(rules) == "off" or not us:
            return
        try:
            self._events(rules, list(events), us, tick, hour)
            for t in threads:
                self.thread(rules, t, tick)
            self._catalog(catalog, set(held), tick, hour)
            self._expire(rules, tick)
        except Exception as e:  # a reading bug never costs the tick
            self.log(f"tick {tick} egg_hunt observe failed ({type(e).__name__})")

    def _events(self, rules: Any, events: list[Mapping[str, Any]], us: str, tick: int, hour: int) -> None:
        for e in sorted(events, key=_event_order):
            eid, payload, kind = e.get("id"), e.get("payload"), e.get("type")
            if not isinstance(eid, int) or eid in self._seen or not isinstance(payload, Mapping):
                continue
            self._seen.add(eid)
            actor = str(e.get("actor") or "")
            if kind == "thread.message" and actor in DEALER_NAMES:
                for target, phrase in mine_hints(payload.get("text"), actor):
                    self._mined(target, phrase, str(payload.get("team") or ""), us)
            elif kind in FOUND_EVENTS and payload.get("team") == us:
                at = e.get("tick")
                self._found(str(payload.get("persona") or ""), str(kind), eid, payload, at, tick, hour)
            elif kind in BACKOFF_EVENTS and payload.get("team") == us:
                dealer = str(payload.get("persona") or "")
                end = _until(payload.get("until_tick"), e.get("tick"), tick, rules.egg_hunt_backoff_ticks)
                if end is None:  # a replayed window after a restart: that cool-off is already over
                    continue
                self._punished(rules, dealer, end, str(kind), tick)
            elif kind == "flag.raised" and payload.get("message", payload.get("message_id")) in self.messages:
                self._back_off("*", tick + 100 * rules.egg_hunt_backoff_ticks, "a team flagged a woven message", tick)
        if len(self._seen) > SEEN_MAX:
            self._seen = set(sorted(self._seen)[-SEEN_MAX // 2 :])

    def _mined(self, dealer: str, phrase: str, team: str, us: str) -> None:
        """A hint a dealer gave in a reply. Any team can prompt-inject a dealer into "ask Pilar about <anything>",
        so one only counts from our own thread or once `HINT_MIN_TEAMS` different teams were given it."""
        key = (dealer, fold(phrase))
        self._hint_teams.setdefault(key, set()).add("@us" if team == us else team)
        old = self.mined.get(key)
        self.mined[key] = replace(old, seen=old.seen + 1) if old else Candidate(dealer, phrase, "hint", 0.5)
        if len(self.mined) > MINED_MAX * len(DEALER_NAMES):
            weakest = min(self.mined, key=lambda k: self.mined[k].score)
            self.mined.pop(weakest)

    def _trusted_hint(self, dealer: str, key: str) -> bool:
        teams = self._hint_teams.get((dealer, key), set())
        return "@us" in teams or len(teams - {""}) >= HINT_MIN_TEAMS

    def _found(
        self, dealer: str, kind: str, eid: int, payload: Mapping[str, Any], at: object, tick: int, hour: int
    ) -> None:
        reward = payload.get("badge") or ",".join(str(x) for x in [*(payload.get("cards") or [])])
        reward = reward or ",".join(str(x) for x in payload.get("packs") or [])
        if kind != "egg.found":  # the reward line of a find: logged, the find itself is counted once
            self.log(f"tick {tick} egg_hunt reward kind={kind} reward={clean(reward, 40) or '-'}")
            return
        if any(r.event == eid for r in self.tried.values()):  # stored before a restart: counted once
            return
        p = self.pending.get(dealer)
        if p is not None and (not isinstance(at, int) or at >= p.tick):
            c = p.weave.candidate
            row = replace(self.tried[(dealer, c.key)], status="found", found_tick=tick, event=eid)
            self.pending.pop(dealer)
        else:  # a find no woven phrase explains (a hand-sent message, an egg on chance): counted all the same
            row = Tried(dealer, f"@find:{eid}", "-", tick, hour, "found", found_tick=tick, event=eid)
        self.tried[(dealer, row.key)] = row
        self._unsaved.append(row)
        self.log(f"tick {tick} egg_hunt found dealer={dealer} phrase={row.pid} finds={self.finds()}")

    def thread(self, rules: Any, t: Mapping[str, Any], tick: int, dealer: str | None = None) -> None:
        """One dealer thread of ours as the taker read it (no request): a cool-off close, or a warning in the
        dealer's reply to a woven message, backs off. Never raises."""
        if self.mode(rules) == "off" or not isinstance(t, Mapping):
            return
        try:
            dealer = dealer or str(t.get("with") or "")
            if t.get("closed_reason") == "cooloff":
                end = _until(t.get("until_tick"), None, tick, rules.egg_hunt_backoff_ticks)
                if end is not None:
                    self._punished(rules, dealer, end, "thread closed for cool-off", tick)
            last = self._recent(dealer, tick, rules.egg_hunt_backoff_ticks)
            if last is None or t.get("id") != last.thread:
                return
            for m in t.get("messages") or []:
                if not isinstance(m, Mapping) or m.get("sender") != dealer or m.get("id") in self._read:
                    continue
                at = m.get("tick")
                if isinstance(at, int) and at < last.tick:
                    continue
                if isinstance(m.get("id"), int):
                    self._read.add(m["id"])
                if WARNING.search(fold(clean(m.get("text"), 1200))):
                    self._punished(rules, dealer, tick + rules.egg_hunt_backoff_ticks, "a warning in the reply", tick)
        except Exception as e:
            self.log(f"tick {tick} egg_hunt thread read failed ({type(e).__name__})")

    def _catalog(self, catalog: Mapping[str, Any] | None, held: set[str], tick: int, hour: int) -> None:
        if not isinstance(catalog, Mapping):
            return
        hidden = {
            str(c.get("id"))
            for s in catalog.get("sets") or []
            if isinstance(s, Mapping)
            for c in s.get("cards") or []
            if isinstance(c, Mapping) and c.get("hidden")
        }
        if self._hidden is not None:
            for ref in sorted(hidden - self._hidden):
                self.log(f"tick {tick} egg_hunt hidden card {clean(ref, 12)} appeared (held by us: {ref in held})")
        self._hidden = hidden

    def _expire(self, rules: Any, tick: int) -> None:
        """A woven phrase with no find inside the gap is just tried: the dealer is free for the next one."""
        for dealer, p in list(self.pending.items()):
            if tick - p.tick >= max(rules.egg_hunt_dealer_gap_ticks, 2):
                self.pending.pop(dealer)

    def _recent(self, dealer: str, tick: int, window: int) -> Tried | None:
        """Our newest phrase to `dealer` said within the last `window` ticks (stored, so a restart keeps it)."""
        rows = [r for r in self.tried.values() if r.dealer == dealer and r.pid != "-" and 0 <= tick - r.tick <= window]
        return max(rows, key=lambda r: r.tick) if rows else None

    def _punished(self, rules: Any, dealer: str, until: int, why: str, tick: int) -> None:
        """A cool-off, strike or warning: back off that dealer; and every dealer when we said a phrase to it within
        the back-off window (the judge's strikes may be silent, and they add up toward a cool-off)."""
        self._back_off(dealer, until, why, tick)
        if dealer in self.pending or self._recent(dealer, tick, rules.egg_hunt_backoff_ticks) is not None:
            self._back_off("*", max(until, tick + rules.egg_hunt_backoff_ticks), f"{why} after a recent phrase", tick)

    def _back_off(self, dealer: str, until: int, why: str, tick: int) -> None:
        if not dealer:
            return
        old = self.backoff.get(dealer, (0, ""))[0]
        if until > old:
            self.backoff[dealer] = (until, why)
            self.log(f"tick {tick} egg_hunt backoff dealer={dealer} until={until} reason={why}")

    def flush(self, tick: int) -> None:
        """After the sends: the new tried/found rows, one bounded write (kept and retried on a failure)."""
        if not self._unsaved:
            return
        try:
            if self.store.save(list(self._unsaved), tick):
                self._unsaved = []
        except Exception as e:
            self.log(f"tick {tick} egg_hunt store failed ({type(e).__name__}); kept")


def _until(until: object, at: object, tick: int, backoff: int) -> int | None:
    """When a cool-off read now ends: its own `until_tick`, else `backoff` ticks after the event (or now). None when
    it already ended (a feed window replayed after a restart)."""
    if isinstance(until, int) and not isinstance(until, bool):
        return until if until > tick else None
    start = at if isinstance(at, int) and not isinstance(at, bool) else tick
    return start + backoff if start + backoff > tick else None


def _event_order(e: Mapping[str, Any]) -> int:
    eid = e.get("id")
    return eid if isinstance(eid, int) else 0


CARRIERS: dict[str, str] = {
    # The organisers' own test message for an egg is "Do you know about <phrase>?" (persona editor). A question,
    # no claim, no instruction; Abuela gets a warm one (she remembers how she was treated). The phrase stands on its
    # own (no "de" before it), so "el …" never reads as the "de el" of a template and stays whole.
    "abuela": "Gracias por su paciencia, de verdad. Una pregunta, si me permite: {phrase}, ¿le suena?",
    "picaros": "Por cierto: {phrase}, ¿os suena?",
    "chato": "Por cierto: {phrase}, ¿le suena?",
    "pilar": "Por cierto, una pregunta: {phrase}, ¿le suena?",
    "banco": "Por cierto, una pregunta: {phrase}, ¿le suena?",
}


def carrier(dealer: str, phrase: str) -> str:
    return CARRIERS.get(dealer, "By the way, do you know about {phrase}?").format(phrase=phrase)
