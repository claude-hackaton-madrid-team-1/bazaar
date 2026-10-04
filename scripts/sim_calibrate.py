"""Calibrate the simulator's `sunday` scenario from the real game's data (read-only).

uv run python scripts/sim_calibrate.py [--out src/bazaar_sim/data/sunday.json]
    [--db-url-file PATH | env SIM_CALIBRATE_DB_URL] [--cal-dir DIR]

Reads the shared Postgres (feed_events, dealer_curves, duels, competitor_profiles) and the keyless API
copies in `--cal-dir` (schedule.json, dealers.json, catalog.json, news.json, levels.json; fetch them at
<= 1 req/s with curl). Every section is a pure function over plain rows, so the tests need no database.
The output is deterministic JSON: sorted keys, rounded floats, no secrets, no timestamps. The database URL
is never printed or written.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
DEFAULT_OUT = REPO / "src" / "bazaar_sim" / "data" / "sunday.json"
DEFAULT_CAL = Path(
    "/private/tmp/claude-501/-Users-ogarcia-projects-hackathons-bazaar/9fca0c62-c959-4fad-bd21-caaf55581526/scratchpad"
)
DEFAULT_DB_FILE = DEFAULT_CAL / "bazaar_team_ro.url"
OUR_TEAM = "t01"
SAT_FIRST_TICK = 160  # Saturday's ticks start here (Friday ran 60 s ticks)
OPEN_HOURS = 16.65  # the schedule's own `day_opens sun` entry: Sunday's 6 h at 15 s run h16.65-22.65 (1440 ticks)
TICK_SECONDS = 15
DEALERS = ("abuela", "chato", "pilar", "picaros", "banco")
RARITIES = ("common", "uncommon", "rare", "epic", "legendary")
# Sessions the real feed does not carry: bench.finished is on the team stream only (.ai/memory.md BE1).
BENCH_REFERENCE = {
    "source": ".ai/memory.md BE1 (team stream, our venues)",
    "sessions": [
        {"session": 1, "efficiency": 0.899, "auto_baseline": 0.899, "venue": "v08"},
        {"session": 2, "efficiency": 0.967, "auto_baseline": 0.967, "venue": "v19"},
        {"session": 3, "efficiency": 0.769, "auto_baseline": 0.769, "venue": "v19"},
    ],
}
# Known Saturday fevers (schedule text; the public feed has no event for them). Sunday's are unknown.
KNOWN_FEVERS = [
    {
        "at_hours": 9.15,
        "until_hours": 11.15,
        "dealer": "pilar",
        "kind": "set",
        "set": "SAL",
        "mult": 1.25,
        "note": "Salamanca fever: Pilar pays 25 % over book for Salamanca",
        "source": "/api/schedule (Saturday), .ai/memory.md",
    }
]


# ------------------------------------------------------------------ helpers


def r(x: float | None, nd: int = 3) -> float | None:
    return None if x is None else round(float(x), nd)


def quantiles(xs: list[float]) -> dict[str, Any]:
    """n, min, p25, p50, p75, max (None when empty)."""
    if not xs:
        return {"n": 0, "min": None, "p25": None, "p50": None, "p75": None, "max": None}
    s = sorted(xs)

    def at(p: float) -> float:
        i = (len(s) - 1) * p
        lo, hi = int(i), min(int(i) + 1, len(s) - 1)
        return s[lo] + (s[hi] - s[lo]) * (i - lo)

    return {"n": len(s), "min": r(s[0]), "p25": r(at(0.25)), "p50": r(at(0.5)), "p75": r(at(0.75)), "max": r(s[-1])}


def pct(xs: list[float], p: float) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    i = (len(s) - 1) * p
    lo, hi = int(i), min(int(i) + 1, len(s) - 1)
    return r(s[lo] + (s[hi] - s[lo]) * (i - lo))


def med(xs: list[float]) -> float | None:
    return r(statistics.median(xs)) if xs else None


def catalog_index(catalog: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for s in catalog["sets"]:
        for c in s["cards"]:
            out[c["id"]] = {"rarity": c["rarity"], "book": c["book"], "set": s["id"]}
    return out


def menu_list_prices(dealer: dict[str, Any]) -> dict[str, int]:
    """Menu list prices by key: a rarity (`uncommon`) or a pack id (`sobre_barrio`)."""
    out: dict[str, int] = {}
    for item in dealer.get("menu", {}).get("sells", []):
        out[item.get("pack") or item["rarity"]] = int(item["list_price"])
    return out


def payload_offer(p: dict[str, Any]) -> dict[str, Any]:
    o = p.get("offer")
    return o if isinstance(o, dict) else {}


def first_ref(offer: dict[str, Any]) -> dict[str, Any] | None:
    for side in ("give", "want"):
        for a in offer.get(side, {}).get("assets", []) or []:
            if a.get("ref"):
                return a
    return None


# ------------------------------------------------------------------ clock, schedule, news


def clock_section(limits: dict[str, int], latency: dict[str, Any]) -> dict[str, Any]:
    return {
        "source": "/api/schedule day_opens/day_closes (h16.65 to h22.65) + /api/clock days; latency: "
        + latency["source"],
        "tick_seconds": TICK_SECONDS,
        "game_tick_seconds": TICK_SECONDS,
        "open_hours": OPEN_HOURS,
        "sunday_ticks": 1440,
        "limits": dict(sorted(limits.items())),
        "rate": {"per_sec": 5, "burst": 20},
        "latency_ms": {"p50": latency["p50"], "p95": latency["p95"], "source": latency["source"]},
    }


def schedule_section(upcoming: list[dict[str, Any]], open_hours: float = OPEN_HOURS) -> dict[str, Any]:
    items = []
    for e in upcoming:
        ticks = round((float(e["at_hours"]) - open_hours) * 3600 / TICK_SECONDS)
        items.append({**e, "at_hours": r(e["at_hours"], 4), "ticks_from_open": ticks, "before_open": ticks < 0})
    return {
        "source": "/api/schedule upcoming (verbatim) + ticks_from_open",
        "open_hours": open_hours,
        "upcoming": items,
    }


def news_section(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """`rows`: news items with source, source_name, headline, body, at_hours (/api/news or news.posted)."""
    items = sorted(
        (
            {
                "source": n.get("source", ""),
                "source_name": n.get("source_name", ""),
                "headline": n.get("headline", ""),
                "body": n.get("body", ""),
                "at_hours": r(n["at_hours"], 4),
            }
            for n in rows
        ),
        key=lambda n: n["at_hours"],
    )
    gaps = [b["at_hours"] - a["at_hours"] for a, b in zip(items, items[1:], strict=False)]
    return {"source": "/api/news (= feed_events news.posted)", "items": items, "cadence_hours": med(gaps)}


# ------------------------------------------------------------------ dealers


def thread_infos(opened: list[dict[str, Any]], messages: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    """thread id -> {dealer, team, side (team buys|sells), ref}: side from the topic, ref from any message asset."""
    info: dict[int, dict[str, Any]] = {}
    for ev in opened:
        p = ev["payload"]
        if p.get("kind") != "persona":
            continue
        topic = p.get("topic") or {}
        info[int(p["thread"])] = {
            "dealer": p.get("with"),
            "team": p.get("team"),
            "side": "buy" if "buy" in topic else ("sell" if "sell" in topic else "other"),
            "topic": topic,
            "ref": None,
        }
    for ev in messages:
        p = ev["payload"]
        t = info.get(int(p.get("thread", -1)))
        if t is not None and t["ref"] is None:
            a = first_ref(payload_offer(p))
            if a is not None:
                t["ref"] = {"ref": a["ref"], "rarity": a.get("rarity"), "kind": a.get("kind")}
    return info


def _key_for(t: dict[str, Any], cards: dict[str, dict[str, Any]]) -> str | None:
    """`pack id` or a rarity for a thread's item."""
    ref = t.get("ref")
    if ref is None:
        topic = t.get("topic") or {}
        buy = topic.get("buy") or {}
        if isinstance(buy, dict) and buy.get("pack"):
            return str(buy["pack"])
        if isinstance(buy, dict) and buy.get("card") in cards:
            return str(cards[buy["card"]]["rarity"])
        return None
    if ref.get("kind") == "pack":
        return str(ref["ref"])
    if ref["ref"] in cards:
        return str(cards[ref["ref"]]["rarity"])
    return ref.get("rarity")


def _book_of(t: dict[str, Any], cards: dict[str, dict[str, Any]]) -> float | None:
    ref = t.get("ref")
    if ref and ref["ref"] in cards:
        return float(cards[ref["ref"]]["book"])
    buy = (t.get("topic") or {}).get("buy") or {}
    if isinstance(buy, dict) and buy.get("card") in cards:
        return float(cards[buy["card"]]["book"])
    return None


def thread_dynamics(messages: list[dict[str, Any]], infos: dict[int, dict[str, Any]]) -> dict[int, dict[str, Any]]:
    """Per persona thread, from thread.message events in id order: dealer prices, a final, what came after it."""
    by_thread: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for ev in sorted(messages, key=lambda e: e["id"]):
        by_thread[int(ev["payload"].get("thread", -1))].append(ev["payload"])
    out: dict[int, dict[str, Any]] = {}
    for tid, msgs in by_thread.items():
        if tid not in infos:
            continue
        dealer = infos[tid]["dealer"]
        dealer_prices: list[int] = []
        team_prices: list[int] = []
        final_at: int | None = None
        final_price: int | None = None
        after_final: list[int] = []
        spoke_after = 0
        for m in msgs:
            o = payload_offer(m)
            cash = (o.get("want", {}).get("cash") or 0) or (o.get("give", {}).get("cash") or 0)
            if m.get("sender") == dealer:
                if final_at is not None:
                    spoke_after += 1
                if not o:
                    continue
                if o.get("final") and final_at is None:
                    final_at, final_price = len(dealer_prices), int(cash)
                elif final_at is not None and cash and int(cash) != final_price:
                    after_final.append(int(cash))
                if cash:
                    dealer_prices.append(int(cash))
            elif o and cash:
                team_prices.append(int(cash))
        out[tid] = {
            "dealer_prices": dealer_prices,
            "team_prices": team_prices,
            "final_at": final_at,
            "final_price": final_price,
            "after_final": after_final,
            "spoke_after_final": spoke_after,
            "team_bids_before_final": (
                sum(1 for _ in team_prices[: max(final_at, 0) + 1]) if final_at is not None else None
            ),
        }
    return out


def dealer_section(
    curves: list[dict[str, Any]],
    infos: dict[int, dict[str, Any]],
    dyn: dict[int, dict[str, Any]],
    cards: dict[str, dict[str, Any]],
    api_dealers: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for d in DEALERS:
        api = api_dealers.get(d, {})
        lists = menu_list_prices(api)
        rows = [c for c in curves if c["dealer"] == d]
        outcome = Counter(c["outcome"] for c in rows)
        open_mult: dict[str, list[float]] = defaultdict(list)
        fills: dict[str, list[float]] = defaultdict(list)
        floor: dict[str, list[float]] = defaultdict(list)
        buy_open: list[float] = []
        buy_fill: list[float] = []
        for c in rows:
            t = infos.get(int(c["thread_id"]))
            if t is None:
                continue
            key = _key_for(t, cards)
            if t["side"] == "buy" and key in lists:
                if c["opening_ask"]:
                    open_mult[key].append(c["opening_ask"] / lists[key])
                if c["outcome"] == "deal" and c["fill_price"]:
                    ratio = c["fill_price"] / lists[key]
                    fills[key].append(ratio)
                    if (c["steps"] or 0) >= 2:
                        floor[key].append(ratio)
            elif t["side"] == "sell":
                book = _book_of(t, cards)
                if book:
                    if c["opening_ask"]:
                        buy_open.append(c["opening_ask"] / book)
                    if c["outcome"] == "deal" and c["fill_price"]:
                        buy_fill.append(c["fill_price"] / book)
        finals_at = [v["final_at"] for tid, v in dyn.items() if infos[tid]["dealer"] == d and v["final_at"] is not None]
        bids_before = [
            v["team_bids_before_final"]
            for tid, v in dyn.items()
            if infos[tid]["dealer"] == d and v["team_bids_before_final"] is not None
        ]
        with_final = [v for tid, v in dyn.items() if infos[tid]["dealer"] == d and v["final_at"] is not None]
        fake = sum(1 for v in with_final if v["after_final"])
        spoke = sum(1 for v in with_final if v["spoke_after_final"])
        out[d] = {
            "source": "dealer_curves + feed_events thread.opened/thread.message; /api/dealers (menu, traits)",
            "n_threads": len(rows),
            "outcomes": dict(sorted(outcome.items())),
            "walk_rate": r(1 - outcome.get("deal", 0) / len(rows)) if rows else None,
            "menu": lists,
            "buys_menu": [{"rarity": b["rarity"], "sets": b["sets"]} for b in api.get("menu", {}).get("buys", [])],
            "open_mult": {k: pct(v, 0.5) for k, v in sorted(open_mult.items())},
            "fill_ratio": {k: quantiles(v) for k, v in sorted(fills.items())},
            "floor_range": {k: {"lo": pct(v, 0.1), "hi": pct(v, 0.9), "n": len(v)} for k, v in sorted(floor.items())},
            "steps_to_final": {
                "median": med(finals_at),
                "p25": pct(finals_at, 0.25),
                "p75": pct(finals_at, 0.75),
                "n": len(finals_at),
            },
            "bids_before_final": med(bids_before),
            "buys": {
                "opening_bid_over_book": quantiles(buy_open),
                "fill_over_book": quantiles(buy_fill),
                "source": "dealer_curves sell threads (topic.sell), book from the card catalog",
            },
            "fake_final": {
                "threads_with_final": len(with_final),
                "threads_that_moved_after_final": fake,
                "rate": r(fake / len(with_final)) if with_final else None,
                "threads_with_dealer_text_after_final": spoke,
                "rate_text_after_final": r(spoke / len(with_final)) if with_final else None,
                "evidence": (
                    "rate: share of threads where the dealer priced differently after an offer with final=true "
                    "(0 in Friday+Saturday data); rate_text_after_final: it only spoke again (walk lines)"
                ),
            },
            "unlock": {
                "open_to_all_at_hours": _plus_hours(api.get("unlock", {}).get("open_to_all_at")),
                "early_deals_with": api.get("unlock", {}).get("early_deals_with"),
                "early_min_deals": api.get("unlock", {}).get("early_min_deals"),
                "early_min_level": api.get("unlock", {}).get("early_min_level"),
            },
            "level": api.get("level"),
            "kind": api.get("kind"),
            "traits": api.get("traits", {}),
        }
    return out


def _plus_hours(s: str | None) -> float | None:
    """`+2.63h` -> 2.63 (hours after activation, as /api/dealers writes it)."""
    if not s:
        return None
    return r(float(s.strip().lstrip("+").rstrip("h")), 3)


def fever_section(updates: list[dict[str, Any]], news: list[dict[str, Any]]) -> dict[str, Any]:
    items = [dict(f) for f in KNOWN_FEVERS]
    for n in news:
        if "pays more" in n.get("headline", "").lower():
            items.append(
                {
                    "at_hours": r(n["at_hours"], 4),
                    "until_hours": None,
                    "dealer": "abuela",
                    "kind": "rarity",
                    "rarity": "uncommon",
                    "mult": None,
                    "note": n["headline"],
                    "source": "/api/news",
                }
            )
    ups = [{"tick": u["tick"], "persona": u["payload"].get("persona"), "by": u["actor"]} for u in updates]
    return {
        "source": "KNOWN_FEVERS (schedule text) + /api/news + feed_events persona.updated",
        "items": items,
        "persona_updates": ups,
        "note": "persona.updated carries only {name, persona, version}: it proves a rule change, not its size",
    }


# ------------------------------------------------------------------ rivals


def rivals_section(
    listed: list[dict[str, Any]],
    cancelled: list[dict[str, Any]],
    settlements: list[dict[str, Any]],
    venue_opened: list[dict[str, Any]],
    fee_changed: list[dict[str, Any]],
    cards: dict[str, dict[str, Any]],
    profiles: list[dict[str, Any]],
) -> dict[str, Any]:
    sat = [e for e in listed if e["tick"] >= SAT_FIRST_TICK and e["actor"] != OUR_TEAM]
    ticks = max(1, max((e["tick"] for e in sat), default=SAT_FIRST_TICK) - SAT_FIRST_TICK + 1)
    per_team: Counter[str] = Counter(e["actor"] for e in sat)
    per_tick: Counter[int] = Counter(e["tick"] for e in sat)
    counts = [per_tick.get(t, 0) for t in range(SAT_FIRST_TICK, SAT_FIRST_TICK + ticks)]
    ask_over: dict[str, list[float]] = defaultdict(list)
    bid_over: dict[str, list[float]] = defaultdict(list)
    life: list[int] = []
    venue_listings: Counter[str] = Counter()
    for e in sat:
        o = e["payload"]["offer"]
        venue_listings[str(e["payload"].get("venue"))] += 1
        life.append(int(o["expires_tick"]) - int(o["created_tick"]))
        give, want = o["give"], o["want"]
        if give.get("assets") and want.get("cash"):
            a = give["assets"][0]
            book = cards.get(a["ref"], {}).get("book")
            if book:
                ask_over[str(a.get("rarity"))].append(want["cash"] / book)
        elif give.get("cash") and want.get("types"):
            ref = want["types"][0].partition(":")[2]
            if ref in cards:
                bid_over[cards[ref]["rarity"]].append(give["cash"] / cards[ref]["book"])
    sat_ids = {int(e["payload"]["offer"]["id"]) for e in sat}
    cancelled_n = sum(1 for c in cancelled if int(c["payload"].get("offer", -1)) in sat_ids)
    sat_set = [s for s in settlements if s["tick"] >= SAT_FIRST_TICK]
    team_trades = [s for s in sat_set if s["payload"].get("persona") is None and s["payload"].get("items")]
    on_rastro = [s for s in team_trades if s["payload"].get("venue") in (None, "rastro")]
    on_team_venue = [s for s in team_trades if s["payload"].get("venue") not in (None, "rastro")]
    sold: Counter[str] = Counter()
    bought: Counter[str] = Counter()
    pairs: Counter[tuple[str, str]] = Counter()
    directed: Counter[tuple[str, str]] = Counter()
    for s in team_trades:
        it = s["payload"]["items"][0]
        sold[it["frm"]] += 1
        bought[it["to"]] += 1
        directed[(it["frm"], it["to"])] += 1
        pairs[tuple(sorted((it["frm"], it["to"])))] += 1  # type: ignore[arg-type]
    teams = sorted({*per_team, *sold, *bought} - {OUR_TEAM})
    prof = {p["team"]: p for p in profiles}
    total_trades = max(1, len(team_trades))
    fees = [int(e["payload"].get("fee_bps", 0)) for e in venue_opened]
    latest_fee: dict[str, int] = {}
    for e in sorted(fee_changed, key=lambda x: x["id"]):
        latest_fee[str(e["payload"]["venue"])] = int(e["payload"].get("fee_bps", 0))
    return {
        "source": "feed_events offer.listed/offer.cancelled/settlement/venue.opened/venue.fee_changed (ticks >= 160, "
        "teams other than t01); competitor_profiles",
        "n_teams": len(teams),
        "listings_per_tick": {
            "mean": r(statistics.fmean(counts)),
            "p50": pct([float(c) for c in counts], 0.5),
            "p95": pct([float(c) for c in counts], 0.95),
            "ticks": ticks,
        },
        "listing_share_by_venue": {k: r(v / len(sat)) for k, v in sorted(venue_listings.items())} if sat else {},
        "ask_over_book": {k: quantiles(v) for k, v in sorted(ask_over.items())},
        "bid_over_book": {k: quantiles(v) for k, v in sorted(bid_over.items())},
        "listing_lifetime_ticks": quantiles([float(x) for x in life]),
        "cancel_rate": r(cancelled_n / len(sat)) if sat else None,
        "settlements_per_tick": {
            "rastro": r(len(on_rastro) / ticks),
            "team_venues": r(len(on_team_venue) / ticks),
        },
        "accept_rate": r(len(team_trades) / len(sat)) if sat else None,
        "venue_fee_bps": {
            "opened": quantiles([float(x) for x in fees]),
            "latest_changed": quantiles([float(x) for x in latest_fee.values()]),
            "n_venues_opened": len(fees),
        },
        "reciprocal_pairs": [
            {
                "a": a,
                "b": b,
                "trades": n,
                "a_to_b": directed.get((a, b), 0),
                "b_to_a": directed.get((b, a), 0),
            }
            for (a, b), n in pairs.most_common(10)
        ],
        "teams": {
            t: {
                "listings_per_tick": r(per_team.get(t, 0) / ticks),
                "settlement_share_as_seller": r(sold.get(t, 0) / total_trades),
                "settlement_share_as_buyer": r(bought.get(t, 0) / total_trades),
                "profile": _profile(prof.get(t)),
            }
            for t in teams
        },
    }


def _profile(p: dict[str, Any] | None) -> dict[str, Any]:
    if not p:
        return {}
    notes = p.get("notes") or {}
    return {
        "avg_pack_price": r(p["avg_pack_price"]) if p.get("avg_pack_price") is not None else None,
        "bids": notes.get("bids"),
        "dealer_threads": notes.get("dealer_threads"),
        "top_set": notes.get("top_set"),
        "level": p.get("level"),
    }


# ------------------------------------------------------------------ bench, duels, packs


def bench_section(started: list[dict[str, Any]], schedule: list[dict[str, Any]]) -> dict[str, Any]:
    sessions = [
        {
            "session": e["payload"].get("session"),
            "name": e["payload"].get("name"),
            "ticks": e["payload"].get("ticks"),
            "start_tick": e["payload"].get("start_tick"),
            "venues": len(e["payload"].get("venues", [])),
        }
        for e in sorted(started, key=lambda e: e["id"])
    ]
    plan = [
        {
            "at_hours": s["at_hours"],
            "traders": s.get("params", {}).get("traders"),
            "name": s.get("params", {}).get("name"),
        }
        for s in schedule
        if s.get("action") == "bench"
    ]
    return {
        "source": "feed_events bench.started; /api/schedule bench params; BENCH_REFERENCE from .ai/memory.md",
        "real_sessions": sessions,
        "sunday_plan": plan,
        "ticks_per_session": 16,
        "reference_efficiency": BENCH_REFERENCE,
        "note": "bench.finished is team-stream only: no efficiency or arrival data in the public feed",
    }


def duels_section(
    ours: list[dict[str, Any]], closed: list[dict[str, Any]], scheduled: list[dict[str, Any]]
) -> dict[str, Any]:
    sessions: dict[int, dict[str, Any]] = {}
    all_status: dict[int, Counter[str]] = defaultdict(Counter)
    for e in closed:
        all_status[int(e["payload"].get("session", 0))][str(e["payload"].get("status"))] += 1
    for sid in sorted(all_status):
        c = all_status[sid]
        n = sum(c.values())
        mine = [d for d in ours if d["session"] == sid]
        deals = [d for d in mine if d["status"] == "deal"]
        sessions[sid] = {
            "all_teams_closed": n,
            "all_teams_deal_rate": r(c.get("deal", 0) / n) if n else None,
            "ours_n": len(mine),
            "ours_deal_rate": r(len(deals) / len(mine)) if mine else None,
            "ours_mean_rounds_deal": med([float(d["rounds"] or 0) for d in deals]),
            "ours_mean_result": r(statistics.fmean([float(d["result"]) for d in deals])) if deals else None,
            "messages_per_duel": med([float(len((d.get("payload") or {}).get("messages", []))) for d in mine]),
        }
    by_role: dict[str, dict[str, Any]] = {}
    for role in ("seller", "buyer"):
        mine = [d for d in ours if d["role"] == role]
        deals = [d for d in mine if d["status"] == "deal"]
        by_role[role] = {
            "n": len(mine),
            "deal_rate": r(len(deals) / len(mine)) if mine else None,
            "rounds": med([float(d["rounds"] or 0) for d in deals]),
            "price_over_limit": quantiles(
                [d["price"] / d["your_limit"] for d in deals if d["price"] and d["your_limit"]]
            ),
        }
    plans = [
        {k: e["payload"].get(k) for k in ("session", "name", "decay", "rounds", "duel_ticks", "duels")}
        for e in sorted(scheduled, key=lambda e: e["id"])
    ]
    return {
        "source": "feed_events duel.closed/duels.scheduled (all teams); duels table (ours only)",
        "by_session": {str(k): v for k, v in sessions.items()},
        "by_role": by_role,
        "scheduled": plans,
        "note": "rival styles are not in the data: only the rival alias and its messages in duels.payload",
    }


def packs_section(opened: list[dict[str, Any]], gifts: list[dict[str, Any]]) -> dict[str, Any]:
    sat = [e for e in opened if e["tick"] >= SAT_FIRST_TICK]
    ticks = max(1, max((e["tick"] for e in sat), default=SAT_FIRST_TICK) - SAT_FIRST_TICK + 1)
    g = [e for e in gifts if e["tick"] >= SAT_FIRST_TICK]
    return {
        "source": "feed_events pack.opened, gift.given (ticks >= 160)",
        "packs_opened_per_tick": r(len(sat) / ticks, 4),
        "by_pack": dict(sorted(Counter(str(e["payload"].get("pack")) for e in sat).items())),
        "gifts_per_tick": r(len(g) / ticks, 4),
        "gifts_by_giver": dict(sorted(Counter(e["actor"] for e in g).items())),
    }


# ------------------------------------------------------------------ assembly


def build(data: dict[str, Any]) -> dict[str, Any]:
    cards = catalog_index(data["catalog"])
    infos = thread_infos(data["thread_opened"], data["thread_message"])
    dyn = thread_dynamics(data["thread_message"], infos)
    api_dealers = {d["id"]: d for d in data["dealers"]["personas"]}
    upcoming = data["schedule"]["upcoming"]
    news_rows = data["news"]["news"]
    notes = [
        "bench.finished is team-stream only: efficiency/auto_baseline come from .ai/memory.md BE1 (3 sessions).",
        "No Sunday data exists: every Sunday number is a Saturday fit; the schedule is the organisers' own.",
        "Schedule hours are game hours: the `day_opens sun` entry sits at h16.65 and `day_closes sun` at h22.65, "
        "exactly 1440 ticks of 15 s apart, so the Sunday ticks are (at_hours - 16.65) * 240. /api/clock shows t 13.37 "
        "now (Saturday ended early), so the organisers must jump the game clock or fire the entries before h16.65 "
        "(hard Market Test h14.65, Market Test h15.0) at the open: the scenario fires them at tick 1.",
        "Duel rival styles are not stored; fever sizes other than Pilar's Salamanca +25 % are not in the feed.",
        "Friday ticks (< 160) ran 60 s and are excluded from the rival rates.",
        "Request latency is a documented default unless Phoenix was reachable (see clock.latency_ms.source).",
    ]
    return {
        "version": 1,
        "generated_from": data["counts"],
        "clock": clock_section(data["limits"], data["latency"]),
        "schedule": schedule_section(upcoming),
        "news": news_section(news_rows),
        "dealers": dealer_section(data["dealer_curves"], infos, dyn, cards, api_dealers),
        "fevers": fever_section(data["persona_updated"], news_rows),
        "rivals": rivals_section(
            data["offer_listed"],
            data["offer_cancelled"],
            data["settlement"],
            data["venue_opened"],
            data["fee_changed"],
            cards,
            data["profiles"],
        ),
        "bench": bench_section(data["bench_started"], upcoming),
        "duels": duels_section(data["duels"], data["duel_closed"], data["duels_scheduled"]),
        "packs": packs_section(data["pack_opened"], data["gift_given"]),
        "notes": notes,
    }


def dumps(doc: dict[str, Any]) -> str:
    return json.dumps(doc, indent=1, sort_keys=True, ensure_ascii=False) + "\n"


# ------------------------------------------------------------------ IO


EVENT_TYPES = {
    "thread_message": "thread.message",
    "thread_opened": "thread.opened",
    "offer_listed": "offer.listed",
    "offer_cancelled": "offer.cancelled",
    "settlement": "settlement",
    "venue_opened": "venue.opened",
    "fee_changed": "venue.fee_changed",
    "persona_updated": "persona.updated",
    "bench_started": "bench.started",
    "duel_closed": "duel.closed",
    "duels_scheduled": "duels.scheduled",
    "pack_opened": "pack.opened",
    "gift_given": "gift.given",
}


def db_url(url_file: Path | None) -> str:
    url = os.environ.get("SIM_CALIBRATE_DB_URL")
    if url:
        return url.strip()
    if url_file is None or not url_file.exists():
        raise SystemExit("no database: set SIM_CALIBRATE_DB_URL or pass --db-url-file")
    return url_file.read_text(encoding="utf-8").strip()


def load_db(url: str) -> dict[str, Any]:
    import psycopg
    from psycopg.rows import dict_row

    out: dict[str, Any] = {}
    counts: dict[str, int] = {}
    with psycopg.connect(url, row_factory=dict_row, options="-c default_transaction_read_only=on") as conn:
        for key, etype in EVENT_TYPES.items():
            rows = conn.execute(
                "select id, tick, actor, payload from feed_events where type = %s order by id", (etype,)
            ).fetchall()
            out[key] = rows
            counts[etype] = len(rows)
        out["dealer_curves"] = conn.execute(
            "select thread_id, dealer, item, opening_ask, final_ask, outcome, fill_price, steps, ticks "
            "from dealer_curves"
        ).fetchall()
        out["duels"] = conn.execute(
            "select duel, session, status, role, your_limit, rounds, price, result, payload from duels"
        ).fetchall()
        out["profiles"] = conn.execute("select team, level, avg_pack_price, notes from competitor_profiles").fetchall()
        counts["dealer_curves"] = len(out["dealer_curves"])
        counts["duels"] = len(out["duels"])
        counts["competitor_profiles"] = len(out["profiles"])
        top = conn.execute("select max(tick) as m from feed_events").fetchone()
        counts["max_tick"] = int(top["m"]) if top else 0
    out["counts"] = dict(sorted(counts.items()))
    return out


def load_cal(cal: Path) -> dict[str, Any]:
    def j(name: str) -> Any:
        return json.loads((cal / name).read_text(encoding="utf-8"))

    clock = {
        "accepts_per_team_per_tick": 1,
        "messages_per_side_per_tick": 1,
        "max_open_threads_per_team": 6,
        "max_open_offers_per_team": 30,
        "offers_per_team_per_tick": 12,
    }
    return {
        "catalog": j("catalog.json"),
        "dealers": j("dealers.json"),
        "schedule": j("schedule.json"),
        "news": j("news.json"),
        "limits": clock,
    }


def latency_from_phoenix() -> dict[str, Any]:
    """Request latency from Phoenix when reachable. Not implemented against a live service here: the default
    is documented (a TLS connect per request, ~25-30 ms from Madrid, plus server time; .ai/memory.md)."""
    return {"p50": 40, "p95": 120, "source": "default (Phoenix not queried; .ai/memory.md tick profile: 25-30 ms TLS)"}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--db-url-file", type=Path, default=DEFAULT_DB_FILE)
    ap.add_argument("--cal-dir", type=Path, default=DEFAULT_CAL / "cal")
    args = ap.parse_args(argv)
    data = load_cal(args.cal_dir)
    data.update(load_db(db_url(args.db_url_file)))
    data["latency"] = latency_from_phoenix()
    doc = build(data)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(dumps(doc), encoding="utf-8")
    print(f"wrote {args.out} ({args.out.stat().st_size} bytes), sections: {', '.join(sorted(doc))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
