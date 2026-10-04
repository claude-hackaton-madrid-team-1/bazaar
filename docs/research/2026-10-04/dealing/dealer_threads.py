"""Per-thread and per-dealer table of Team 1's Saturday dealer threads (read-only).

Sources (shared Postgres, SELECT only, inside a READ ONLY transaction):
  feed_events  thread.opened / thread.message / thread.closed / settlement  (t01, tick >= 159)
  decisions    taker dealer_* rows (our max per thread) and dealer-sell rows (our floor per dealer+card)

Prints two TSV blocks: one row per thread, then one row per dealer.  By default our limits/floors are used
only for the inside-limit flag; the limit and the surplus over it (from which a limit can be recovered next to
a public price) print only with --with-limits, for a private file outside the repo.
Note (review item 2): the sell 'limit' is the hand CLI's --floor (or the maker's), NOT our value: SAL-07 (1362)
and RET-06 (1272) show a positive surplus over it while both sold below our value.  MAL-09 (1823) has no limit
(it was bought under a human approval).

    uv run python docs/research/2026-10-04/dealing/dealer_threads.py [--with-limits]
"""

from __future__ import annotations

import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).parent))
from q import url  # noqa: E402

SAT = 159


@dataclass
class Thread:
    tid: int
    dealer: str
    side: str  # buy (we buy from the dealer) / sell (we sell to the dealer)
    what: str
    opened: int
    msgs: list[tuple[int, str, int | None, bool]] = field(default_factory=list)  # tick, sender, cash, final
    closed: int | None = None
    closed_reason: str | None = None
    deal_price: int | None = None
    deal_tick: int | None = None
    limit: int | None = None  # taker: max we pay; dealer-sell: floor we accept
    last_kind: str | None = None
    walk_reason: str | None = None

    @property
    def theirs(self) -> list[int]:
        return [c for _, s, c, _ in self.msgs if s == self.dealer and c is not None]

    @property
    def ours(self) -> list[int]:
        return [c for _, s, c, _ in self.msgs if s == "t01" and c is not None]

    @property
    def final(self) -> int | None:
        fs = [c for _, s, c, f in self.msgs if s == self.dealer and f]
        return fs[-1] if fs else None

    @property
    def best_theirs(self) -> int | None:
        t = self.theirs
        if not t:
            return None
        return min(t) if self.side == "buy" else max(t)

    def inside_limit_walk(self) -> bool:
        if self.deal_price is not None or self.limit is None or self.best_theirs is None:
            return False
        b = self.best_theirs
        return b <= self.limit if self.side == "buy" else b >= self.limit

    def surplus_pct(self) -> float | None:
        if self.deal_price is None or not self.limit:
            return None
        if self.side == "buy":
            return 100.0 * (self.limit - self.deal_price) / self.limit
        return 100.0 * (self.deal_price - self.limit) / self.limit

    def off_open_pct(self) -> float | None:
        """Deal price vs the dealer's opening price: discount (buy) or premium (sell)."""
        t = self.theirs
        if self.deal_price is None or not t:
            return None
        o = t[0]
        if self.side == "buy":
            return 100.0 * (o - self.deal_price) / o
        return 100.0 * (self.deal_price - o) / o


def cash_of(offer: dict, sender: str) -> int | None:
    if not offer:
        return None
    give, want = offer.get("give") or {}, offer.get("want") or {}
    # a dealer's ask (sells us a card) wants cash; a dealer's bid (buys our card) gives cash.
    # our bid gives cash; our ask wants cash.
    g, w = give.get("cash") or 0, want.get("cash") or 0
    return g if g else (w if w else None)


def load(conn: psycopg.Connection) -> dict[int, Thread]:
    th: dict[int, Thread] = {}
    cur = conn.cursor()
    cur.execute(
        "select tick, payload from feed_events where type='thread.opened' and tick>=%s "
        "and payload->>'team'='t01' order by id",
        (SAT,),
    )
    for tick, p in cur.fetchall():
        topic = p.get("topic") or {}
        if "sell" in topic:
            side, what = "sell", ",".join(str(a) for a in topic["sell"].get("assets", []))
        else:
            b = topic.get("buy", {})
            side, what = "buy", b.get("card") or ("pack:" + str(b.get("pack")))
        th[p["thread"]] = Thread(p["thread"], p["with"], side, what, tick)
    cur.execute(
        "select tick, payload from feed_events where type='thread.message' and tick>=%s "
        "and payload->>'team'='t01' order by id",
        (SAT,),
    )
    for tick, p in cur.fetchall():
        t = th.get(p["thread"])
        if t is None:
            continue
        off = p.get("offer") or {}
        sender = p.get("sender")
        t.msgs.append((tick, sender, cash_of(off, sender), bool(off.get("final"))))
        if t.side == "sell" and t.what.replace(",", "").isdigit() and off:
            # resolve the asset id to a card ref for the sell side
            for leg in (off.get("give") or {}, off.get("want") or {}):
                for a in leg.get("assets") or []:
                    t.what = a.get("ref", t.what)
    cur.execute(
        "select tick, payload from feed_events where type='thread.closed' and tick>=%s " "and payload->>'team'='t01'",
        (SAT,),
    )
    for tick, p in cur.fetchall():
        t = th.get(p["thread"])
        if t:
            t.closed, t.closed_reason = tick, p.get("reason")
    # settlements with a persona: match to the latest thread with that dealer + card opened before
    cur.execute(
        "select tick, payload from feed_events where type='settlement' and tick>=%s "
        "and payload->'parties' ? 't01' and payload->>'persona' is not null order by tick",
        (SAT,),
    )
    for tick, p in cur.fetchall():
        refs = {i["ref"] for i in p["items"]}
        cands = [
            t
            for t in th.values()
            if t.dealer == p["persona"]
            and t.opened <= tick
            and t.deal_price is None
            and (t.what in refs)
            and t.msgs
            and t.msgs[-1][0] >= tick - 3
        ]
        if not cands:
            print(f"# unmatched settlement tick {tick} {p['persona']} {refs}", file=sys.stderr)
            continue
        t = max(cands, key=lambda x: x.opened)
        t.deal_price, t.deal_tick = int(p["price"]), tick
    # our limits: taker max per thread; dealer-sell floor per (dealer, ref) nearest decision
    cur.execute(
        "select thread_id, tick, kind, reason, candidates from decisions where agent='taker' "
        "and kind like 'dealer_%%' and thread_id is not null order by id"
    )
    for tid, _tick, kind, reason, c in cur.fetchall():
        t = th.get(tid)
        if not t:
            continue
        if c and c.get("max") is not None:
            t.limit = int(c["max"])
        t.last_kind = kind
        if kind in ("dealer_walk", "dealer_closed"):
            t.walk_reason = reason
    cur.execute("select tick, kind, reason, candidates from decisions where agent='dealer-sell' order by id")
    sell_rows = cur.fetchall()
    for t in th.values():
        if t.side != "sell":
            continue
        rows = [
            r
            for r in sell_rows
            if r[3] and r[3].get("dealer") == t.dealer and r[3].get("ref") == t.what
            # the floor in force while this thread ran: decisions up to its last message + 1
            and t.opened - 1 <= r[0] <= (t.msgs[-1][0] if t.msgs else t.opened + 1)
        ]
        if rows:
            fl = [r[3].get("floor") for r in rows if r[3].get("floor") is not None]
            if fl:
                t.limit = int(fl[-1])
            walks = [r for r in rows if r[1] == "dealer_walk"]
            if walks:
                t.walk_reason = walks[-1][2]
            t.last_kind = rows[-1][1]
    return th


def main() -> None:
    show = "--with-limits" in sys.argv
    with psycopg.connect(url()) as conn:
        conn.read_only = True
        th = load(conn)
    cols = [
        "thread",
        "dealer",
        "side",
        "card",
        "opened",
        "closed",
        "ticks",
        "our_msgs",
        "their_msgs",
        "their_first",
        "their_best",
        "final",
        "deal",
        "off_open_%",
        "inside_walk",
        "closed_reason",
        "why",
    ]
    if show:
        cols[14:14] = ["surplus_%", "limit"]
    print("\t".join(cols))
    agg: dict[str, dict] = defaultdict(lambda: defaultdict(float))
    for t in sorted(th.values(), key=lambda x: x.opened):
        end = t.deal_tick or t.closed or (t.msgs[-1][0] if t.msgs else t.opened)
        row = [
            t.tid,
            t.dealer,
            t.side,
            t.what,
            t.opened,
            t.closed or "",
            end - t.opened,
            len(t.ours),
            len(t.theirs),
            t.theirs[0] if t.theirs else "",
            t.best_theirs or "",
            t.final or "",
            t.deal_price or "",
            f"{t.off_open_pct():.0f}" if t.off_open_pct() is not None else "",
            "YES" if t.inside_limit_walk() else "",
            t.closed_reason or "",
            (t.walk_reason or "")[:70],
        ]
        if show:
            row[14:14] = [f"{t.surplus_pct():.0f}" if t.surplus_pct() is not None else "", t.limit or ""]
        print("\t".join(str(x) for x in row))
        a = agg[f"{t.dealer}/{t.side}"]
        a["threads"] += 1
        a["our_msgs"] += len(t.ours)
        a["their_msgs"] += len(t.theirs)
        a["silent"] += 0 if t.theirs else 1
        if t.deal_price is not None:
            a["deals"] += 1
            a["cash"] += -t.deal_price if t.side == "buy" else t.deal_price
            a["rounds_in_deals"] += len(t.ours)
            if t.off_open_pct() is not None:
                a["off_open_sum"] += t.off_open_pct()
                a["off_open_n"] += 1
            if t.surplus_pct() is not None:
                a["surplus_sum"] += t.surplus_pct()
                a["surplus_n"] += 1
        if t.inside_limit_walk():
            a["inside_walks"] += 1
        if t.final is not None:
            a["finals"] += 1
    print()
    print(
        "dealer/side\tthreads\tsilent(no dealer price)\tdeals\tdeal_rate_%\tour_msgs\ttheir_msgs\t"
        "our_msgs_per_deal\tfinals_seen\tcash_flow\tmean_off_open_%\tinside_limit_walks"
        + ("\tmean_surplus_over_limit_%" if show else "")
    )
    for k, a in sorted(agg.items()):
        d = a["deals"]
        print(
            "\t".join(
                str(x)
                for x in [
                    k,
                    int(a["threads"]),
                    int(a["silent"]),
                    int(d),
                    f"{100 * d / a['threads']:.0f}",
                    int(a["our_msgs"]),
                    int(a["their_msgs"]),
                    f"{a['rounds_in_deals'] / d:.1f}" if d else "",
                    int(a["finals"]),
                    int(a["cash"]),
                    f"{a['off_open_sum'] / a['off_open_n']:.0f}" if a["off_open_n"] else "",
                    int(a["inside_walks"]),
                ]
                + ([f"{a['surplus_sum'] / a['surplus_n']:.0f}" if a["surplus_n"] else ""] if show else [])
            )
        )


if __name__ == "__main__":
    main()
