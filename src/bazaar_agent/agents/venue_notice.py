"""The notice on our venue that names cards (MM2): the page cards the most other teams miss, from the team matrix.

Market points count the gains other teams realise on our venue (RULES.md "Your own market"), and v19 had none: our
generic notice named nothing a trader could act on, while the venues that fill name cards. So the keeper's notice
names the 3-4 page cards that the most other teams miss for a page close to complete (`team_matrix.Cell.
missing_for_page`, the matrix the taker stores and the maker reads), with our fee and what the broker does.

Only public facts go out: card ids that match the catalog's shape, our venue's id, name and fee, and the house
market's live fee. Never a team (not even a rival), a value, a multiplier or our cash; only cards we hold a copy of
(never one we miss), and a card that only podium rivals miss (`Summary.rival`) is not advertised. No matrix, a
stale one (`LatestMatrix.current`) or no card that fits: None, and the keeper sends its generic notice instead.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Collection, Sequence

from bazaar_agent.agents.market import Venue, _fee
from bazaar_agent.team_matrix import TeamMatrix
from bazaar_agent.venue import Announcement, VenueSpec

NOTICE_MAX_CHARS = 240  # the feed clips a notice here (venue.ANNOUNCE_MAX_CHARS is the server's 280)
WANTED_MAX = 4  # cards named in one notice
CARD_REF = re.compile(r"[A-Z]{2,4}-[0-9]{2}")  # LAV-03, RET-10: anything else is never echoed
EXAMPLE_PRICE = 20  # "dearer" is judged on one sale at this price, as the generic notice's example


def fee_text(fee_bps: int, fee_per_card: int) -> str:
    return f"{fee_bps / 100:g} %" + (f" + {fee_per_card} P/card" if fee_per_card else "")


def wanted_cards(
    matrix: TeamMatrix | None, us: str, limit: int = WANTED_MAX, only: Collection[str] | None = None
) -> list[str]:
    """The page cards the most other teams miss for a page close to complete, most teams first, then by id.
    Never a podium rival's alone (a card only rivals miss is left out), and with `only` (the cards we hold a copy
    of) never one we miss ourselves: a public "wanted" would raise its asks to us and send its sellers to a venue
    our key cannot trade on."""
    if matrix is None:
        return []
    teams: dict[str, set[str]] = defaultdict(set)
    for c in matrix.cells:
        summary = matrix.teams.get(c.team)
        rival = summary is not None and summary.rival is not None
        ours = only is None or c.card in only
        if c.missing_for_page and c.team not in (us, matrix.us) and not rival and ours and CARD_REF.fullmatch(c.card):
            teams[c.card].add(c.team)
    return sorted(teams, key=lambda card: (-len(teams[card]), card))[:limit]


def clean(text: str) -> str:
    """Printable ASCII only, whitespace runs collapsed: nothing in it can hide a character or a line."""
    return " ".join("".join(ch if ch.isascii() and ch.isprintable() else " " for ch in text).split())


def wanted_notice(plan: VenueSpec, venue: str, cards: Sequence[str], house: Venue | None = None) -> Announcement | None:
    """Our venue, its fee and the wanted cards in at most NOTICE_MAX_CHARS (fewer cards when it is long), then the
    house market's fee when it is dearer and still fits. None without a card that fits."""
    named = [c for c in cards if CARD_REF.fullmatch(c)][:WANTED_MAX]
    head = f"{plan.name} ({venue}, {fee_text(plan.fee_bps, plan.fee_per_card)} fee, crossing bids and asks matched"
    for n in range(len(named), 0, -1):
        text = clean(f"{head} every tick): wanted {', '.join(named[:n])}; sellers and buyers welcome.")
        if len(text) > NOTICE_MAX_CHARS:
            continue
        if house is not None and _fee(house.fee_bps, house.fee_per_card, EXAMPLE_PRICE, 1) > _fee(
            plan.fee_bps, plan.fee_per_card, EXAMPLE_PRICE, 1
        ):
            where = "El Rastro" if house.id == "rastro" else "the house market"
            more = clean(f"{text} On {where} the side that accepts pays {fee_text(house.fee_bps, house.fee_per_card)}.")
            text = more if len(more) <= NOTICE_MAX_CHARS else text
        return Announcement(text=text)
    return None
