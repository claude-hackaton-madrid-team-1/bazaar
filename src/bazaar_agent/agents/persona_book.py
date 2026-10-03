"""The taker's persona book: the `/api/dealers` personas it already reads every tick (no request of its own),
parsed by `persona_model.parse_personas`, and a snapshot of each stored in Postgres (`traders`: traits, menu,
unlock, the monitor's own columns) when they change, so DataGrip shows what every dealer published.

The store is off the tick: a write runs on a daemon thread, one at a time, at most once per `STORE_EVERY_TICKS`
ticks unless a persona we never saw appears (a new L4/L5 dealer is stored at once). A failed or slow write is
logged and never holds a send.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable, Sequence
from typing import Any

from bazaar_agent.monitor import TraderSnapshot, dealer_snapshots
from bazaar_agent.persona_model import Persona, parse_personas

STORE_EVERY_TICKS = 10
Writer = Callable[[list[TraderSnapshot], int], None]


def signature(dealers: Sequence[Any]) -> str:
    """What a persona published, for change detection (the fields the model reads, nothing else)."""
    keep = ("id", "status", "level", "traits", "menu", "unlock", "open_to_all")
    rows = sorted(({k: d.get(k) for k in keep} for d in dealers if isinstance(d, dict)), key=lambda r: str(r.get("id")))
    return json.dumps(rows, sort_keys=True, default=str)


class PersonaBook:
    def __init__(self, write: Writer | None, log: Callable[[str], None], every: int = STORE_EVERY_TICKS) -> None:
        self.write, self.log, self.every = write, log, every
        self.personas: dict[str, Persona] = {}
        self._signature: str | None = None
        self._stored: str | None = None  # the signature last stored
        self._stored_tick: int | None = None
        self._attempted: str | None = None  # the signature last handed to the writer (stored or not)
        self._worker: threading.Thread | None = None

    def observe(self, dealers: Sequence[Any], tick: int) -> dict[str, Persona]:
        """This tick's personas; store them when they changed (new persona: at once; else every `every` ticks)."""
        sig = signature(dealers)
        if sig != self._signature:
            fresh = parse_personas(dealers)
            new = sorted(set(fresh) - set(self.personas))
            if new and self.personas:
                self.log(f"tick {tick} personas: new dealer(s) {', '.join(new)}: params from their traits")
            self.personas, self._signature = fresh, sig
        if self.write is not None and sig != self._stored and self._due(tick, dealers):
            self._store(dealers, sig, tick)
        return self.personas

    def _due(self, tick: int, dealers: Sequence[Any]) -> bool:
        if self._stored_tick is None or tick - self._stored_tick >= self.every:
            return True
        stored_ids = {str(d.get("id")) for d in json.loads(self._attempted or "[]")}  # our own json.dumps output
        return any(isinstance(d, dict) and str(d.get("id")) not in stored_ids for d in dealers)

    def _store(self, dealers: Sequence[Any], sig: str, tick: int) -> None:
        if self._worker is not None and self._worker.is_alive():
            return  # the previous write is still running: the next due tick tries again
        snaps = list(dealer_snapshots({"personas": list(dealers)}).values())
        write = self.write
        assert write is not None

        def run() -> None:
            try:
                write(snaps, tick)
                self._stored = sig  # only a stored payload counts: a failed write is retried when it is due
            except Exception as e:  # noqa: BLE001 — storage is for reading later; never a reason to stop trading
                self.log(f"tick {tick} personas: snapshot not stored ({type(e).__name__})")

        self._stored_tick, self._attempted = tick, sig
        self._worker = threading.Thread(target=run, name="persona-store", daemon=True)
        self._worker.start()
