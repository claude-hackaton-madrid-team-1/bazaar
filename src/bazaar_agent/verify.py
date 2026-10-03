"""Morning assumption verifier: the night's plans rest on assumptions; each one gets a read-only check.

Every `Check` names the assumption, where it came from, the read-only endpoint and field that settle it,
the earliest time that works, and what flips on the answer (a PR, a GUARDRAILS.md or STRATEGY.md value,
a plan step). `evaluate()` runs the automatic ones over a `Snapshot` of API responses (captured fixtures
tonight, live GETs in the morning) and returns PASS / FAIL / UNKNOWN with the evidence; a manual check
is always UNKNOWN and says how to settle it by hand. Nothing here writes to the game.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from statistics import median
from typing import Any, Literal

from bazaar_agent import intel

Status = Literal["PASS", "FAIL", "UNKNOWN"]


@dataclass(frozen=True)
class Snapshot:
    """The read-only responses a morning run collects (any may be missing: its checks say UNKNOWN)."""

    clock: Mapping[str, Any] | None = None
    schedule: Mapping[str, Any] | None = None
    me: Mapping[str, Any] | None = None
    levels: Mapping[str, Any] | None = None
    dealers: Sequence[Mapping[str, Any]] | None = None
    venues: Sequence[Mapping[str, Any]] | None = None
    leaderboard: Mapping[str, Any] | None = None
    duels: Sequence[Mapping[str, Any]] | None = None
    events: Sequence[intel.Event] = ()
    team: str | None = None
    since_tick: int = 0  # the first tick of the day being checked (Saturday's first), for feed checks

    def today(self) -> list[intel.Event]:
        return [e for e in self.events if int(e.get("tick", 0)) >= self.since_tick]


@dataclass(frozen=True)
class Result:
    status: Status
    evidence: str


@dataclass(frozen=True)
class Check:
    id: str
    assumption: str
    when: str  # earliest time the check can settle it ("09:00", "after the h5 Market Test", ...)
    endpoint: str
    flips: str  # what changes with the answer
    if_unknown: str  # the safe default the plans use until it is settled
    priority: Literal["high", "medium", "low"] = "medium"
    sources: tuple[str, ...] = ()
    how: str = ""  # how to settle it by hand when `run` is None (or the data is not there yet)
    run: Callable[[Snapshot], Result] | None = None

    @property
    def manual(self) -> bool:
        return self.run is None


def evaluate(checks: Iterable[Check], snap: Snapshot) -> list[tuple[Check, Result]]:
    out = []
    for c in checks:
        if c.run is None:
            out.append((c, Result("UNKNOWN", f"manual: {c.how}")))
            continue
        try:
            out.append((c, c.run(snap)))
        except Exception as e:  # one malformed response never hides the other checks
            out.append((c, Result("UNKNOWN", f"could not evaluate ({type(e).__name__}: {e})")))
    return out


# ---------------------------------------------------------------- helpers


def _unknown(why: str) -> Result:
    return Result("UNKNOWN", why)


def _ok(cond: bool, yes: str, no: str) -> Result:
    return Result("PASS" if cond else "FAIL", yes if cond else no)


def _dealer(snap: Snapshot, dealer_id: str) -> Mapping[str, Any] | None:
    return next((d for d in snap.dealers or [] if d.get("id") == dealer_id), None)


def _schedule_items(snap: Snapshot, action: str) -> list[Mapping[str, Any]]:
    body = snap.schedule or {}
    return [u for u in body.get("upcoming") or [] if u.get("action") == action]


# ---------------------------------------------------------------- automatic checks


def clock_pace(snap: Snapshot) -> Result:
    if not snap.clock:
        return _unknown("no GET /api/clock")
    if snap.clock.get("today") != "sat":
        return _unknown(f"the clock says today is {snap.clock.get('today')!r}: run it once Saturday opens")
    secs = snap.clock.get("tick_seconds")
    return _ok(secs == 30.0, f"tick_seconds {secs}", f"tick_seconds {secs}, not 30: re-time every plan's slots")


def clock_anchor(snap: Snapshot) -> Result:
    """Saturday 09:00 is game hour 4.0 (the clock jumped) or 2.65 (it resumed where Friday froze)."""
    if not snap.clock or snap.clock.get("today") != "sat":
        return _unknown("needs Saturday's first GET /api/clock")
    t = float(snap.clock.get("t_hours") or 0)
    if t >= 3.95:
        return Result("PASS", f"t_hours {t:.2f}: the clock jumped to h4 (grant at 09:03, round 2 at 09:00)")
    return Result("FAIL", f"t_hours {t:.2f}: the clock resumed (grant ~10:24, Friday's round runs until h4)")


def limits_hold(snap: Snapshot) -> Result:
    if not snap.clock:
        return _unknown("no GET /api/clock")
    lim = snap.clock.get("limits") or {}
    want = {"accepts_per_team_per_tick": 1, "offers_per_team_per_tick": 12, "max_open_threads_per_team": 6}
    off = {k: lim.get(k) for k, v in want.items() if lim.get(k) != v}
    return _ok(not off, f"limits as planned: {want}", f"limits moved: {off} (planned {want})")


def grant_scheduled(snap: Snapshot) -> Result:
    grants = [g for g in _schedule_items(snap, "grant_all") if (g.get("params") or {}).get("cash")]
    if not grants and not snap.schedule:
        return _unknown("no GET /api/schedule")
    sat = [g for g in grants if 4.0 <= float(g.get("at_hours") or 0) < 18.0]
    if sat:
        g = sat[0]
        cash = (g.get("params") or {}).get("cash")
        return _ok(cash == 150, f"grant {cash} P at h{g.get('at_hours')}", f"grant is {cash} P, not 150")
    paid = [e for e in snap.today() if e.get("type") in ("grant", "grant.given", "grant_all")]
    if paid:
        return Result("PASS", f"grant event seen at tick {paid[0].get('tick')}")
    return _unknown("no Saturday grant in the schedule or the feed yet")


def ladder_resets(snap: Snapshot) -> Result:
    """If Saturday's round restarts the best three, our ladder_points falls to ~0 when it opens."""
    if not snap.me or not snap.clock or snap.clock.get("today") != "sat":
        return _unknown("needs /api/me on Saturday, before our first Saturday dealer deal")
    score = snap.me.get("score") or {}
    pts = float(score.get("ladder_points") or 0)
    return _ok(
        pts < 0.01,
        f"ladder_points {pts}: the best three restart per round (make 3 new deals per level)",
        f"ladder_points {pts}: Friday's deals still count (only better deals replace them)",
    )


def level_and_unlocks(snap: Snapshot) -> Result:
    if not snap.me:
        return _unknown("no GET /api/me")
    unlocked = list(snap.me.get("unlocked") or [])
    return _ok(
        "chato" in unlocked,
        f"level {snap.me.get('level')}, unlocked {unlocked}",
        f"El Chato is not unlocked for us: unlocked {unlocked}",
    )


def abuela_menu(snap: Snapshot) -> Result:
    d = _dealer(snap, "abuela")
    if d is None:
        return _unknown("no Abuela in GET /api/dealers")
    menu = d.get("menu") or {}
    pack: Mapping[str, Any] = next((s for s in menu.get("sells") or [] if s.get("pack") == "sobre_barrio"), {})
    seen = (menu.get("deals_per_team_per_hour"), pack.get("opening_ask"), pack.get("per_team_per_hour"))
    return _ok(
        seen == (8, 30, 3), "8 deals/h, pack opening 30, 3 packs/h", f"(deals/h, pack opening, packs/h) = {seen}"
    )


def chato_menu(snap: Snapshot) -> Result:
    d = _dealer(snap, "chato")
    if d is None:
        return _unknown("El Chato is not in GET /api/dealers (the fixture predates him)")
    menu = d.get("menu") or {}
    return Result("PASS", f"deals/h {menu.get('deals_per_team_per_hour')}, sells {len(menu.get('sells') or [])}")


def openings_hold(snap: Snapshot) -> Result:
    """Abuela still opens uncommons at 29 and commons at 12 (W3's floors rest on that regime)."""
    threads = [
        t for t in intel.dealer_threads(snap.today()) if t.dealer == "abuela" and t.side == "buy" and t.opening_ask
    ]
    if len(threads) < 3:
        return _unknown(f"{len(threads)} Abuela threads in today's feed: wait for 3")
    openings = sorted({int(t.opening_ask or 0) for t in threads})
    return _ok(
        set(openings) <= {7, 12, 17, 29, 30}, f"openings {openings}", f"new openings {openings}: re-fit the floors"
    )


def floors_hold(snap: Snapshot) -> Result:
    """Today's Abuela fills sit where W3's floor table says (uncommons ~23, commons ~10)."""
    threads = [
        t for t in intel.dealer_threads(snap.today()) if t.dealer == "abuela" and t.side == "buy" and t.fill_price
    ]
    unc = [int(t.fill_price or 0) for t in threads if t.opening_ask == 29]
    com = [int(t.fill_price or 0) for t in threads if t.opening_ask == 12]
    if len(unc) + len(com) < 4:
        return _unknown(f"{len(unc) + len(com)} Abuela card fills today: wait for 4")
    bits, ok = [], True
    if unc:
        m = median(unc)
        bits.append(f"uncommon median fill {m:g}")
        ok &= 21 <= m <= 25
    if com:
        m = median(com)
        bits.append(f"common median fill {m:g}")
        ok &= 8 <= m <= 11
    return _ok(ok, "; ".join(bits), "; ".join(bits) + " (outside W3's range: re-run `bazaar ladder floors`)")


def l3_announced(snap: Snapshot) -> Result:
    known = ("abuela", "chato")
    names = [d.get("id") for d in snap.dealers or [] if d.get("id") not in known]
    announced = [
        e
        for e in snap.today()
        if e.get("type") == "level.announced"
        and (e.get("payload") or {}).get("level") not in known
        and (e.get("payload") or {}).get("persona") not in known
    ]
    if names or announced:
        who = names or [(e.get("payload") or {}).get("name") for e in announced]
        return Result("PASS", f"a new level is announced: {who} (run `bazaar plan levels`)")
    return _unknown("no third dealer yet (watch level.announced)")


def unlock_rule(snap: Snapshot) -> Result:
    """The next dealer's unlock block names El Chato and 3 deals (B21's analogy)."""
    new = [d for d in snap.dealers or [] if d.get("id") not in ("abuela", "chato")]
    if not new:
        return _unknown("no L3 dealer in GET /api/dealers yet")
    unlock = new[0].get("unlock") or {}
    seen = (unlock.get("early_deals_with"), unlock.get("early_min_deals"))
    return _ok(seen == ("chato", 3), f"{new[0].get('id')}: 3 deals with chato", f"{new[0].get('id')}: unlock {unlock}")


def stall_scores(snap: Snapshot) -> Result:
    """Without a venue, does the free starter stall score Market Test points for us?"""
    if not snap.me:
        return _unknown("no GET /api/me")
    score = snap.me.get("score") or {}
    pts, eff = score.get("bench_points"), score.get("bench_efficiency")
    if pts is None and eff is None:
        return _unknown("no Market Test has scored us yet (first at h3/h5)")
    return _ok(
        bool(pts), f"bench_points {pts} (efficiency {eff})", f"bench_points {pts}: the stall does not score for us"
    )


def days_meaning(snap: Snapshot) -> Result:
    two = [d for d in snap.duels or [] if d.get("your_days_weight") is not None]
    if not two:
        return _unknown("no two-issue duel payload yet (Duels II, h13)")
    meaning = str(two[0].get("days_meaning") or "")
    if not meaning:
        return _unknown("days_meaning is null in the real payload (as Friday's practice): keep the worst case")
    gain = re.search(r"\(\+\)|gain", meaning) is not None
    return _ok(gain, f"days_meaning {meaning!r}: signed weights", f"days_meaning {meaning!r}")


def cash_at_open(snap: Snapshot) -> Result:
    """W7's cash plan starts from 353 P (Friday's close) before the grant."""
    if not snap.me or not snap.clock or snap.clock.get("today") != "sat":
        return _unknown("needs /api/me at Saturday's open")
    cash = snap.me.get("cash")
    return _ok(
        cash in (353, 503), f"cash {cash}", f"cash {cash}, not 353 (or 503 after the grant): re-run `plan pages`"
    )


def tick_continues(snap: Snapshot) -> Result:
    """Saturday's ticks continue Friday's numbering (feed captures and --since-tick rely on it)."""
    if not snap.clock or snap.clock.get("today") != "sat":
        return _unknown("needs Saturday's GET /api/clock")
    tick = int(snap.clock.get("tick") or 0)
    return _ok(tick >= 159, f"tick {tick}", f"tick {tick}: the numbering restarted; rebase every --since-tick")


def retiro_released(snap: Snapshot) -> Result:
    """El Retiro (RET) is released for round 2: our album gets its page and dealers sell it."""
    if not snap.me:
        return _unknown("no GET /api/me")
    pages = [str(p.get("set")) for p in (snap.me.get("album") or {}).get("pages") or []]
    if not snap.clock or snap.clock.get("today") != "sat":
        return _unknown(f"not Saturday yet (album pages {pages})")
    return _ok("RET" in pages, f"album pages {pages}", f"RET not in our album yet: {pages}")


def market_tests_scheduled(snap: Snapshot) -> Result:
    """The Market Test runs every 2 h on Saturday (h5, 7, 9, 11, 13, 15, the hard one at 16, 17)."""
    benches = sorted(float(b.get("at_hours") or 0) for b in _schedule_items(snap, "bench"))
    if not benches and not snap.schedule:
        return _unknown("no GET /api/schedule")
    sat = [h for h in benches if 4.0 <= h < 18.0]
    want = [5.0, 7.0, 9.0, 11.0, 13.0, 15.0, 16.0, 17.0]
    return _ok(sat == want, f"Saturday sessions at h{sat}", f"Saturday sessions at h{sat}, planned h{want}")


AUTOMATIC: dict[str, Callable[[Snapshot], Result]] = {
    "cash-at-open": cash_at_open,
    "tick-numbering-continues": tick_continues,
    "retiro-released": retiro_released,
    "market-test-schedule": market_tests_scheduled,
    "clock-pace-30s": clock_pace,
    "clock-anchor": clock_anchor,
    "limits-unchanged": limits_hold,
    "grant-150-saturday": grant_scheduled,
    "ladder-resets-per-round": ladder_resets,
    "chato-unlocked": level_and_unlocks,
    "abuela-menu": abuela_menu,
    "chato-menu": chato_menu,
    "abuela-openings": openings_hold,
    "abuela-floors": floors_hold,
    "l3-announced": l3_announced,
    "l3-unlock-rule": unlock_rule,
    "stall-scores": stall_scores,
    "days-meaning-sign": days_meaning,
}


@dataclass(frozen=True)
class Report:
    rows: tuple[tuple[Check, Result], ...] = field(default_factory=tuple)

    def counts(self) -> dict[str, int]:
        out = {"PASS": 0, "FAIL": 0, "UNKNOWN": 0}
        for _, r in self.rows:
            out[r.status] += 1
        return out


# ---------------------------------------------------------------- the catalogue and the snapshot

CATALOGUE = Path(__file__).parent / "data" / "assumptions.json"


def load_checks(path: Path = CATALOGUE) -> list[Check]:
    """The harvested assumptions (`data/assumptions.json`); those with an automatic check get its `run`."""
    rows = json.loads(path.read_text(encoding="utf-8"))
    out = []
    for r in rows:
        out.append(
            Check(
                id=str(r["id"]),
                assumption=str(r["assumption"]),
                when=str(r.get("when") or "?"),
                endpoint=str(r.get("endpoint") or ""),
                flips=str(r.get("flips") or ""),
                if_unknown=str(r.get("if_unknown") or ""),
                priority=r.get("priority") if r.get("priority") in ("high", "medium", "low") else "medium",
                sources=tuple(str(s) for s in r.get("sources") or ()),
                how=str(r.get("verify_how") or ""),
                run=AUTOMATIC.get(str(r["id"])),
            )
        )
    return out


def _body(data: Any) -> Any:
    return data.get("body", data) if isinstance(data, dict) and "body" in data else data


def _list(data: Any, *keys: str) -> list[Mapping[str, Any]]:
    data = _body(data)
    if isinstance(data, list):
        return [d for d in data if isinstance(d, dict)]
    for key in keys:
        if isinstance(data, dict) and isinstance(data.get(key), list):
            return [d for d in data[key] if isinstance(d, dict)]
    return []


FIXTURE_FILES = {
    "clock": "get_api_clock.anon.json",
    "schedule": "get_api_schedule.anon.json",
    "me": "get_api_me.team.json",
    "levels": "get_api_levels.anon.json",
    "dealers": "get_api_dealers.anon.json",
    "venues": "get_api_venues.anon.json",
    "leaderboard": "get_api_leaderboard.anon.json",
    "duels": "get_api_duels.team.json",
}


def snapshot_from_dir(
    directory: Path, events: Sequence[intel.Event] = (), team: str | None = None, since_tick: int = 0
) -> Snapshot:
    """Captured responses (the repo's API fixtures, or a morning capture in the same layout)."""

    def read(name: str) -> Any:
        path = directory / FIXTURE_FILES[name]
        return _body(json.loads(path.read_text(encoding="utf-8"))) if path.is_file() else None

    return Snapshot(
        clock=read("clock"),
        schedule=read("schedule"),
        me=read("me"),
        levels=read("levels"),
        dealers=_list(read("dealers"), "personas", "dealers") or None,
        venues=_list(read("venues"), "venues") or None,
        leaderboard=read("leaderboard"),
        duels=_list(read("duels"), "duels") or None,
        events=events,
        team=team,
        since_tick=since_tick,
    )


def snapshot_live(public: Any, team_client: Any | None, events: Sequence[intel.Event], since_tick: int = 0) -> Snapshot:
    """Read-only GETs: public endpoints without a key, `/me` and `/duels` with the team key (reads only)."""
    me = team_client.me() if team_client is not None else None
    duels = _list(team_client.duels(), "duels") if team_client is not None else None
    return Snapshot(
        clock=public.clock(),
        schedule=public.schedule(),
        me=me,
        levels=public.levels(),
        dealers=_list(public.dealers(), "personas", "dealers"),
        venues=_list(public.venues(), "venues"),
        leaderboard=public.leaderboard(),
        duels=duels,
        events=events,
        team=str(me.get("id")) if me else None,
        since_tick=since_tick,
    )
