"""Venue notices for the maker (N12): announced fee changes and venues on their way out, from the feed.

`GET /api/venues` shows a venue's fee NOW. A listing lives `offer_ttl_ticks` (40), and a venue owner may
announce a higher fee from a later tick ("v03 will charge 1% from T147"): the buyer of our ask would pay
that fee for most of the listing's life. So the maker scores each venue at the worse of its fee now and
any fee announced to take effect within the listing's life, and leaves out a venue that is closing.
In memory, from the events the maker already reads: no database and no game call. Any error leaves the
venues as `/api/venues` gave them.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import replace
from typing import Any

from bazaar_agent.agents.market import Venue
from bazaar_agent.learn.model import Learning
from bazaar_agent.learn.reader import FeedReader

GONE_STATES = frozenset({"closing", "closed", "suspended"})
BACK_STATES = frozenset({"reopened", "open"})


def adjust(venues: Iterable[Venue], notices: Iterable[Learning], tick: int, horizon: int) -> list[Venue]:
    """The venues as a listing posted at `tick` and living `horizon` ticks will meet them."""
    pool = sorted(notices, key=lambda lr: (lr.tick, lr.evidence))
    upcoming: dict[str, tuple[int, int]] = {}
    state: dict[str, str] = {}
    for lr in pool:
        venue = str(lr.detail.get("venue") or lr.subject)
        effective = lr.detail.get("effective_tick")
        if lr.kind == "fee_change" and isinstance(effective, int) and tick < effective <= tick + horizon:
            bps, per_card = lr.detail.get("fee_bps"), lr.detail.get("fee_per_card")
            if isinstance(bps, int) and isinstance(per_card, int):
                upcoming[venue] = (bps, per_card)
        elif lr.kind == "announcement" and lr.detail.get("state") in GONE_STATES | BACK_STATES:
            state[venue] = str(lr.detail["state"])
    out = []
    for v in venues:
        if state.get(v.id) in GONE_STATES:
            continue
        bps, per_card = upcoming.get(v.id, (v.fee_bps, v.fee_per_card))
        out.append(replace(v, fee_bps=max(v.fee_bps, bps), fee_per_card=max(v.fee_per_card, per_card)))
    return out


class VenueNotices:
    """The maker's reader: keeps the venue learnings it has seen; `adjust()` never raises."""

    def __init__(self, log: Callable[[str], None] = lambda message: None) -> None:
        self.log = log
        self.reader: FeedReader | None = None
        self.notices: list[Learning] = []
        self._failed = False

    def update(self, events: Iterable[dict[str, Any]], us: str) -> None:
        try:
            if self.reader is None or self.reader.us != us:
                self.reader, self.notices = FeedReader(us), []
            self.notices += [lr for lr in self.reader.read(events) if lr.subject_kind == "venue"]
        except Exception as e:
            self._fail(e)

    def adjust(self, venues: list[Venue], tick: int, horizon: int) -> list[Venue]:
        try:
            return adjust(venues, self.notices, tick, horizon)
        except Exception as e:
            self._fail(e)
            return venues

    def _fail(self, error: Exception) -> None:
        if not self._failed:
            self.log(f"learnings: venue notices failed ({type(error).__name__}); venues as /api/venues shows them")
        self._failed = True
