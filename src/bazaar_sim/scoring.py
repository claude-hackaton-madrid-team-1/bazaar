"""Scores and the team's own view (`GET /api/me`), an approximation of RULES.md "Scoring".

- Ladder: per dealer level, the share of each deal's price range captured (opening ask to the
  dealer's secret limit); the best three deals per level count, a missing one as zero, and higher
  levels weigh more. A deal at the opening price counts zero.
- Trades: the value gained with other teams, at private values (`Team.trade_gain`).
- Duels: the share of each deal's pie captured, shrunk by the rounds of talk (`Team.duel_points`).
- Market-making: the bench efficiency and value created on the team's venue (`Team.mm_points`).
Luck (packs) is shown but never counts. The public board refreshes every few ticks.
"""

from __future__ import annotations

from typing import Any

from bazaar_sim import catalog
from bazaar_sim.models import Team
from bazaar_sim.views import asset_brief, asset_name, venues_view
from bazaar_sim.world import World

LEVEL_OF = {"abuela": 1, "chato": 2}
LEVEL_WEIGHT = {1: 1.0, 2: 1.5}
LADDER_SCALE = 10.0
REFRESH_TICKS = 5


def ladder_points(team: Team) -> float:
    total = 0.0
    for dealer, level in LEVEL_OF.items():
        shares = sorted((d.share for d in team.deals if d.dealer == dealer and d.negotiated), reverse=True)[:3]
        total += LEVEL_WEIGHT[level] * LADDER_SCALE * sum(shares) / 3
    return round(total, 2)


def album(w: World, team_id: str) -> dict[str, Any]:
    held = w.held_counts(team_id)
    pages: list[dict[str, Any]] = []
    for code in catalog.released_sets():
        cards = catalog.page_cards(code)
        have = sum(1 for c in cards if held.get(c.ref, 0) > 0)
        pages.append(
            {
                "complete": have == len(cards),
                "have": have,
                "master": catalog.master_complete(code, held),
                "name": catalog.set_name(code),
                "of": len(cards),
                "set": code,
            }
        )
    filled = sum(p["have"] for p in pages)
    return {"filled": filled, "pages": pages, "slots": sum(p["of"] for p in pages)}


def rarest(w: World, team_id: str) -> dict[str, Any] | None:
    cards = [a for a in w.holdings(team_id) if a.kind == "card"]
    if not cards:
        return None
    order = {r: i for i, r in enumerate(catalog.RARITY_ORDER)}
    best = max(cards, key=lambda a: (order[catalog.cards()[a.ref].rarity], -a.serial))
    brief = asset_brief(best)
    return {
        "name": asset_name(best),
        "print_run": brief["print_run"],
        "rarity": brief["rarity"],
        "ref": best.ref,
        "serial": best.serial,
    }


def score_row(w: World, team: Team) -> dict[str, Any]:
    pages = album(w, team.id)
    ladder = ladder_points(team)
    negotiating = round(ladder + team.duel_points + team.trade_gain, 2)
    market = round(team.mm_points, 2)
    return {
        "adjustments": [],
        "album_filled": pages["filled"],
        "album_slots": pages["slots"],
        "badges": list(team.badges),
        "deals": len(team.deals),
        "frozen": team.frozen,
        "level": len(team.unlocked),
        "luck": round(team.luck, 2),
        "market": market,
        "name": team.name,
        "negotiating": negotiating,
        "pages_complete": sum(1 for p in pages["pages"] if p["complete"]),
        "rank": 0,
        "rarest": rarest(w, team.id),
        "score": round(negotiating + market, 2),
        "team": team.id,
        "venue": team.venue,
    }


def ranked_rows(w: World) -> list[dict[str, Any]]:
    rows = [score_row(w, t) for t in w.state.teams.values()]
    rows.sort(key=lambda r: (-r["score"], r["team"]))
    return [{**r, "rank": i + 1} for i, r in enumerate(rows)]


def snapshot_if_due(w: World) -> None:
    if w.tick % REFRESH_TICKS == 0 or not w.state.leaderboard:
        w.state.leaderboard = ranked_rows(w)
        w.state.leaderboard_tick = w.tick


def leaderboard_view(w: World) -> dict[str, Any]:
    if not w.state.leaderboard:
        snapshot_if_due(w)
    return {
        "currency": "P",
        "next_refresh_tick": w.state.leaderboard_tick + REFRESH_TICKS,
        "round": 1,
        "rounds": [{"name": "Simulator · El Rastro", "phase": 0.0, "round": 1, "status": "active", "weight": 1.0}],
        "snapshot_tick": w.state.leaderboard_tick,
        "t": w.t_hours,
        "teams": w.state.leaderboard,
        "tick": w.tick,
        "venues": venues_view(w)["venues"],
        "weights": {"market": 30.0, "negotiating": 30.0},
    }


def my_score(w: World, team: Team) -> dict[str, Any]:
    live = {r["team"]: r["rank"] for r in ranked_rows(w)}
    row = score_row(w, team)
    return {
        **row,
        "rank": live.get(team.id, 0),
        "neg_points": round(team.trade_gain, 2),
        "mm_points": round(team.mm_points, 2),
        "duel_points": round(team.duel_points, 2),
        "ladder_points": ladder_points(team),
        "bench_efficiency": team.bench_efficiency,
        "bench_points": round(team.mm_points, 2) if team.bench_efficiency is not None else None,
        "bench_venue": team.bench_venue,
        "luck_private": round(team.luck, 2),
    }


def asset_view(w: World, team: Team, counts: dict[str, int], asset: Any) -> dict[str, Any]:
    view = {**asset_brief(asset), "name": asset_name(asset)}
    if asset.kind == "card":
        view["your_value"] = catalog.held_copy_value(asset.ref, counts.get(asset.ref, 1), team.affinity)
    else:
        view["your_value"] = float(catalog.packs()[asset.ref]["expected_book"])
    return view


def me_view(w: World, team_id: str) -> dict[str, Any]:
    team = w.team(team_id)
    counts = w.held_counts(team_id)
    assets = sorted(w.holdings(team_id), key=lambda a: a.id)
    open_threads = [
        {"id": th.id, "with": th.with_, "kind": th.kind, "topic": th.topic, "status": th.status}
        for th in w.state.threads.values()
        if th.status == "open" and team_id in (th.team, th.with_)
    ]
    venue = w.state.venues.get(team.venue) if team.venue else None
    return {
        "affinity": dict(team.affinity),
        "album": album(w, team_id),
        "assets": [asset_view(w, team, counts, a) for a in assets],
        "badges": list(team.badges),
        "cash": team.cash,
        "collection_value": catalog.collection_value(dict(counts), team.affinity, catalog.released_sets()),
        "frozen": team.frozen,
        "id": team.id,
        "level": len(team.unlocked),
        "name": team.name,
        "open_threads": open_threads,
        "score": my_score(w, team),
        "tick": w.tick,
        "tick_seconds": w.state.clock.tick_seconds,
        "unlocked": list(team.unlocked),
        "venue": {"venue": venue.venue, "name": venue.name, "status": venue.status} if venue else None,
    }


def value_view(w: World, team_id: str, ref: str) -> dict[str, Any]:
    from bazaar_sim.errors import invalid

    if catalog.card(ref) is None:
        raise invalid(f"unknown card {ref!r}")
    team = w.team(team_id)
    return {"card": ref, "your_value": catalog.one_more_value(ref, w.held_counts(team_id).get(ref, 0), team.affinity)}
