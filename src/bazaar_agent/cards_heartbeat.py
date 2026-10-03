"""The cards heartbeat: a new card in the catalog is a new trading opportunity (the public /cards page).

When the organisers release a set (El Retiro Saturday, one more Sunday), unhide a card or print a burst of copies,
`/api/catalog` shows it first: a dealer starts selling it and the teams chasing that page start paying for it. The
taker already reads the catalog and `/api/dealers` every tick, so this module costs no request: it diffs each
tick's catalog against the last one it saw (kept in `card_events.json`, so a restart still sees what changed while
it was down) and reports three kinds of event:

- `new_card`: a card the catalog did not show before (or showed hidden) in a released set;
- `set_released`: every card of a set that turned `released`;
- `minted_jump`: a card whose minted count rose by `MINTED_JUMP` or more since the last report.

Each event is stored once in the learnings store (kind `card_release`, deduped by its key), logged, and written to
`<data_dir>/agents/card_events.json` with the dealers that sell or buy it per their `/api/dealers` menus. For
`card_release_boost_ticks` ticks after a new card or a released set (never a minted jump), `boost()` hands the
taker a rank multiplier for those cards (GUARDRAILS `card_release_boost_enabled`): the order of its buys and dealer
openings only, every buy still passes guardrails and the official-value cap. The hint file is read back typed: a
row that does not parse is dropped, and a heartbeat failure is logged, never raised into the tick.
Detection runs in memory before the sends; the store and the file are written after them (`flush`).
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from bazaar_agent.guardrails import Guardrails
from bazaar_agent.learn.model import Learning

EVENTS_FILE = "card_events.json"
MINTED_JUMP = 3  # copies printed since the last report that make a jump worth a hint
BOOST = 1.5  # rank multiplier of a boosted card's score (ranking only)
MAX_COUNT = 2**31 - 1  # a tick or a count beyond this is not a game number (and would not fit the store)
KEEP_EVENTS = 200  # the file keeps the newest events only
KINDS = frozenset({"new_card", "set_released", "minted_jump"})
BOOSTED = frozenset({"new_card", "set_released"})  # a minted jump is a hint, never a rank boost
LISTS = ("sold_by", "bought_by", "packs")
SAFE = re.compile(r"[^A-Za-z0-9_.:\-]")


@dataclass(frozen=True)
class CardEvent:
    kind: str  # new_card | set_released | minted_jump
    card: str
    set: str
    rarity: str
    tick: int
    minted: int
    print_run: int
    sold_by: tuple[str, ...]  # dealers whose menu sells this rarity of this set
    bought_by: tuple[str, ...]  # dealers whose menu buys it
    packs: tuple[str, ...]  # "dealer:pack" lines that may pull it

    def line(self) -> str:
        sold = ", ".join(self.sold_by) or "no dealer"
        bought = ", ".join(self.bought_by) or "no dealer"
        what = {"new_card": "new card", "set_released": "set released", "minted_jump": f"minted {self.minted}"}
        head = f"{what.get(self.kind, self.kind)}: {self.card} ({self.rarity}, {self.set})"
        return f"{head} · sold by {sold} · bought by {bought}"


def _str(value: Any, cap: int = 64) -> str:
    return SAFE.sub("_", str(value or ""))[:cap]


def catalog_cards(catalog: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """card ref -> {set, rarity, minted, print_run, visible}: visible = its set is released (unsaid: released)
    and the card is not hidden."""
    out: dict[str, dict[str, Any]] = {}
    for s in catalog.get("sets") or []:
        if not isinstance(s, dict) or not s.get("id"):
            continue
        for c in s.get("cards") or []:
            if isinstance(c, dict) and c.get("id"):
                out[_str(c["id"])] = {
                    "set": _str(s["id"]),
                    "rarity": _str(c.get("rarity"), 32),
                    "minted": _count(c.get("minted")),  # None: not said this read (never a jump)
                    "print_run": _count(c.get("print_run")) or 0,
                    "visible": bool(s.get("released", True)) and not c.get("hidden"),
                }
    return out


def _count(value: Any) -> int | None:
    """A non-negative whole count, or None (missing, a bool, a float that is not whole, text)."""
    if isinstance(value, bool) or not isinstance(value, int | float) or value != value or value < 0:
        return None
    if isinstance(value, int):
        return value if value <= MAX_COUNT else None
    return int(value) if value.is_integer() and value <= MAX_COUNT else None  # inf is not an integer


def _entry(value: Any) -> dict[str, Any] | None:
    """One baseline entry read back from the hint file, typed, or None."""
    if not isinstance(value, dict) or not isinstance(value.get("visible"), bool):
        return None
    minted = value.get("minted")
    if minted is not None and _count(minted) is None:
        return None
    return {
        "set": _str(value.get("set")),
        "rarity": _str(value.get("rarity"), 32),
        "minted": None if minted is None else _count(minted),
        "print_run": _count(value.get("print_run")) or 0,
        "visible": value["visible"],
    }


def _event(row: Any) -> CardEvent | None:
    """One event read back from the hint file, typed, or None (a tampered or older row is dropped)."""
    if not isinstance(row, dict) or row.get("kind") not in KINDS:
        return None
    tick, minted = _count(row.get("tick")), _count(row.get("minted"))
    card = _str(row.get("card")) if isinstance(row.get("card"), str) else ""
    if tick is None or minted is None or not card:
        return None
    lists: list[list[Any]] = [v if isinstance(v := row.get(k), list) else [] for k in LISTS]
    sold, bought, packs = (tuple(_str(x) for x in v if isinstance(x, str))[:20] for v in lists)
    return CardEvent(
        row["kind"], card, _str(row.get("set")), _str(row.get("rarity"), 32), tick, minted,
        _count(row.get("print_run")) or 0, sold, bought, packs,
    )  # fmt: skip


def _covers(line: Mapping[str, Any], set_code: str) -> bool:
    sets = line.get("sets")
    if isinstance(sets, list):
        return set_code in {str(x) for x in sets}
    return sets in (None, "released") or str(sets) == set_code


def dealer_lines(dealers: Iterable[Mapping[str, Any]], set_code: str, rarity: str) -> tuple[tuple[str, ...], ...]:
    """(sold_by, bought_by, packs) for one card, from the dealers' menus (`/api/dealers`)."""
    sold, bought, packs = set(), set(), set()
    for d in dealers:
        did, menu = _str(d.get("id")), d.get("menu") or {}
        if not did or not isinstance(menu, dict):
            continue
        for line in menu.get("sells") or []:
            if not isinstance(line, dict) or not _covers(line, set_code):
                continue
            if line.get("pack"):
                packs.add(f"{did}:{_str(line['pack'])}")
            elif line.get("rarity") == rarity:
                sold.add(did)
        for line in menu.get("buys") or []:
            if isinstance(line, dict) and line.get("rarity") == rarity and _covers(line, set_code):
                bought.add(did)
    return tuple(sorted(sold)), tuple(sorted(bought)), tuple(sorted(packs))


def detect(
    before: Mapping[str, Mapping[str, Any]],
    now: Mapping[str, Mapping[str, Any]],
    dealers: Iterable[Mapping[str, Any]],
    tick: int,
    jump: int = MINTED_JUMP,
) -> list[CardEvent]:
    """What changed from `before` to `now` (both `catalog_cards`). Nothing is new on the first look (empty `before`)."""
    if not before:
        return []
    menus = list(dealers)
    shown_before = {b["set"] for b in before.values() if b["visible"]}
    released_sets = {c["set"] for c in now.values() if c["visible"] and c["set"] not in shown_before}
    out = []
    for ref, c in sorted(now.items()):
        old = before.get(ref)
        if not c["visible"]:
            continue
        if old is None or not old["visible"]:
            kind = "set_released" if c["set"] in released_sets else "new_card"
        elif c["minted"] is not None and old["minted"] is not None and c["minted"] - old["minted"] >= jump:
            kind = "minted_jump"
        else:
            continue
        sold, bought, packs = dealer_lines(menus, c["set"], c["rarity"])
        minted = c["minted"] if c["minted"] is not None else 0
        out.append(CardEvent(kind, ref, c["set"], c["rarity"], tick, minted, c["print_run"], sold, bought, packs))
    return out


def advance(
    before: Mapping[str, Mapping[str, Any]], now: Mapping[str, Mapping[str, Any]], jump: int = MINTED_JUMP
) -> dict[str, dict[str, Any]]:
    """The next baseline: `now`, except a minted count rising slower than a jump (or not said) keeps its last
    reported value (so a card printed one copy a tick still reports a jump once it adds up), and a card or set the
    read left out keeps its entry (a glitchy read is never "released again" on the next one)."""
    out = {ref: dict(c) for ref, c in before.items() if ref not in now}
    for ref, c in now.items():
        old = before.get(ref)
        if old is None or not old["visible"] or not c["visible"] or old["minted"] is None:
            out[ref] = dict(c)
        elif c["minted"] is None or 0 <= c["minted"] - old["minted"] < jump:
            out[ref] = {**c, "minted": old["minted"]}
        else:
            out[ref] = dict(c)
    return out


def learning_of(ev: CardEvent) -> Learning:
    detail: dict[str, Any] = {"item": ev.card, "code": ev.kind, "rarity": ev.rarity, "set": ev.set}
    detail.update(minted=ev.minted, print_run=ev.print_run, sold_by=list(ev.sold_by), bought_by=list(ev.bought_by))
    detail["packs"] = list(ev.packs)
    if ev.kind == "minted_jump":
        detail["effective_tick"] = ev.tick  # a later jump of the same card is its own row
    return Learning(
        subject_kind="organiser",
        subject=ev.set or "catalog",
        kind="card_release",
        tick=max(0, ev.tick),
        confidence=1.0,  # the official catalog
        text=ev.line(),
        detail=detail,
    )


def boost(rules: Guardrails, events: Iterable[CardEvent], tick: int) -> dict[str, float]:
    """card ref -> rank multiplier for the cards released (a new card, a released set) in the last
    `card_release_boost_ticks` ticks; nothing while `card_release_boost_enabled` is false. A minted jump is logged
    and stored only: other teams' pulls make a card more plentiful, never a reason to chase it (security #185)."""
    if not rules.card_release_boost_enabled:
        return {}
    ticks = rules.card_release_boost_ticks
    return {ev.card: BOOST for ev in events if ev.kind in BOOSTED and 0 <= tick - ev.tick < ticks}


class CardsHeartbeat:
    """`observe` before the sends (memory only), `flush` after them. Neither ever raises."""

    def __init__(
        self,
        rules: Guardrails,
        record: Callable[[list[Learning]], object],
        log: Callable[[str], None],
        out_dir: Path,
    ) -> None:
        self.rules, self.record, self.log = rules, record, log
        self.path = out_dir / EVENTS_FILE
        self.baseline: dict[str, dict[str, Any]] = {}
        self.events: list[CardEvent] = []
        self._pending: list[CardEvent] = []
        self._dropped: set[str] = set()  # cards whose boosted opening guardrails refused
        self._load()

    def observe(self, tick: int, catalog: Mapping[str, Any], dealers: Iterable[Mapping[str, Any]]) -> list[CardEvent]:
        try:
            now = catalog_cards(catalog)
            if not now:
                return []  # an empty or failed catalog read is never "every card vanished"
            fresh = detect(self.baseline, now, dealers, tick)
            self.baseline = advance(self.baseline, now) if self.baseline else now
            self._pending += fresh
            self.events = (self.events + fresh)[-KEEP_EVENTS:]
            return fresh
        except Exception as e:  # noqa: BLE001 — a hint only: the heartbeat never breaks a tick
            self.log(f"tick {tick} cards: skipped ({type(e).__name__})")
            return []

    def unboost(self, card: str) -> None:
        """Guardrails refused this card's opening (e.g. no official value above the ladder start): it goes back to
        today's order for the rest of its window, so it never holds a dealer's slot tick after tick (#185 review)."""
        self._dropped.add(card)

    def boost(self, tick: int) -> dict[str, float]:
        try:
            return {k: v for k, v in boost(self.rules, self.events, tick).items() if k not in self._dropped}
        except Exception as e:  # noqa: BLE001 — no boost is today's ranking: never break a tick
            self.log(f"tick {tick} cards: no boost ({type(e).__name__})")
            return {}

    def flush(self, tick: int) -> None:
        """After the sends: log, store and write the hint file (also the first baseline)."""
        fresh, self._pending = self._pending, []
        for ev in fresh:
            self.log(f"tick {tick} cards: {ev.line()}")
        try:
            if fresh:
                self.record([learning_of(ev) for ev in fresh])
        except Exception as e:  # noqa: BLE001 — memory keeps the events; the file and the log still say them
            self.log(f"tick {tick} cards: learnings not stored ({type(e).__name__})")
        try:
            if fresh or not self.path.exists():
                self._write(tick)
        except Exception as e:  # noqa: BLE001 — the file is a hint: never break the after-sends work
            self.log(f"tick {tick} cards: {EVENTS_FILE} not written ({type(e).__name__})")

    def _write(self, tick: int) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        active = boost(self.rules, self.events, tick)
        body = {
            "tick": tick,
            "boost_enabled": self.rules.card_release_boost_enabled,
            "boosted": sorted(active),
            "events": [asdict(ev) for ev in self.events],
            "baseline": self.baseline,
        }
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(body, ensure_ascii=False, indent=1))
        tmp.replace(self.path)

    def _load(self) -> None:
        """The last run's baseline and events: a release while we were down is still seen. Unreadable: start fresh."""
        try:
            body = json.loads(self.path.read_text())
            raw = body.get("baseline") or {}
            entries = {_str(k): _entry(v) for k, v in raw.items() if isinstance(k, str)}
            # A baseline that lost an entry is no baseline: its cards would all read as fresh releases.
            ok = all(k and v is not None for k, v in entries.items())
            self.baseline = {k: v for k, v in entries.items() if v is not None} if ok else {}
            rows = body.get("events") if isinstance(body.get("events"), list) else []
            self.events = [ev for ev in map(_event, rows[-KEEP_EVENTS:]) if ev is not None]
        except Exception:  # noqa: BLE001 — the hint file is optional: whatever it holds, start fresh
            self.baseline, self.events = {}, []
