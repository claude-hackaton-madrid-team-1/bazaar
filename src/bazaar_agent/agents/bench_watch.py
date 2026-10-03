"""Watch the Market Test without a venue of our own (B20): read-only, then calibrate the bench model from it.

Every team without a venue has a free starter stall, and `/api/me` carries its broker key
(`starter_broker_key`, the kit's `Bazaar.me`). A broker cannot act on the stall (it is `auto`: the engine
crosses every pair first), but the key still reads the stall's book (`GET /api/broker/book`), and during a
Market Test that book holds the synthetic `bench_offers` every venue receives. One read a tick, logged to a
JSONL file, measures what W1a's bench model (#77) only assumes: when traders arrive, how long they stay,
who never moves their quote and how fast the others relax. Nothing is ever sent: no match, no announce.

What the stall's book shows is the book after the engine's crossing, so a trader that disappears was
either crossed by the stall or left; `calibrate` tells the two apart by replaying the stall's rule (lowest
ask against highest bid while they cross) on the previous read.

The key is a secret like the team key: it is registered with the telemetry scrubber, never printed or
logged, and only sent to the host `venue.check_broker_key_for_url` allows.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import SecretStr

from bazaar_agent import telemetry as tm
from bazaar_agent.ticks import Clock

WATCH_FILE = "bench_book.jsonl"
KNOWN_FIELDS = {"id", "run", "give", "want"}


def starter_key(me: Mapping[str, Any]) -> SecretStr | None:
    """The free stall's broker key from `/api/me`, registered with the scrubber; None while there is no stall."""
    raw = me.get("starter_broker_key")
    if not isinstance(raw, str) or not raw.strip():
        return None
    tm.add_secret(raw)
    return SecretStr(raw.strip())


def _cash(side: object) -> int:
    value = side.get("cash") if isinstance(side, dict) else None
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


def snapshot(book: Mapping[str, Any], clock: Clock) -> dict[str, Any] | None:
    """One read of the bench part of a broker book, or None outside a Market Test. Any field of a bench offer
    beyond id, run, give and want is kept under `extra` (an expiry or an age would change what a broker can do)."""
    offers = []
    for o in book.get("bench_offers") or []:
        if not isinstance(o, dict) or not isinstance(o.get("id"), str):
            continue
        ask, bid = _cash(o.get("want")), _cash(o.get("give"))
        if bool(ask) == bool(bid):
            continue
        row: dict[str, Any] = {
            "id": o["id"],
            "run": o.get("run"),
            "side": "sell" if ask else "buy",
            "quote": ask or bid,
        }
        extra = {k: v for k, v in o.items() if k not in KNOWN_FIELDS}
        if extra:
            row["extra"] = extra
        offers.append(row)
    if not offers:
        return None
    return {"tick": clock.tick, "t_hours": clock.t_hours, "book_tick": book.get("tick"), "offers": offers}


class BenchWatch:
    """`on_tick(clock)`: one book read a tick, appended to `path` when a Market Test is on. Reads only."""

    def __init__(self, broker: Any, path: Path, log: Callable[[str], None]) -> None:
        self.broker, self.path, self.log = broker, path, log
        self.reads = self.rows = 0
        self._live = False  # the last read had bench offers: an empty one is logged once, to close the session

    def on_tick(self, clock: Clock) -> None:
        book = self.broker.book()
        self.reads += 1
        row = snapshot(book if isinstance(book, dict) else {}, clock)
        if row is None and self._live:  # the session's last traders are gone: one empty read records it
            row = {"tick": clock.tick, "t_hours": clock.t_hours, "book_tick": book.get("tick"), "offers": []}
            self._live = False
            self._write(row)
            return
        if row is None:
            return
        self._live = True
        self._write(row)
        runs = sorted({str(o["run"]) for o in row["offers"]})
        self.log(f"tick {clock.tick} bench watch: {len(row['offers'])} offers (run {', '.join(runs)}) -> {self.path}")

    def _write(self, row: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, sort_keys=True) + "\n")
        self.rows += 1


# ---------------------------------------------------------------- what the reads say about the bench


@dataclass
class _Track:
    side: str
    first: int
    last: int
    quotes: list[int] = field(default_factory=list)
    end: str = "open"  # open (still there at the last read), crossed (by the stall) or left


def _crossed_at(prev: list[dict[str, Any]], now_ids: set[str], tolerance: float) -> set[str]:
    """Traders gone since the previous read that were most likely crossed rather than left: the vanished sells
    and buys paired lowest ask with highest bid while the bid was within `tolerance` of the ask. On the free
    stall the engine crosses at the start of a tick, before any read, so a crossed pair is never seen crossing:
    its last quotes were one relax step short of each other."""
    gone = [o for o in prev if o["id"] not in now_ids]
    asks = sorted((o for o in gone if o["side"] == "sell"), key=lambda o: o["quote"])
    bids = sorted((o for o in gone if o["side"] == "buy"), key=lambda o: -o["quote"])
    out: set[str] = set()
    for s, b in zip(asks, bids, strict=False):
        if s["quote"] > b["quote"] * (1 + tolerance):
            break
        out |= {s["id"], b["id"]}
    return out


def calibrate(rows: Iterable[Mapping[str, Any]], *, tolerance: float = 0.15) -> dict[str, Any]:
    """Per run: traders seen, arrivals (ticks after the run's first read), stays of the traders who left,
    the share whose quote never moved (seen twice or more), the mean relax step, and any extra offer fields.

    Read off the free stall, it is biased: a trader the engine crosses on arrival is never seen, so arrivals
    and the trader count are undercounted and the firm share is the share among traders not crossed at once.
    Read off our own board venue (`broker watch --ours`), nothing is crossed before the read."""
    ordered = sorted(rows, key=lambda r: r["tick"])
    by_run: dict[str, list[Mapping[str, Any]]] = {}
    last: dict[str, int] = {}  # run -> index of the last read that showed it
    for i, r in enumerate(ordered):
        runs = {str(o.get("run")) for o in r.get("offers") or []}
        for run in runs:
            by_run.setdefault(run, []).append(
                {"tick": r["tick"], "offers": [o for o in r["offers"] if str(o.get("run")) == run]}
            )
            last[run] = i
    for run, i in last.items():  # the read after a run's last one: its remaining traders are gone by then
        if i + 1 < len(ordered):
            by_run[run].append({"tick": ordered[i + 1]["tick"], "offers": []})
    out: dict[str, Any] = {}
    for run, reads in sorted(by_run.items()):
        reads = sorted(reads, key=lambda r: r["tick"])
        start = reads[0]["tick"]
        tracks: dict[str, _Track] = {}
        extras: set[str] = set()
        for i, r in enumerate(reads):
            ids = {o["id"] for o in r["offers"]}
            if i:
                crossed = _crossed_at(reads[i - 1]["offers"], ids, tolerance)
                for o in reads[i - 1]["offers"]:
                    if o["id"] not in ids and tracks[o["id"]].end == "open":
                        tracks[o["id"]].end = "crossed" if o["id"] in crossed else "left"
            for o in r["offers"]:
                t = tracks.setdefault(o["id"], _Track(o["side"], r["tick"] - start, r["tick"] - start))
                t.last = r["tick"] - start
                t.quotes.append(int(o["quote"]))
                extras |= set((o.get("extra") or {}).keys())
        twice = [t for t in tracks.values() if len(t.quotes) >= 2]
        steps = [abs(t.quotes[-1] - t.quotes[0]) / (len(t.quotes) - 1) for t in twice if t.quotes[-1] != t.quotes[0]]
        left = [t.last - t.first + 1 for t in tracks.values() if t.end == "left"]
        out[run] = {
            "reads": len(reads),
            "traders": len(tracks),
            "sells": sum(t.side == "sell" for t in tracks.values()),
            "arrivals": sorted(t.first for t in tracks.values()),
            "stays_of_leavers": sorted(left),
            "crossed": sum(t.end == "crossed" for t in tracks.values()),
            "firm_share": round(sum(len(set(t.quotes)) == 1 for t in twice) / len(twice), 3) if twice else None,
            "mean_relax_step": round(sum(steps) / len(steps), 2) if steps else None,
            "extra_fields": sorted(extras),
        }
    return out


def read_rows(path: Path) -> list[dict[str, Any]]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict) and isinstance(row.get("tick"), int):
            rows.append(row)
    return rows
