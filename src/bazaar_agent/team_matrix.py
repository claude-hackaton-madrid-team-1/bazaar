"""The team matrix: every team × card we can place, and what each team wants, holds spare and is worth to us.

Built in the taker's news-sentinel window (every 10 ticks, after the sends) from what the taker already has: the
supply map (`supply.py`: the feed, the card scan and `/api/me`), the public leaderboard the rank watch keeps
(`rank_watch.py`), the chasers per set (`intel.team_flows`) and the tape. No request of its own.

Per team × card: the copies we place with the team (`holds`), its likely duplicates (`spare`), and the page cards
it is missing on a page close to complete (`missing_for_page`, at most `MISSING_MAX` missing). Per team: rank,
trend, top set, venue, last trades, whether it is a podium rival (`buyers.is_rival`), what it wants and what it
holds spare that we miss. Holdings are lower bounds (copies we cannot place stay unplaced), so each cell carries a
confidence. The negotiators read compact views of it (`row`, `card`): never a price, never an instruction.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from bazaar_agent.buyers import BuyerConfig, is_rival
from bazaar_agent.rank_watch import SAFE_ID, Standing

MISSING_MAX = 2  # a page with at most this many page cards missing is "close to complete"...
NEAR_SHARE = 0.7  # ...and at least this share of its page cards held (a 1/3 page is not close)
TOP = 5  # entries per list in a negotiator's view
TRADES = 3  # last trades kept per team
HOLD_FLOOR = 0.5  # confidence of a holding when we can place none of the card's other copies
MISSING_SHARE = 0.4  # a "missing" cell is an inference: at most this confidence


@dataclass(frozen=True)
class Cell:
    """One team × card: `holds` copies placed with the team (a lower bound), `spare` = holds - 1, or a page card
    the team misses on a page close to complete (`missing_for_page`, with that page's `page_have`/`page_of`)."""

    team: str
    card: str
    holds: int
    spare: int
    missing_for_page: bool
    page_have: int | None
    page_of: int | None
    confidence: float


@dataclass(frozen=True)
class Summary:
    """One team: its standing and what it means for us."""

    team: str
    rank: int | None
    score: float | None
    trend: int | None  # ranks climbed over the rank watch's window (+ up, - down); None: one board seen
    top_set: str | None  # the set it chases (`intel.team_flows`)
    venue: str | None
    rival: str | None  # why it is a podium rival (`buyers.is_rival`), else None
    wants: str  # the page cards it misses on a page close to complete, and the set it chases
    has_for_us: str  # its spare copies of page cards we miss
    last_trades: str  # its newest settlements, newest first


@dataclass(frozen=True)
class TeamMatrix:
    tick: int
    us: str
    cells: tuple[Cell, ...]
    teams: dict[str, Summary]
    _by_team: dict[str, list[Cell]] = field(default_factory=dict, compare=False, repr=False)
    _by_card: dict[str, list[Cell]] = field(default_factory=dict, compare=False, repr=False)

    def __post_init__(self) -> None:
        for c in self.cells:
            self._by_team.setdefault(c.team, []).append(c)
            self._by_card.setdefault(c.card, []).append(c)

    def row(self, team: str | None) -> dict[str, Any] | None:
        """The counterparty's compact row for a decider's state (None: a team we know nothing about)."""
        s = self.teams.get(team or "")
        if s is None:
            return None
        cells = self._by_team.get(s.team, [])
        spare = sorted((c for c in cells if c.spare > 0), key=lambda c: (-c.spare, c.card))[:TOP]
        missing = sorted((c for c in cells if c.missing_for_page), key=_closeness)[:TOP]
        return {
            "team": s.team,
            "rank": s.rank,
            "trend": s.trend,
            "rival": s.rival is not None,
            "top_set": s.top_set,
            "holds_spare": [f"{c.holds}x {c.card}" for c in spare],
            "misses_for_page": [f"{c.card} ({c.page_have}/{c.page_of})" for c in missing],
            "has_for_us": s.has_for_us,
            "summary": self.text(s.team),
        }

    def card(self, ref: str, top: int = TOP) -> dict[str, list[dict[str, Any]]]:
        """For one card decision: the teams that hold it spare and the teams that miss it for a page, top `top`."""
        cells = self._by_card.get(ref, [])
        spare = sorted((c for c in cells if c.spare > 0), key=lambda c: (-c.spare, self._rank(c.team), c.team))
        missing = sorted((c for c in cells if c.missing_for_page), key=lambda c: (*_closeness(c), self._rank(c.team)))
        return {
            "spare": [self._who(c, holds=c.holds) for c in spare[:top]],
            "missing": [self._who(c, page=f"{c.page_have}/{c.page_of}") for c in missing[:top]],
        }

    def text(self, team: str) -> str:
        """'t14: holds 2x LAT-04; misses SAL-03 for a 9/10 page; rank 12 (+3); not a rival'."""
        s = self.teams.get(team)
        if s is None:
            return f"{team}: unknown"
        cells = self._by_team.get(team, [])
        spare = sorted((c for c in cells if c.spare > 0), key=lambda c: (-c.spare, c.card))[:3]
        missing = sorted((c for c in cells if c.missing_for_page), key=_closeness)[:3]
        parts = []
        if spare:
            parts.append("holds " + ", ".join(f"{c.holds}x {c.card}" for c in spare))
        if missing:
            parts.append("misses " + ", ".join(f"{c.card} for a {c.page_have}/{c.page_of} page" for c in missing))
        trend = f" ({s.trend:+d})" if s.trend else ""
        parts.append(f"rank {s.rank}{trend}" if s.rank is not None else "rank unknown")
        parts.append(f"rival ({s.rival})" if s.rival else "not a rival")
        return f"{team}: " + "; ".join(parts)

    def _rank(self, team: str) -> int:
        s = self.teams.get(team)
        return s.rank if s is not None and s.rank is not None else 99

    def _who(self, c: Cell, **extra: Any) -> dict[str, Any]:
        s = self.teams.get(c.team)
        return {"team": c.team, **extra, "rank": s.rank if s else None, "rival": bool(s and s.rival)}


def _closeness(c: Cell) -> tuple[int, str]:
    """Pages nearest completion first."""
    return ((c.page_of or 0) - (c.page_have or 0), c.card)


# ---------------------------------------------------------------- building it


def page_cards(catalog: Mapping[str, Any], released: Iterable[str]) -> dict[str, list[str]]:
    """Set code -> its page cards, for the released sets (our album pages)."""
    sets = set(released)
    return {
        str(s.get("id")): [str(c.get("id")) for c in s.get("cards") or [] if c.get("page")]
        for s in catalog.get("sets") or []
        if str(s.get("id")) in sets
    }


def _placed_share(card: Any) -> float:
    """Share of the card's other copies we can place (1.0: every copy outside ours is placed)."""
    others, unplaced = int(getattr(card, "others", 0)), int(getattr(card, "unplaced", 0))
    return others / (others + unplaced) if others + unplaced > 0 else 1.0


def holdings(supply: Any) -> tuple[dict[str, Counter[str]], dict[str, float]]:
    """team -> card -> copies we place with it, and card -> placed share (from `supply.SupplyMap.cards`)."""
    held: dict[str, Counter[str]] = defaultdict(Counter)
    share: dict[str, float] = {}
    for ref, card in (getattr(supply, "cards", None) or {}).items():
        share[ref] = _placed_share(card)
        for team, n in card.holders:
            if SAFE_ID.fullmatch(str(team)) and n > 0:
                held[str(team)][ref] += int(n)
    return held, share


def cells_of(
    held: Mapping[str, Counter[str]], share: Mapping[str, float], pages: Mapping[str, Sequence[str]]
) -> list[Cell]:
    """Every holding, and the page cards each team misses on a page close to complete."""
    page_of = {ref: code for code, refs in pages.items() for ref in refs}
    out: list[Cell] = []
    for team, cards in held.items():
        have = {code: sum(1 for ref in refs if cards.get(ref, 0) > 0) for code, refs in pages.items()}
        for ref, n in sorted(cards.items()):
            code = page_of.get(ref)
            conf = round(HOLD_FLOOR + (1 - HOLD_FLOOR) * share.get(ref, 1.0), 2)
            out.append(Cell(team, ref, n, max(0, n - 1), False, have.get(code or ""), _of(pages, code), conf))
        for code, refs in pages.items():
            missing = [ref for ref in refs if cards.get(ref, 0) <= 0]
            if not missing or len(missing) > MISSING_MAX or have[code] < NEAR_SHARE * len(refs):
                continue
            conf = round(MISSING_SHARE * sum(share.get(r, 1.0) for r in refs) / max(1, len(refs)), 2)
            out.extend(Cell(team, ref, 0, 0, True, have[code], len(refs), conf) for ref in missing)
    return out


def _of(pages: Mapping[str, Sequence[str]], code: str | None) -> int | None:
    return len(pages[code]) if code is not None and code in pages else None


def last_trades(events: Iterable[Mapping[str, Any]], limit: int = TRADES) -> dict[str, list[str]]:
    """team -> its newest settlements as short lines, newest first ("t412 bought LAT-04 20 from t05")."""
    out: dict[str, list[str]] = defaultdict(list)
    for e in sorted(events, key=lambda e: -int(e.get("id") or 0)):
        p = e.get("payload")
        if e.get("type") != "settlement" or not isinstance(p, dict):
            continue
        price = p.get("price")
        for item in p.get("items") or []:
            if not isinstance(item, dict) or item.get("kind", "card") != "card":
                continue
            ref, frm, to = str(item.get("ref") or "")[:16], str(item.get("frm") or "")[:24], str(item.get("to") or "")
            for team, verb, other in ((to, "bought", f"from {frm}"), (frm, "sold", f"to {to[:24]}")):
                if SAFE_ID.fullmatch(team) and len(out[team]) < limit:
                    out[team].append(f"t{e.get('tick')} {verb} {ref} {price} {other}")
    return out


def summaries(
    teams: Iterable[str],
    cells: Sequence[Cell],
    standings: Mapping[str, Sequence[Standing]],
    us: str,
    our_held: Mapping[str, int],
    pages: Mapping[str, Sequence[str]],
    chasers: Mapping[str, Sequence[str]],
    trades: Mapping[str, Sequence[str]],
    cfg: BuyerConfig,
) -> dict[str, Summary]:
    ours = standings.get(us) or ()
    our_rank = ours[-1].rank if ours else None
    top_set = {str(t): code for code, ts in chasers.items() for t in ts}
    we_miss = {ref for refs in pages.values() for ref in refs if our_held.get(ref, 0) <= 0}
    by_team: dict[str, list[Cell]] = defaultdict(list)
    for c in cells:
        by_team[c.team].append(c)
    out = {}
    for team in sorted(set(teams)):
        boards = standings.get(team) or ()
        now = boards[-1] if boards else None
        mine = by_team.get(team, [])
        missing = sorted((c for c in mine if c.missing_for_page), key=_closeness)[:TOP]
        wants = [f"{c.card} for a {c.page_have}/{c.page_of} page" for c in missing]
        if top_set.get(team):
            wants.append(f"chases {top_set[team]}")
        spare = sorted((c for c in mine if c.spare > 0 and c.card in we_miss), key=lambda c: (-c.spare, c.card))
        out[team] = Summary(
            team=team,
            rank=now.rank if now else None,
            score=now.score if now else None,
            trend=(boards[0].rank - now.rank) if now and len(boards) > 1 else None,
            top_set=top_set.get(team),
            venue=now.venue if now else None,
            rival=is_rival(now.rank if now else None, our_rank, cfg),
            wants="; ".join(wants),
            has_for_us=", ".join(f"{c.holds}x {c.card}" for c in spare[:TOP]),
            last_trades="; ".join(trades.get(team, ())),
        )
    return out


def build_matrix(
    tick: int,
    us: str,
    catalog: Mapping[str, Any],
    supply: Any,
    our_held: Mapping[str, int],
    released: Sequence[str],
    chasers: Mapping[str, Sequence[str]],
    standings: Mapping[str, Sequence[Standing]],
    events: Sequence[Mapping[str, Any]],
    cfg: BuyerConfig | None = None,
) -> TeamMatrix:
    """The matrix from what the taker already holds: `supply` = `strategy.Market.supply`, `our_held` / `released` /
    `chasers` = the same Market's, `standings` = the rank watch's snapshots per team (oldest first)."""
    pages = page_cards(catalog, released)
    held, share = holdings(supply)
    held.pop(us, None)
    cells = cells_of(held, share, pages)
    teams = {*held, *(t for t in standings if SAFE_ID.fullmatch(t))} - {us}
    found = summaries(teams, cells, standings, us, our_held, pages, chasers, last_trades(events), cfg or BuyerConfig())
    return TeamMatrix(tick, us, tuple(cells), found)
