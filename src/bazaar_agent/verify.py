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
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
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
    catalog: Mapping[str, Any] | None = None
    now: datetime | None = None  # when the snapshot was read (time-dependent checks need it)

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
# A check says PASS or FAIL only on the fields it needs: a missing or renamed field is UNKNOWN, never 0.


def _unknown(why: str) -> Result:
    return Result("UNKNOWN", why)


def _ok(cond: bool, yes: str, no: str) -> Result:
    return Result("PASS" if cond else "FAIL", yes if cond else no)


def _dealer(snap: Snapshot, dealer_id: str) -> Mapping[str, Any] | None:
    return next((d for d in snap.dealers or [] if d.get("id") == dealer_id), None)


def _schedule_items(snap: Snapshot, action: str) -> list[Mapping[str, Any]]:
    body = snap.schedule or {}
    return [u for u in body.get("upcoming") or [] if u.get("action") == action]


def _saturday(snap: Snapshot) -> Mapping[str, Any] | None:
    return snap.clock if snap.clock and snap.clock.get("today") == "sat" else None


def hours_since_open(clock: Mapping[str, Any], now: datetime | None) -> float | None:
    """Real hours since today's doors opened (`days[].opens` for `today`), if both are known."""
    day = next((d for d in clock.get("days") or [] if d.get("day") == clock.get("today")), None)
    if now is None or not day or not day.get("opens"):
        return None
    return (now - datetime.fromisoformat(str(day["opens"]))).total_seconds() / 3600


def first_tick_today(clock: Mapping[str, Any], now: datetime | None) -> int | None:
    """Today's first tick, from the clock's tick, its pace and the time since the doors opened."""
    since, tick, secs = hours_since_open(clock, now), clock.get("tick"), clock.get("tick_seconds")
    if since is None or tick is None or not secs:
        return None
    return max(0, int(tick) - int(since * 3600 / float(secs)))


def _rarities(snap: Snapshot) -> dict[str, str]:
    return {
        str(c.get("id")): str(c.get("rarity"))
        for s in (snap.catalog or {}).get("sets") or []
        for c in s.get("cards") or []
    }


def _our_settlements_today(snap: Snapshot, dealers_only: bool = False) -> list[intel.Event]:
    out = []
    for e in snap.today():
        p = e.get("payload") or {}
        ours = e.get("type") == "settlement" and snap.team in (p.get("parties") or [])
        if ours and (p.get("persona") or not dealers_only):
            out.append(e)
    return out


def _grant_fired_today(snap: Snapshot) -> bool:
    return any(
        e.get("type") == "schedule.fired" and (e.get("payload") or {}).get("action") == "grant_all"
        for e in snap.today()
    )


# ---------------------------------------------------------------- automatic checks


def clock_pace(snap: Snapshot) -> Result:
    if not snap.clock:
        return _unknown("no GET /api/clock")
    if _saturday(snap) is None:
        return _unknown(f"the clock says today is {snap.clock.get('today')!r}: run it once Saturday opens")
    secs = snap.clock.get("tick_seconds")
    if secs is None:
        return _unknown("no tick_seconds in GET /api/clock")
    return _ok(secs == 30.0, f"tick_seconds {secs}", f"tick_seconds {secs}, not 30: re-time every plan's slots")


def clock_anchor(snap: Snapshot) -> Result:
    """Saturday 09:00 is game hour 4.0 (the clock jumped) or 2.65 (it resumed where Friday froze). A late
    run subtracts the real time since the doors opened (a game hour is a real hour)."""
    clock = _saturday(snap)
    if clock is None:
        return _unknown("needs Saturday's first GET /api/clock")
    if clock.get("t_hours") is None:
        return _unknown("no t_hours in GET /api/clock")
    t, since = float(clock["t_hours"]), hours_since_open(clock, snap.now)
    if since is None:
        return _unknown(f"t_hours {t:.2f}, but the time since Saturday's open is unknown")
    at_open = t - since
    if abs(at_open - 4.0) <= 0.25:
        return Result("PASS", f"h{at_open:.2f} at the open: the clock jumped to h4 (grant 09:03, round 2 from 09:00)")
    if abs(at_open - 2.65) <= 0.25:
        return Result("FAIL", f"h{at_open:.2f} at the open: the clock resumed (grant ~10:24, Friday's round to h4)")
    return _unknown(f"h{at_open:.2f} at the open is neither 4.0 nor 2.65: re-timed? read `bazaar timeline`")


def limits_hold(snap: Snapshot) -> Result:
    if not snap.clock:
        return _unknown("no GET /api/clock")
    lim = snap.clock.get("limits") or {}
    want = {"accepts_per_team_per_tick": 1, "offers_per_team_per_tick": 12, "max_open_threads_per_team": 6}
    missing = [k for k in want if lim.get(k) is None]
    if missing:
        return _unknown(f"limits without {missing} in GET /api/clock")
    off = {k: lim.get(k) for k, v in want.items() if lim.get(k) != v}
    return _ok(not off, f"limits as planned: {want}", f"limits moved: {off} (planned {want})")


def grant_scheduled(snap: Snapshot) -> Result:
    grants = [g for g in _schedule_items(snap, "grant_all") if (g.get("params") or {}).get("cash")]
    sat = [g for g in grants if 4.0 <= float(g.get("at_hours") or 0) < 18.0]
    if sat:
        g = sat[0]
        cash = (g.get("params") or {}).get("cash")
        return _ok(cash == 150, f"grant {cash} P at h{g.get('at_hours')}", f"grant is {cash} P, not 150")
    if _grant_fired_today(snap):  # once it fires it leaves `upcoming`
        return Result("PASS", "the grant fired today (schedule.fired grant_all): check /me cash rose by 150")
    if not snap.schedule:
        return _unknown("no GET /api/schedule")
    return _unknown("no Saturday grant in the schedule or the feed")


def ladder_resets(snap: Snapshot) -> Result:
    """If Saturday's round restarts the best three, our ladder_points falls to ~0 when it opens. Only
    readable before our first Saturday dealer deal."""
    if not snap.me or _saturday(snap) is None:
        return _unknown("needs /api/me on Saturday, before our first Saturday dealer deal")
    score = snap.me.get("score") or {}
    if score.get("ladder_points") is None:
        return _unknown("no score.ladder_points in GET /api/me")
    pts = float(score["ladder_points"])
    if pts < 0.01:
        return Result("PASS", f"ladder_points {pts}: the best three restart per round (make 3 new deals per level)")
    if not snap.events or snap.team is None:
        return _unknown(f"ladder_points {pts}: needs today's feed to rule out a Saturday deal of ours")
    if _our_settlements_today(snap, dealers_only=True):
        return _unknown(f"ladder_points {pts} after our first Saturday dealer deal: too late to tell")
    return Result("FAIL", f"ladder_points {pts} before any Saturday deal: Friday's deals still count")


def level_and_unlocks(snap: Snapshot) -> Result:
    if not snap.me:
        return _unknown("no GET /api/me")
    if snap.me.get("unlocked") is None:
        return _unknown("no `unlocked` in GET /api/me")
    unlocked = list(snap.me["unlocked"])
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
    if None in seen:
        return _unknown(f"(deals/h, pack opening, packs/h) = {seen}: a field is missing")
    return _ok(
        seen == (8, 30, 3), "8 deals/h, pack opening 30, 3 packs/h", f"(deals/h, pack opening, packs/h) = {seen}"
    )


def chato_menu(snap: Snapshot) -> Result:
    """El Chato's menu as modelled: 6 deals/h, uncommons listed at 30, rares at 90 (his card openings,
    33 and 97, show only in his threads)."""
    d = _dealer(snap, "chato")
    if d is None:
        return _unknown("El Chato is not in GET /api/dealers (the fixture predates him)")
    menu = d.get("menu") or {}
    sells = menu.get("sells") or []

    def listed(rarity: str) -> Any:
        return next((s.get("list_price") for s in sells if s.get("rarity") == rarity), None)

    seen = (menu.get("deals_per_team_per_hour"), listed("uncommon"), listed("rare"))
    if None in seen:
        return _unknown(f"(deals/h, uncommon list, rare list) = {seen}: a field is missing")
    return _ok(seen == (6, 30, 90), "6 deals/h, uncommons 30, rares 90", f"(deals/h, uncommon, rare) = {seen}")


def _abuela_card_buys(snap: Snapshot) -> dict[str, list[intel.DealerThread]] | None:
    """Today's Abuela card buys by rarity (packs and sales left out), or None without the catalog."""
    rarity = _rarities(snap)
    if not rarity:
        return None
    out: dict[str, list[intel.DealerThread]] = {}
    for t in intel.dealer_threads(snap.today()):
        if t.dealer == "abuela" and t.side == "buy" and t.item in rarity:
            out.setdefault(rarity[t.item], []).append(t)
    return out


def openings_hold(snap: Snapshot) -> Result:
    """Abuela still opens uncommons at 29 and commons at 12 (W3's floors rest on that regime)."""
    by = _abuela_card_buys(snap)
    if by is None:
        return _unknown("no GET /api/catalog to tell commons from uncommons")
    seen = {r: sorted(int(t.opening_ask) for t in by.get(r, []) if t.opening_ask) for r in ("common", "uncommon")}
    if sum(len(v) for v in seen.values()) < 3:
        return _unknown(f"{sum(len(v) for v in seen.values())} Abuela card buy threads today: wait for 3")
    want = {"common": 12, "uncommon": 29}
    off = {r: v for r, v in seen.items() if v and median(v) != want[r]}
    counts = {r: dict(sorted(Counter(v).items())) for r, v in seen.items()}  # opening: threads
    return _ok(not off, f"openings {counts}", f"openings {counts}, planned {want}: re-fit the floors")


def floors_hold(snap: Snapshot) -> Result:
    """Today's Abuela fills sit where W3's floor table says (uncommons 21–25, commons 8–11)."""
    by = _abuela_card_buys(snap)
    if by is None:
        return _unknown("no GET /api/catalog to tell commons from uncommons")
    fills = {r: [int(t.fill_price) for t in by.get(r, []) if t.fill_price] for r in ("common", "uncommon")}
    if sum(len(v) for v in fills.values()) < 4:
        return _unknown(f"{sum(len(v) for v in fills.values())} Abuela card fills today: wait for 4")
    ranges = {"common": (8, 11), "uncommon": (21, 25)}
    bits, ok = [], True
    for r, v in fills.items():
        if v:
            m = median(v)
            bits.append(f"{r} median fill {m:g}")
            ok &= ranges[r][0] <= m <= ranges[r][1]
    return _ok(ok, "; ".join(bits), "; ".join(bits) + " (outside W3's range: re-run `bazaar ladder floors`)")


def l3_announced(snap: Snapshot) -> Result:
    known = ("abuela", "chato")
    names = [d.get("id") for d in snap.dealers or [] if d.get("id") not in known]
    announced = [
        e
        for e in snap.today()
        if e.get("type") == "level.announced"
        and (e.get("payload") or {}).get("kind") == "persona"  # a dealer, not a new mechanic
        and (e.get("payload") or {}).get("level") not in known
    ]
    if names or announced:
        who = names or [(e.get("payload") or {}).get("name") for e in announced]
        return Result("PASS", f"a new dealer is announced: {who} (run `bazaar plan levels`)")
    return _unknown("no third dealer yet (watch level.announced)")


def unlock_rule(snap: Snapshot) -> Result:
    """The next dealer's unlock block names El Chato and 3 deals (B21's analogy)."""
    new = [d for d in snap.dealers or [] if d.get("id") not in ("abuela", "chato")]
    if not new:
        return _unknown("no L3 dealer in GET /api/dealers yet")
    unlock = new[0].get("unlock")
    if not unlock:
        return _unknown(f"{new[0].get('id')} has no unlock block: read GET /api/dealers/{new[0].get('id')}")
    seen = (unlock.get("early_deals_with"), unlock.get("early_min_deals"))
    return _ok(seen == ("chato", 3), f"{new[0].get('id')}: 3 deals with chato", f"{new[0].get('id')}: unlock {unlock}")


def stall_scores(snap: Snapshot) -> Result:
    """Without a venue of ours, does the free starter stall score Market Test points for us?"""
    if not snap.me:
        return _unknown("no GET /api/me")
    if snap.me.get("venue"):
        return _unknown("we run our own venue: the Market Test scores it, not the stall")
    score = snap.me.get("score") or {}
    pts, where = score.get("bench_points"), score.get("bench_venue")
    if pts is None:
        return _unknown("no Market Test has scored us yet (first at h3/h5)")
    if float(pts) > 0:
        return Result("PASS", f"bench_points {pts} (venue {where}, efficiency {score.get('bench_efficiency')})")
    if where:
        return _unknown(f"bench_points 0 on {where}: scored, but a bad session; read the next one")
    return Result("FAIL", "bench_points 0 and no bench_venue: the stall does not score for us")


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
    """W7's cash plan starts from 353 P (Friday's close), 503 once the grant fired. Only readable before
    our first Saturday settlement."""
    if not snap.me or _saturday(snap) is None:
        return _unknown("needs /api/me at Saturday's open")
    cash = snap.me.get("cash")
    if cash is None:
        return _unknown("no cash in GET /api/me")
    if not snap.events or snap.team is None:
        return _unknown(f"cash {cash}: needs today's feed to rule out a Saturday settlement of ours")
    if _our_settlements_today(snap):
        return _unknown(f"cash {cash} after our first Saturday settlement: too late to tell")
    want = 503 if _grant_fired_today(snap) else 353
    return _ok(cash == want, f"cash {cash}", f"cash {cash}, not {want}: re-run `bazaar plan pages`")


def tick_continues(snap: Snapshot) -> Result:
    """Saturday's ticks continue Friday's numbering (feed captures and --since-tick rely on it)."""
    clock = _saturday(snap)
    if clock is None:
        return _unknown("needs Saturday's GET /api/clock")
    first = first_tick_today(clock, snap.now)
    if first is None:
        return _unknown(f"tick {clock.get('tick')}, but the time since the open is unknown")
    if first >= 150:
        return Result("PASS", f"Saturday's first tick ~{first}: the numbering continues")
    if first < 30:
        return Result("FAIL", f"Saturday's first tick ~{first}: the numbering restarted; rebase every --since-tick")
    return _unknown(f"Saturday's first tick ~{first}: neither a restart nor Friday's 160")


def retiro_released(snap: Snapshot) -> Result:
    """El Retiro (RET) is released for round 2 (GET /api/catalog `sets[].released`)."""
    ret = next((s for s in (snap.catalog or {}).get("sets") or [] if s.get("id") == "RET"), None)
    if ret is None:
        return _unknown("no RET set in GET /api/catalog")
    if _saturday(snap) is None:
        return _unknown(f"not Saturday yet (RET released: {ret.get('released')})")
    return _ok(ret.get("released") is True, "RET released", f"RET not released: {ret.get('release')}")


def market_tests_scheduled(snap: Snapshot) -> Result:
    """The Market Test runs every 2 h on Saturday (h5, 7, 9, 11, 13, 15, the hard one at 16, 17). Sessions
    already run leave `upcoming`, so only the ones still ahead are compared."""
    if not snap.schedule:
        return _unknown("no GET /api/schedule")
    now = float(snap.schedule.get("now_hours") or 0)
    benches = sorted(float(b.get("at_hours") or 0) for b in _schedule_items(snap, "bench"))
    sat = [h for h in benches if 4.0 <= h < 18.0 and h > now]
    want = [h for h in (5.0, 7.0, 9.0, 11.0, 13.0, 15.0, 16.0, 17.0) if h > now]
    return _ok(sat == want, f"Saturday sessions ahead at h{sat}", f"Saturday sessions ahead at h{sat}, planned h{want}")


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
    "catalog": "get_api_catalog.anon.json",
}


def snapshot_from_dir(
    directory: Path,
    events: Sequence[intel.Event] = (),
    team: str | None = None,
    since_tick: int = 0,
    now: datetime | None = None,
) -> Snapshot:
    """Captured responses (the repo's API fixtures, or a morning capture in the same layout). `now` is when
    they were captured; without it the time-dependent checks say UNKNOWN."""

    def read(name: str) -> Any:
        path = directory / FIXTURE_FILES[name]
        return _body(json.loads(path.read_text(encoding="utf-8"))) if path.is_file() else None

    me = read("me")
    return Snapshot(
        clock=read("clock"),
        schedule=read("schedule"),
        me=me,
        levels=read("levels"),
        dealers=_list(read("dealers"), "personas", "dealers") or None,
        venues=_list(read("venues"), "venues") or None,
        leaderboard=read("leaderboard"),
        duels=_list(read("duels"), "duels") or None,
        events=events,
        team=team or (str(me.get("id")) if me and me.get("id") else None),
        since_tick=since_tick,
        catalog=read("catalog"),
        now=now,
    )


def snapshot_live(
    public: Any, team_client: Any | None, events: Sequence[intel.Event], since_tick: int | None = None
) -> Snapshot:
    """Read-only GETs: public endpoints without a key, `/me` and `/duels` with the team key (reads only).
    Without `since_tick`, the feed checks start at today's first tick, worked out from the clock; if it
    cannot be, at the current tick (they then wait for today's threads instead of reading Friday's)."""
    now = datetime.now(UTC)
    clock = public.clock()
    me = team_client.me() if team_client is not None else None
    duels = _list(team_client.duels(), "duels") if team_client is not None else None
    if since_tick is None:
        first = first_tick_today(clock, now) if clock else None
        since_tick = first if first is not None else int((clock or {}).get("tick") or 0)
    return Snapshot(
        clock=clock,
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
        catalog=public.catalog(),
        now=now,
    )
