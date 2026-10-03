"""B13 evidence (virtual time, no network): how late the first Saturday tick is handled.

Usage: git show origin/main:src/bazaar_agent/ticks.py > /tmp/ticks_main.py
       uv run python docs/night/b13_wake_sim.py /tmp/ticks_main.py src/bazaar_agent/ticks.py

A loop starts at a random moment ~6 h before the 09:00 opening and runs the real `run_per_tick`; the fake
server opens `lag` s after `next_opens`; our wall clock is off by `skew` s. 1,000 starts per row, seed 7.
"""

import importlib.util
import random
import statistics
import sys
from datetime import UTC, datetime
from unittest import mock


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m


OPEN = datetime(2026, 10, 3, 7, 0, tzinfo=UTC).timestamp()


def run(mod, start, lag, skew):
    t = [start]
    reads = [0]
    handled = []

    def read():
        reads[0] += 1
        now = t[0]
        if now < OPEN + lag:
            return {
                "tick": 159,
                "doors": "closed",
                "paused": True,
                "next_tick_in": 0.0,
                "next_opens": datetime.fromtimestamp(OPEN, UTC).isoformat(),
            }
        since = now - (OPEN + lag)
        return {"tick": 160 + int(since // 30), "tick_seconds": 30.0, "next_tick_in": 30 - since % 30}

    def on_tick(c):
        handled.append((c.tick, t[0]))

    def sleep(s):
        t[0] += s

    with (
        mock.patch.object(mod.time, "time", lambda: t[0] + skew),
        mock.patch.object(mod.time, "monotonic", lambda: t[0]),
    ):
        mod.run_per_tick(read, on_tick, max_ticks=1, sleep=sleep, on_error=lambda st, e: (_ for _ in ()).throw(e))
    return handled[0][1] - (OPEN + lag), handled[0][0] - 160, reads[0]


rng = random.Random(7)
for label, path in (("main", sys.argv[1]), ("B13", sys.argv[2])):
    mod = load(path, label)
    for lag, skew in ((0, 0), (0, 3), (0, -3), (90, 0), (61.3, 0), (92, 0), (93.7, 0)):
        rows = [run(mod, OPEN - 6 * 3600 - rng.uniform(0, 300), lag, skew) for _ in range(1000)]
        late = [r[0] for r in rows]
        ticks = [r[1] for r in rows]
        reads = [r[2] for r in rows]
        print(
            f"{label:4} lag={lag:3}s skew={skew:+}s  late p50={statistics.median(late):6.1f}s max={max(late):6.1f}s "
            f"ticks missed mean={statistics.mean(ticks):.2f} max={max(ticks)}  "
            f"clock reads over 6 h mean={statistics.mean(reads):.0f}"
        )
