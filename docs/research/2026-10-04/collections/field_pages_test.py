"""Field-wide check: does a team's negotiating score jump when its `pages` count rises?

Read-only, leaderboard_snapshots (public data). For each team and consecutive refresh pair, Δnegotiating
minus the median Δnegotiating of all teams in that pair (drift). Compares intervals where pages rose vs not.
Usage (DATABASE_URL in env): uv run python field_pages_test.py
"""
import os
import statistics as st
from collections import defaultdict

import psycopg

with psycopg.connect(os.environ["DATABASE_URL"], options="-c default_transaction_read_only=on") as conn:
    rows = conn.execute(
        "select tick, team, negotiating::float, pages, deals from leaderboard_snapshots where world='real' order by tick, team"
    ).fetchall()

by_tick = defaultdict(dict)
for tick, team, neg, pages, deals in rows:
    by_tick[tick][team] = (neg, pages, deals)
ticks = sorted(by_tick)
rose, flat, rose_nodeal = [], [], []
events = []
for a, b in zip(ticks, ticks[1:]):
    common = by_tick[a].keys() & by_tick[b].keys()
    deltas = {t: by_tick[b][t][0] - by_tick[a][t][0] for t in common}
    med = st.median(deltas.values())
    for t in common:
        x = deltas[t] - med
        dp = by_tick[b][t][1] - by_tick[a][t][1]
        dd = by_tick[b][t][2] - by_tick[a][t][2]
        if dp > 0:
            rose.append(x)
            events.append((b, t, dp, dd, round(x, 2)))
            if dd == 0:
                rose_nodeal.append(x)
        elif dp == 0:
            flat.append(x)


def summ(xs):
    if not xs:
        return "n=0"
    xs = sorted(xs)
    return f"n={len(xs)} mean={st.mean(xs):+.3f} median={st.median(xs):+.3f} p10={xs[len(xs)//10]:+.3f} p90={xs[9*len(xs)//10]:+.3f}"


print("refreshes", len(ticks), "ticks", ticks[0], "-", ticks[-1])
print("pages rose      :", summ(rose))
print("  ...deals flat :", summ(rose_nodeal))
print("pages unchanged :", summ(flat))
print("events (tick, team, +pages, +deals, excess dNeg):")
for e in events:
    print(" ", e)


# --- split each page-rise event by the source of the cards the team received in that interval (tape) ---
with psycopg.connect(os.environ["DATABASE_URL"], options="-c default_transaction_read_only=on") as conn:
    tape = conn.execute(
        "select t.tick, t.buyer, t.seller, t.persona, t.card_id, t.price, c.rarity from tape t "
        "left join cards c on c.id = t.card_id where t.card_id is not null"
    ).fetchall()
prev_tick = {b: a for a, b in zip(ticks, ticks[1:])}
groups = defaultdict(list)
print("\nper event: cards bought in (prev refresh, refresh], source")
for b, t, dp, dd, x in events:
    a = prev_tick[b]
    got = [(tk, s, pe, cid, pr) for tk, by, s, pe, cid, pr, r in tape if by == t and a < tk <= b]
    sold = [(tk, by, pe, cid, pr) for tk, by, s, pe, cid, pr, r in tape if s == t and a < tk <= b]
    kinds = {"team" if pe is None else "dealer" for _, _, pe, _, _ in got}
    src = "team" if "team" in kinds else ("dealer" if kinds else "none-seen")
    if any(pe is None for *_, pe, _, _ in [(0, 0, p, 0, 0) for _, _, p, _, _ in sold]):
        src += "+team-sale"
    groups[src].append(x)
    print(f"  {b} {t} excess {x:+.2f} src={src} bought={[(c, p, pe or s) for _, s, pe, c, p in got]} sold={[(c, p, pe or by) for _, by, pe, c, p in sold]}")
for k, xs in sorted(groups.items()):
    print(k, summ(xs))


# --- control (review item 7): intervals WITHOUT a page rise, by what the team traded; duel windows apart ---
DUELS = [(459, 651), (1239, 1431)]  # Duels I and II, ticks (schedule.fired .. duels.finished)


def in_duels(a, b):
    return any(a < hi and b > lo for lo, hi in DUELS)


ctrl = defaultdict(list)
for a, b in zip(ticks, ticks[1:]):
    common = by_tick[a].keys() & by_tick[b].keys()
    deltas = {t: by_tick[b][t][0] - by_tick[a][t][0] for t in common}
    med = st.median(deltas.values())
    for t in common:
        if by_tick[b][t][1] != by_tick[a][t][1]:
            continue
        rows = [(by, s, pe) for tk, by, s, pe, cid, pr, r in tape if a < tk <= b and t in (by, s)]
        team_buy = any(by == t and pe is None for by, s, pe in rows)
        team_sale = any(s == t and pe is None for by, s, pe in rows)
        dealer_buy = any(by == t and pe is not None for by, s, pe in rows)
        if team_buy and not team_sale:
            key = "team buy, no team sale"
        elif dealer_buy and not team_buy and not team_sale:
            key = "dealer buy only"
        else:
            continue
        ctrl[(key, "in duels" if in_duels(a, b) else "outside duels")].append(deltas[t] - med)
print("\ncontrol: no page rise")
for k, xs in sorted(ctrl.items()):
    print(" ", k, summ(xs))
outside = [x for (b, t, dp, dd, x) in events if not in_duels(prev_tick[b], b)]
print("page rises outside duel windows:", summ(outside))
