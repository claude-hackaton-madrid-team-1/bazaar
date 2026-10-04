"""Saturday's public market and our own quoting, from the shared ledger (read-only).

Usage: uv run python docs/market-making/reports/mm-probe/market_stats.py
Saturday = round 2 = feed ticks >= 160 (round.started at tick 160) up to the close at 1445.
Only public facts are printed in aggregate (listing prices are public on the feed); no private value of ours.
"""

from collections import Counter, defaultdict
from statistics import median

import psycopg
from q import dsn  # type: ignore[import-not-found]

SAT = 160


def rows(conn: psycopg.Connection, sql: str) -> list[tuple]:
    return conn.execute(sql).fetchall()


def side_of(o: dict) -> tuple[str, str, int] | None:
    give, want = o["give"], o["want"]
    if len(give.get("assets") or []) == 1 and (want.get("cash") or 0) > 0 and not want.get("types"):
        return "ask", give["assets"][0]["ref"], want["cash"]
    if (give.get("cash") or 0) > 0 and not give.get("assets") and len(want.get("types") or []) == 1:
        return "bid", want["types"][0].removeprefix("card:"), give["cash"]
    return None


def main() -> None:
    with psycopg.connect(dsn(), options="-c default_transaction_read_only=on") as conn:
        rarity = dict(rows(conn, "select id, rarity from cards"))
        listed = rows(
            conn,
            f"select tick, actor, payload->'offer' from feed_events where type='offer.listed' and tick>={SAT}"
            " and actor like 't%'",
        )
        trades = rows(
            conn,
            f"select tick, payload from feed_events where type='settlement' and tick>={SAT}"
            " and payload->>'persona' is null",
        )
    px: dict[tuple[str, str, bool], list[int]] = defaultdict(list)
    ours: Counter[tuple[str, str, bool]] = Counter()
    for _, actor, o in listed:
        s = side_of(o)
        if not s:
            continue
        side, ref, p = s
        key = (rarity.get(ref, "?"), side, bool(o.get("to")))
        px[key].append(p)
        if actor == "t01":
            ours[(o["venue"] if o["venue"] in ("rastro",) else "team venue", side, bool(o.get("to")))] += 1
    tp: dict[str, list[int]] = defaultdict(list)
    venue_trades: Counter[str] = Counter()
    for _, t in trades:
        if t.get("price", 0) > 0 and len(t["items"]) == 1:
            tp[rarity.get(t["items"][0]["ref"], "?")].append(t["price"])
        venue_trades["rastro" if t.get("venue") == "rastro" else "team venues"] += 1
    print(
        "rarity     | public asks n / median | public bids n / median | addressed asks n / median"
        " | team trades n / median"
    )
    for r in ("common", "uncommon", "rare", "epic", "legendary"):
        a, b, aa, t = px[(r, "ask", False)], px[(r, "bid", False)], px[(r, "ask", True)], tp[r]
        f = lambda v: f"{len(v):5} / {median(v):5.0f}" if v else f"{0:5} /     -"  # noqa: E731
        print(f"{r:10} | {f(a):>22} | {f(b):>22} | {f(aa):>25} | {f(t):>22}")
    print("team-to-team settlements (cash > 0 or swaps):", dict(venue_trades))
    print("our listings (venue class, side, addressed):", dict(ours))


if __name__ == "__main__":
    main()
