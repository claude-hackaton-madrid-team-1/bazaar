"""The rank watch: a rival team that climbs the public leaderboard fast, and why.

Reads only keyless public data the caller already holds: the `/api/leaderboard` snapshot (refreshed every few
minutes, so one snapshot tick is seen once) and the public feed window. When a team other than us climbs
`min_jump` ranks or more against its oldest snapshot inside `window_ticks`, it records one `Learning`
(kind `rival_move`, subject the team) that explains the climb from the board's components (negotiating, market,
level, pages) and the feed between the two snapshots: its dealer deals, its trades with other teams and the
trades other teams made on its venue (RULES.md "Scoring": market-making counts value created between other
teams on your venue). Every string from the board or the feed is quoted data, cleaned and capped.
Behaviour: none. It logs and stores; strategy and Jev may read it.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from bazaar_agent.learn.model import Learning

CONFIDENCE = 0.9  # public facts: the board and the feed say so; the "why" is our reading of them
MIN_MOVE = 0.1  # a component that moved less is not named
LIST_CAP = 5  # items named per list in the text
DETAIL_CAP = 20
LATEST_MAX = 3  # rival moves kept for Jev's state  # items kept per list in `detail`
FIELD_MAX = 24
SAFE_ID = re.compile(r"^[A-Za-z0-9_.:\-]{1,64}$")
COMPONENTS = ("negotiating", "market")


@dataclass(frozen=True)
class Standing:
    tick: int
    team: str
    rank: int
    score: float
    negotiating: float
    market: float
    level: int
    pages: int
    deals: int
    venue: str | None


def _clean(value: Any, cap: int = FIELD_MAX) -> str:
    text = " ".join("".join(ch if ch.isprintable() else " " for ch in str(value or "")).split())
    return text[:cap]


def _num(value: Any) -> float | None:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _id(value: Any) -> str | None:
    return value if isinstance(value, str) and SAFE_ID.match(value) else None


def board_tick(board: Mapping[str, Any]) -> int | None:
    """The snapshot's tick (`snapshot_tick`, else `tick`)."""
    for key in ("snapshot_tick", "tick"):
        tick = _int(board.get(key))
        if tick is not None and tick >= 0:
            return tick
    return None


def standings(board: Mapping[str, Any], tick: int | None = None) -> list[Standing]:
    """Every valid row of a leaderboard payload; a bad row is skipped. `tick`: fallback when the board has none."""
    at = board_tick(board) if isinstance(board, Mapping) else None
    at = at if at is not None else tick
    rows = board.get("teams") if isinstance(board, Mapping) else None
    if at is None or not isinstance(rows, list):
        return []
    out = []
    for row in rows:
        standing = _standing(row, at) if isinstance(row, Mapping) else None
        if standing is not None:
            out.append(standing)
    return out


def _standing(row: Mapping[str, Any], tick: int) -> Standing | None:
    team, rank = _id(row.get("team")), _int(row.get("rank"))
    score = _num(row.get("score"))
    if team is None or rank is None or rank < 1 or score is None:
        return None
    return Standing(
        tick=tick,
        team=team,
        rank=rank,
        score=score,
        negotiating=_num(row.get("negotiating")) or 0.0,
        market=_num(row.get("market")) or 0.0,
        level=_int(row.get("level")) or 0,
        pages=_int(row.get("pages_complete")) or 0,
        deals=_int(row.get("deals")) or 0,
        venue=_id(row.get("venue")),
    )


def _refs(payload: Mapping[str, Any], team: str) -> tuple[list[str], str]:
    """The item refs of a settlement and the team's side: "bought" when items came to it, else "sold"."""
    items = [i for i in payload.get("items") or [] if isinstance(i, Mapping)]
    refs = [_clean(i.get("ref") or i.get("kind") or "?", 16) for i in items]
    side = "bought" if any(i.get("to") == team for i in items) or not items else "sold"
    return refs, side


def _price(payload: Mapping[str, Any]) -> float:
    return _num(payload.get("price")) or 0.0


def _p(value: float) -> str:
    return f"{value:g}"


def _classify(team: str, venue: str | None, events: Sequence[Mapping[str, Any]], lo: int, hi: int) -> dict[str, Any]:
    """The team's dealer deals, team trades, others' trades on its venue and levels, from feed events in (lo, hi]."""
    dealer: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []
    on_venue: list[dict[str, Any]] = []
    levels: list[dict[str, Any]] = []
    evidence: list[int] = []
    for e in events:
        tick, payload = _int(e.get("tick")), e.get("payload")
        if tick is None or not lo < tick <= hi or not isinstance(payload, Mapping):
            continue
        kind, used = e.get("type"), False
        if kind == "settlement":
            used = _settlement(team, venue, payload, dealer, trades, on_venue)
        elif kind == "level.unlocked" and payload.get("team") == team:
            levels.append({"persona": _clean(payload.get("persona")), "level": _int(payload.get("level"))})
            used = True
        if used and _int(e.get("id")) is not None:
            evidence.append(e["id"])
    return {
        "dealer_deals": dealer,
        "team_trades": trades,
        "venue_trades": on_venue,
        "levels": levels,
        "evidence": evidence,
    }


def _settlement(
    team: str,
    venue: str | None,
    payload: Mapping[str, Any],
    dealer: list[dict[str, Any]],
    trades: list[dict[str, Any]],
    on_venue: list[dict[str, Any]],
) -> bool:
    parties = [_clean(p, 64) for p in payload.get("parties") or [] if isinstance(p, str)]
    persona = _id(payload.get("persona"))
    where = _id(payload.get("venue"))
    if team in parties:
        refs, side = _refs(payload, team)
        if persona is not None:
            dealer.append({"persona": persona, "refs": refs, "price": _price(payload), "side": side})
        else:
            other = next((p for p in parties if p != team), "?")
            trades.append({"counterparty": other, "refs": refs, "price": _price(payload), "side": side, "venue": where})
        return True
    if venue is not None and where == venue:
        refs = [_clean(i.get("ref") or "?", 16) for i in payload.get("items") or [] if isinstance(i, Mapping)]
        on_venue.append({"venue": venue, "parties": parties, "refs": refs, "price": _price(payload)})
        return True
    return False


def _capped(parts: list[str]) -> str:
    more = len(parts) - LIST_CAP
    return ", ".join(parts[:LIST_CAP]) + (f", +{more} more" if more > 0 else "")


def _dealer_text(d: Mapping[str, Any]) -> str:
    refs = "+".join(d["refs"]) or "?"
    if d["side"] == "bought":
        return f"{d['persona']} {refs} {_p(d['price'])}"
    return f"sold {refs} {_p(d['price'])} to {d['persona']}"


def _trade_text(t: Mapping[str, Any]) -> str:
    refs = "+".join(t["refs"]) or "?"
    word = "from" if t["side"] == "bought" else "to"
    where = f" on {t['venue']}" if t["venue"] else ""
    return f"{t['side']} {refs} {_p(t['price'])} {word} {t['counterparty']}{where}"


def _component_text(deltas: Mapping[str, float], before: Standing, after: Standing) -> str:
    moved = sorted((k for k in COMPONENTS if abs(deltas[k]) >= MIN_MOVE), key=lambda k: -abs(deltas[k]))
    parts = [f"{k} {deltas[k]:+.1f}" for k in moved]
    if after.level != before.level:
        parts.append(f"level {before.level}→{after.level}")
    if after.pages != before.pages:
        parts.append(f"pages {after.pages - before.pages:+d}")
    return ", ".join(parts) or f"score {deltas['score']:+.1f} (others fell)"


def explain(
    before: Standing, after: Standing, events: Sequence[Mapping[str, Any]], board: Mapping[str, Any]
) -> tuple[str, dict[str, Any]]:
    """Why `after.team` climbed from `before` to `after`: the text and the numbers behind it."""
    team, jump = after.team, before.rank - after.rank
    venue = after.venue or _owned_venue(board, team)
    found = _classify(team, venue, events, before.tick, after.tick)
    deltas = {
        "score": round(after.score - before.score, 2),
        "negotiating": round(after.negotiating - before.negotiating, 2),
        "market": round(after.market - before.market, 2),
    }
    text = (
        f"{team} {jump:+d} ranks ({before.rank}→{after.rank}) in {after.tick - before.tick} ticks: "
        f"{_component_text(deltas, before, after)}"
    )
    if found["dealer_deals"]:
        text += "; dealers: " + _capped([_dealer_text(d) for d in found["dealer_deals"]])
    if found["team_trades"]:
        text += "; teams: " + _capped([_trade_text(t) for t in found["team_trades"]])
    if found["venue_trades"]:
        n = len(found["venue_trades"])
        sample = _capped([f"{'+'.join(v['refs']) or '?'} {_p(v['price'])}" for v in found["venue_trades"]])
        text += f"; venue {venue}: {n} trade{'s' if n != 1 else ''} between other teams ({sample})"
    if found["levels"]:
        text += "; unlocked: " + _capped([f"{lv['persona']} L{lv['level']}" for lv in found["levels"]])
    detail: dict[str, Any] = {
        "event_id": f"{team}:{before.tick}-{after.tick}",
        "rank_before": before.rank,
        "rank_after": after.rank,
        "jump": jump,
        "ticks": after.tick - before.tick,
        "deltas": deltas,
        "level_before": before.level,
        "level_after": after.level,
        "pages_gained": after.pages - before.pages,
        "deals_gained": after.deals - before.deals,
        "venue": venue,
        "evidence": found["evidence"],
        **{k: found[k][:DETAIL_CAP] for k in ("dealer_deals", "team_trades", "venue_trades", "levels")},
    }
    return text, detail


def _owned_venue(board: Mapping[str, Any], team: str) -> str | None:
    for v in board.get("venues") or []:
        if isinstance(v, Mapping) and v.get("owner") == team:
            return _id(v.get("venue"))
    return None


def learning_of(text: str, detail: Mapping[str, Any], after: Standing) -> Learning:
    return Learning(
        subject_kind="team",
        subject=after.team,
        kind="rival_move",
        tick=after.tick,
        confidence=CONFIDENCE,
        evidence=tuple(detail.get("evidence") or ()),
        text=text,
        detail={k: v for k, v in detail.items() if k != "evidence"},
    )


class RankWatch:
    """Call `observe` once per tick with the latest board and feed window: never raises, never sends."""

    def __init__(
        self,
        record: Callable[[list[Learning]], object],
        log: Callable[[str], None],
        us: str | None = None,
        window_ticks: int = 20,
        min_jump: int = 3,
        history_ticks: int = 60,
        save: Callable[[list[Standing]], object] | None = None,
    ) -> None:
        self.record, self.log, self.us = record, log, us
        self.window, self.min_jump, self.history = window_ticks, min_jump, max(history_ticks, window_ticks)
        self.snapshots: dict[str, list[Standing]] = {}
        self.seen_ticks: set[int] = set()
        self._failed: set[str] = set()
        self.save = save  # each new board, e.g. into Postgres `leaderboard_snapshots` (`leaderboard_store`)
        self.latest: list[Learning] = []  # the newest rival moves, newest first (for Jev's state)

    def observe(self, board: Mapping[str, Any], events: Sequence[Mapping[str, Any]], tick: int) -> list[Learning]:
        try:
            return self._run(board, events, tick)
        except Exception as e:  # noqa: BLE001 — logging only: the watch never breaks a tick
            self._once(f"tick {tick} rank watch: skipped ({type(e).__name__})")
            return []

    def _run(self, board: Mapping[str, Any], events: Sequence[Mapping[str, Any]], tick: int) -> list[Learning]:
        rows = standings(board, tick)
        if not rows or rows[0].tick in self.seen_ticks:
            return []
        now = rows[0].tick
        climbs = [(old, row) for row in rows if (old := self._climb(row)) is not None]
        learnings, lines = [], []
        for old, row in climbs:
            text, detail = explain(old, row, events, board)
            learnings.append(learning_of(text, detail, row))
            lines.append(f"tick {tick} rival move: {learnings[-1].text}")
        if learnings:
            self.record(learnings)  # a store that raises: nothing is kept, the same board is tried again
        for _, row in climbs:
            self.snapshots[row.team] = []  # said once: the next climb is measured from this board
        for line in lines:
            self.log(line)
        self._keep(rows, now)
        self.latest = [*reversed(learnings), *self.latest][:LATEST_MAX]
        if self.save is not None:
            self.save(rows)
        return learnings

    def seed(self, rows: Sequence[Standing]) -> int:
        """Reload stored boards (oldest first) after a restart: they become the history, never a learning."""
        by_tick: dict[int, list[Standing]] = {}
        for row in rows:
            by_tick.setdefault(row.tick, []).append(row)
        for tick in sorted(by_tick):
            self._keep(by_tick[tick], tick)
        return len(by_tick)

    def _climb(self, row: Standing) -> Standing | None:
        """The team's oldest snapshot inside the window when it climbed `min_jump` ranks since, else None."""
        if row.team == self.us:
            return None
        inside = [s for s in self.snapshots.get(row.team, []) if row.tick - self.window <= s.tick < row.tick]
        old = min(inside, key=lambda s: s.tick, default=None)
        return old if old is not None and old.rank - row.rank >= self.min_jump else None

    def _keep(self, rows: Sequence[Standing], now: int) -> None:
        cutoff = now - self.history
        self.seen_ticks = {t for t in self.seen_ticks if t >= cutoff} | {now}
        for row in rows:
            history = [s for s in self.snapshots.get(row.team, []) if s.tick >= cutoff]
            self.snapshots[row.team] = [*history, row]
        for team in [t for t, h in self.snapshots.items() if not h or h[-1].tick < cutoff]:
            del self.snapshots[team]

    def _once(self, line: str) -> None:
        key = line.split(": ", 1)[-1]
        if key not in self._failed:
            self._failed.add(key)
            self.log(line)
