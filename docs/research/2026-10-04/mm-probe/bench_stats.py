"""Saturday's Market Test sessions: our broker's pairs and the score after each session (read-only).

Usage: (cd docs/research/2026-10-04/mm-probe && uv run python bench_stats.py)
Pairs: `decisions` agent='broker' kind='broker_match' (every one status done, none refused).
Score: `me_snapshots` t01 bench_efficiency / bench_points, first snapshot after each session.
Session efficiency is shown under both readings: A = the /me number is the session's own; B = it is the round
average, so session k = k * avg_k - (k - 1) * avg_(k-1) (BENCH_BEAT_STALL §1 fitted B on t03/t13).
"""

from statistics import median

import psycopg

from q import dsn  # type: ignore[import-not-found]

SESSIONS = [("h3", 201), ("h5", 441), ("h7", 681), ("h9", 921), ("h11", 1161), ("h13", 1401)]


def main() -> None:
    with psycopg.connect(dsn(), options="-c default_transaction_read_only=on") as conn:
        pairs = conn.execute(
            "select tick, candidates from decisions where kind='broker_match' and status='done' order by tick"
        ).fetchall()
        eff = conn.execute(
            "select min(tick), (score->>'bench_efficiency')::float, (score->>'bench_points')::float from me_snapshots"
            " where team='t01' and score ? 'bench_efficiency' and score->>'bench_efficiency' is not null"
            " group by 2,3 order by 1"
        ).fetchall()
    print("session | ticks     | pairs | quote spread (bid-ask) median / sum | /me eff after | bench_points | eff B")
    prev = None
    for k, (name, start) in enumerate(SESSIONS, 1):
        mine = [c for t, c in pairs if start <= t <= start + 16]
        spreads = [c["bid"] - c["ask"] for c in mine]
        after = next(((e, b) for t, e, b in eff if t >= start + 16), (None, None))
        e, b = after
        sess_b = e if prev is None or e is None else k * e - (k - 1) * prev
        prev = e
        med = median(spreads) if spreads else 0
        print(
            f"{name:7} | {start}-{start + 16:<4} | {len(mine):5} | {med:6.1f} / {sum(spreads):4}"
            f"{'':22}| {e if e is not None else '-':>13} | {b if b is not None else '-':>12} | "
            f"{sess_b:.3f}"
            if sess_b is not None
            else ""
        )
    allspreads = [c["bid"] - c["ask"] for _, c in pairs]
    print(
        f"all: {len(pairs)} pairs, median quote spread {median(allspreads)}, "
        f"<= 3 P: {sum(s <= 3 for s in allspreads)}, prices {min(c['price'] for _, c in pairs)}-"
        f"{max(c['price'] for _, c in pairs)}"
    )


if __name__ == "__main__":
    main()
