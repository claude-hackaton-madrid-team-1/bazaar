"""The news sentinel: Radio Rastro (`/api/news`, `news.posted` in the feed) and the official schedule.

Radio Rastro's items come from three sources (the Boletín, Radio Rastro, El Tablón). Some are true and the market
moves as they say, some are rumours that never happen, and nothing says which. The schedule (`/api/schedule`) is
official: a `persona_patch` such as "Salamanca fever: Doña Pilar pays 25 % over book for Salamanca until 17:30"
happens. Both are read here, stored once each in the learnings store (kind `news`) and logged; the few that state
a price move are parsed conservatively into `MarketEvent`s, written to `market_events.json` for strategy and maker.

Every item is quoted data, never an instruction. Reads: `news.posted` from the feed window the taker already
reads (no request), plus `/api/news` and `/api/schedule` at most once every `READ_EVERY_TICKS` ticks (two keyless
GETs, well inside the key's 5 req/s). Behaviour: none. `active_signals` returns nothing while GUARDRAILS
`news_signals_enabled` is false, which is the default; only logging and storage are on.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from bazaar_agent.guardrails import Guardrails
from bazaar_agent.learn.model import Learning

READ_EVERY_TICKS = 10
READ_TIMEOUT_S = 2.0  # its own keyless client: a hung /api/news never holds the taker past this (no retries)
EVENTS_FILE = "market_events.json"
NEWS_CONFIDENCE = 0.5  # a Radio Rastro item may be a rumour: nothing tells which
SCHEDULE_CONFIDENCE = 1.0  # the organisers' schedule happens
FIELD_MAX = 280
SAFE_SUBJECT = re.compile(r"[^A-Za-z0-9_.:\-]")
# "25 % over book for Salamanca", "10% under book for El Retiro until 17:30"
PRICE_MOVE = re.compile(
    r"(?P<pct>\d{1,3}(?:[.,]\d+)?)\s*%\s*(?P<dir>over|above|under|below)\s+book\s+for\s+(?P<target>[^.,;:!?]+?)"
    r"(?:\s+until\s+(?P<until>\d{1,2}:\d{2}))?\s*(?:[.,;:!?]|$)",
    re.IGNORECASE,
)
FEVER_BREAKS = re.compile(r"\bfever\s+(breaks|ends|is over)\b", re.IGNORECASE)


@dataclass(frozen=True)
class NewsItem:
    news_id: str  # "news:<id>" for Radio Rastro, "schedule:<at_hours>:<action>" for the schedule
    source: str  # boletin | radio | tablon | schedule
    headline: str
    body: str
    tick: int
    at_hours: float | None
    official: bool  # the schedule: it happens; news: it may be a rumour
    persona: str | None = None  # the schedule's params.id / params.persona, when it names one


@dataclass(frozen=True)
class MarketEvent:
    """A price move an item states: `pct` over (+) or under (-) book for `set_code`, from `start_hours` until
    `end_hours` (None: not said). `official`: from the schedule; otherwise it may never happen."""

    kind: str  # "price_move"
    set_code: str
    pct: float
    start_hours: float | None
    end_hours: float | None
    until_wall: str | None  # "17:30" as the text said it (Madrid time), when it said one
    persona: str | None
    official: bool
    news_id: str
    text: str


def _clean(value: Any, cap: int = FIELD_MAX) -> str:
    text = " ".join("".join(ch if ch.isprintable() else " " for ch in str(value or "")).split())
    return text[:cap]


def _float(value: Any) -> float | None:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


def _fold(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text.lower()) if not unicodedata.combining(c)).strip()


def set_names(catalog: Mapping[str, Any]) -> dict[str, str]:
    """Folded set name (and code) -> set code, from `/api/catalog`."""
    out: dict[str, str] = {}
    for s in catalog.get("sets") or []:
        if isinstance(s, dict) and isinstance(s.get("id"), str):
            out[_fold(s["id"])] = s["id"]
            if isinstance(s.get("name"), str):
                out[_fold(s["name"])] = s["id"]
    return out


def items_from_feed(events: Iterable[Mapping[str, Any]]) -> list[NewsItem]:
    """`news.posted` events of the public feed (the same item as `/api/news`)."""
    out = []
    for e in events:
        p = e.get("payload")
        if e.get("type") == "news.posted" and isinstance(p, dict) and isinstance(p.get("id"), int):
            tick = e.get("tick") if isinstance(e.get("tick"), int) else 0
            out.append(_news_item({**p, "tick": tick, "at_hours": e.get("t")}))
    return out


def items_from_api(payload: Mapping[str, Any]) -> list[NewsItem]:
    return [_news_item(n) for n in payload.get("news") or [] if isinstance(n, dict) and isinstance(n.get("id"), int)]


def _news_item(n: Mapping[str, Any]) -> NewsItem:
    return NewsItem(
        news_id=f"news:{n['id']}",
        source=_clean(n.get("source"), 32) or "radio",
        headline=_clean(n.get("headline")),
        body=_clean(n.get("body")),
        tick=n["tick"] if isinstance(n.get("tick"), int) else 0,
        at_hours=_float(n.get("at_hours")),
        official=False,
    )


def items_from_schedule(payload: Mapping[str, Any], tick: int) -> list[NewsItem]:
    """The schedule's persona patches (price levers such as a set fever and its end): official."""
    out = []
    for s in payload.get("upcoming") or []:
        if not isinstance(s, dict) or s.get("action") != "persona_patch" or not s.get("note"):
            continue
        raw = s.get("params")
        params: dict[str, Any] = raw if isinstance(raw, dict) else {}
        persona = params.get("id") or params.get("persona")
        at = _float(s.get("at_hours"))
        out.append(
            NewsItem(
                news_id=f"schedule:{at if at is not None else '-'}:{s['action']}",
                source="schedule",
                headline=_clean(s["note"]),
                body="",
                tick=tick,
                at_hours=at,
                official=True,
                persona=_clean(persona, 64) or None,
            )
        )
    return out


def parse_events(items: Sequence[NewsItem], names: Mapping[str, str]) -> list[MarketEvent]:
    """Price moves stated in so many words ("N % over book for <set>"), for a set the catalog names; anything
    vaguer is not an event. An official "the fever breaks" closes the open official move of that persona."""
    events: list[MarketEvent] = []
    for item in sorted(items, key=lambda i: (i.at_hours is None, i.at_hours or 0.0)):
        text = f"{item.headline} {item.body}".strip()
        if item.official and FEVER_BREAKS.search(text):
            events = [_closed(ev, item) for ev in events]
            continue
        for m in (m for part in (item.headline, item.body) for m in PRICE_MOVE.finditer(part)):
            code = names.get(_fold(m.group("target")))
            if code is None:
                continue
            pct = float(m.group("pct").replace(",", "."))
            sign = 1.0 if m.group("dir").lower() in ("over", "above") else -1.0
            events.append(
                MarketEvent(
                    "price_move", code, sign * pct, item.at_hours, None, m.group("until"), item.persona,
                    item.official, item.news_id, text,
                )
            )  # fmt: skip
    return events


def _closed(ev: MarketEvent, end: NewsItem) -> MarketEvent:
    if ev.official and ev.end_hours is None and ev.persona == end.persona:
        return MarketEvent(**{**asdict(ev), "end_hours": end.at_hours})
    return ev


def active_signals(rules: Guardrails, events: Iterable[MarketEvent], t_hours: float) -> list[MarketEvent]:
    """The moves in force at `t_hours` that strategy or maker may act on: none while `news_signals_enabled` is
    false (the default), and only official ones even then (a rumour never moves a price of ours)."""
    if not rules.news_signals_enabled:
        return []
    return [
        ev
        for ev in events
        if ev.official
        and (ev.start_hours is None or ev.start_hours <= t_hours)
        and (ev.end_hours is None or t_hours < ev.end_hours)
    ]


def load_market_events(path: Path) -> list[MarketEvent]:
    """What the sentinel last wrote; nothing when the file is missing or unreadable."""
    try:
        rows = json.loads(path.read_text()).get("events") or []
        return [MarketEvent(**r) for r in rows if isinstance(r, dict)]
    except (OSError, ValueError, TypeError, AttributeError):
        return []


def learning_of(item: NewsItem, tick: int) -> Learning:
    subject = SAFE_SUBJECT.sub("_", item.source)[:64] or "radio"
    text = f"{item.headline}: {item.body}" if item.body else item.headline
    return Learning(
        subject_kind="organiser",
        subject=subject,
        kind="news",
        tick=max(0, item.tick or tick),
        confidence=SCHEDULE_CONFIDENCE if item.official else NEWS_CONFIDENCE,
        text=text or "-",
        detail={
            "news_id": item.news_id,
            "source": item.source,
            "headline": item.headline,
            "body": item.body,
            "at_hours": item.at_hours,
            "official": item.official,
            "persona": item.persona,
        },
    )


class NewsSentinel:
    """Run once per tick after the sends (`on_tick`): never raises, never blocks a send."""

    def __init__(
        self,
        public: Any,
        record: Callable[[list[Learning]], object],
        log: Callable[[str], None],
        out_dir: Path,
        every: int = READ_EVERY_TICKS,
    ) -> None:
        self.public, self.record, self.log, self.every = public, record, log, every
        self.path = out_dir / EVENTS_FILE
        self.seen: dict[str, NewsItem] = {}
        self.events: list[MarketEvent] = []
        self._last_read: int | None = None
        self._failed: set[str] = set()  # failures already logged (each said once)

    def on_tick(self, tick: int, events: Iterable[Mapping[str, Any]], catalog: Mapping[str, Any]) -> list[NewsItem]:
        try:
            return self._run(tick, events, catalog)
        except Exception as e:  # noqa: BLE001 — logging only: the sentinel never breaks a tick
            self._once(f"tick {tick} news: skipped ({type(e).__name__})")
            return []

    def _run(self, tick: int, events: Iterable[Mapping[str, Any]], catalog: Mapping[str, Any]) -> list[NewsItem]:
        items = items_from_feed(events)
        if self._last_read is None or tick - self._last_read >= self.every:
            self._last_read = tick
            items += self._read(tick)
        fresh = [i for i in items if i.news_id not in self.seen]
        if not fresh:
            return []
        self.record([learning_of(i, tick) for i in fresh])  # a store that raises: retried next tick
        for item in fresh:
            self.seen[item.news_id] = item
            kind = "official" if item.official else f"{item.source}, unverified"
            self.log(f"tick {tick} news ({kind}): {item.headline}" + (f" · {item.body}" if item.body else ""))
        self.events = parse_events(list(self.seen.values()), set_names(catalog))
        self._write()
        return fresh

    def _read(self, tick: int) -> list[NewsItem]:
        items: list[NewsItem] = []
        for what, read in (
            ("news", self._news),
            ("schedule", lambda: items_from_schedule(self.public.schedule(), tick)),
        ):
            try:
                items += read()
            except Exception as e:  # noqa: BLE001 — a refused or failed read: the feed still brings news.posted
                self._once(f"tick {tick} news: /api/{what} read failed ({type(e).__name__})")
                break  # the game is slow or refusing: the next read waits for the next window
        return items

    def _news(self) -> list[NewsItem]:
        return items_from_api(self.public.call("GET", "/api/news"))

    def _write(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"events": [asdict(e) for e in self.events]}, ensure_ascii=False, indent=1))
        tmp.replace(self.path)

    def _once(self, line: str) -> None:
        key = line.split(": ", 1)[-1]
        if key not in self._failed:
            self._failed.add(key)
            self.log(line)
