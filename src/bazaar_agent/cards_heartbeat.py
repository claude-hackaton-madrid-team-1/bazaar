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
`card_release_boost_ticks` ticks after it, `boost()` hands strategy a rank multiplier for those cards (GUARDRAILS
`card_release_boost_enabled`): ranking only, every buy still passes guardrails and the official-value cap.
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
KEEP_EVENTS = 200  # the file keeps the newest events only
KEYS = frozenset({"set", "rarity", "minted", "print_run", "visible"})
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
                    "minted": int(c.get("minted") or 0),
                    "print_run": int(c.get("print_run") or 0),
                    "visible": bool(s.get("released", True)) and not c.get("hidden"),
                }
    return out


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
        elif c["minted"] - old["minted"] >= jump:
            kind = "minted_jump"
        else:
            continue
        sold, bought, packs = dealer_lines(menus, c["set"], c["rarity"])
        out.append(CardEvent(kind, ref, c["set"], c["rarity"], tick, c["minted"], c["print_run"], sold, bought, packs))
    return out


def advance(
    before: Mapping[str, Mapping[str, Any]], now: Mapping[str, Mapping[str, Any]], jump: int = MINTED_JUMP
) -> dict[str, dict[str, Any]]:
    """The next baseline: `now`, except a minted count rising slower than a jump keeps its last reported value
    (so a card printed one copy a tick still reports a jump once it adds up)."""
    out = {}
    for ref, c in now.items():
        old = before.get(ref)
        keep = old is not None and old["visible"] and c["visible"] and 0 <= c["minted"] - old["minted"] < jump
        out[ref] = {**c, "minted": old["minted"]} if keep and old is not None else dict(c)
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
    """card ref -> rank multiplier for the cards released or reprinted in the last `card_release_boost_ticks`
    ticks; nothing while `card_release_boost_enabled` is false."""
    if not rules.card_release_boost_enabled:
        return {}
    return {ev.card: BOOST for ev in events if 0 <= tick - ev.tick < rules.card_release_boost_ticks}


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

    def boost(self, tick: int) -> dict[str, float]:
        return boost(self.rules, self.events, tick)

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
        except OSError as e:
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
            self.baseline = {str(k): dict(v) for k, v in raw.items() if isinstance(v, dict) and v.keys() >= KEYS}
            rows = [r for r in body.get("events") or [] if isinstance(r, dict)]
            self.events = [CardEvent(**{**r, **{k: tuple(r.get(k) or ()) for k in LISTS}}) for r in rows]
        except (OSError, ValueError, TypeError, AttributeError):
            self.baseline, self.events = {}, []
