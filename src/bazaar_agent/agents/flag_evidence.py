"""Flag precision from the public feed (S1 part B): the evidence read before `allow_flags` goes on.

A wrong `POST /api/flags` costs points, so the flag rule (agents/inspector.py) is replayed over every
structured offer a dealer made in the captured feed, with its thread's topic and its words. The feed has all
of it: `thread.opened` carries the topic and `thread.message` carries the dealer's words and structured offer.
Pure functions, no network.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from bazaar_agent.agents.inspector import CardIndex, Inspection, inspect_offer

Event = Mapping[str, Any]


@dataclass(frozen=True)
class FeedOffer:
    """One structured offer a dealer sent in a thread, as the public feed shows it."""

    dealer: str
    message_id: int | None
    thread: int
    tick: int | None
    topic: Mapping[str, Any] | None  # None: the thread opened before our capture
    offer: Mapping[str, Any]
    text: str | None


def dealer_offers(events: Iterable[Event]) -> list[FeedOffer]:
    """Every distinct structured offer a dealer (the thread's `with`) made, oldest first."""
    topics: dict[int, Mapping[str, Any]] = {}
    out: dict[tuple[int, int | None], FeedOffer] = {}
    for e in events:
        payload = e.get("payload")
        p: Mapping[str, Any] = payload if isinstance(payload, dict) else {}
        thread = p.get("thread")
        if not isinstance(thread, int) or p.get("kind") != "persona":
            continue
        if e.get("type") == "thread.opened" and isinstance(p.get("topic"), dict) and p["topic"]:
            topics[thread] = p["topic"]  # an empty topic ({}) says nothing to judge against: unknown
        offer, sender = p.get("offer"), p.get("sender")
        if e.get("type") != "thread.message" or not isinstance(offer, dict) or not isinstance(sender, str):
            continue
        if not sender or sender != p.get("with"):  # a dealer's own message (no sender: not a dealer's)
            continue
        message = p.get("message")
        mid = message if isinstance(message, int) else None
        text = p.get("text") if isinstance(p.get("text"), str) else None
        tick = e.get("tick") if isinstance(e.get("tick"), int) else None
        oid = offer["id"] if isinstance(offer.get("id"), int) else -1
        out.setdefault((oid, mid), FeedOffer(str(sender), mid, thread, tick, topics.get(thread), offer, text))
    return list(out.values())


@dataclass(frozen=True)
class Evidence:
    """The flag rule over the feed: verdict counts, per dealer, and every offer it would flag."""

    offers: int
    known_topic: int
    counts: Mapping[str, int]
    by_dealer: Mapping[str, Mapping[str, int]]
    would_flag: tuple[Inspection, ...]  # certain tricksters, trusted dealers included (they are never sent)
    trusted: frozenset[str]
    flag_dealers: frozenset[str] = frozenset()  # GUARDRAILS.md opt-in: the only dealers a flag may go to
    unreadable: int = 0  # offers whose shape the inspector could not read (skipped)

    @property
    def flags_from_untrusted(self) -> int:
        return sum(1 for i in self.would_flag if i.dealer not in self.trusted)

    @property
    def dealers(self) -> tuple[str, ...]:
        return tuple(sorted(self.by_dealer))

    def as_state(self) -> dict[str, Any]:
        """What Jev reads (`questions/flags.json`): counts only, no counterparty text."""
        untrusted = sorted(d for d in self.by_dealer if d not in self.trusted)
        opted = sum(1 for i in self.would_flag if i.dealer in self.flag_dealers and i.dealer not in self.trusted)
        return {
            "flag_dealers": sorted(self.flag_dealers),
            "would_flag_on_flag_dealers": opted,
            "unreadable_offers": self.unreadable,
            "dealer_offers_inspected": self.offers,
            "offers_with_known_topic": self.known_topic,
            "clean": self.counts.get("clean", 0),
            "blocked": self.counts.get("block", 0),
            "would_flag_all_dealers": len(self.would_flag),
            "would_flag_untrusted_dealers": self.flags_from_untrusted,
            "would_flag_trusted_dealers": len(self.would_flag) - self.flags_from_untrusted,
            "untrusted_dealers_seen": untrusted,
            "untrusted_dealer_offers": sum(sum(self.by_dealer[d].values()) for d in untrusted),
            "trusted_dealers": sorted(self.trusted),
        }


def precision(
    offers: Iterable[FeedOffer],
    cards: CardIndex,
    trusted: frozenset[str],
    flag_dealers: frozenset[str] = frozenset(),
) -> Evidence:
    """Replay the inspector over feed offers. An offer whose thread opened before the capture is skipped
    (no topic: the inspector would only block it, and it says nothing about the flag rule), and so is an
    offer whose shape it cannot read (counted)."""
    counts = {"clean": 0, "block": 0, "flag": 0}
    by_dealer: dict[str, dict[str, int]] = {}
    flagged: list[Inspection] = []
    total = known = unreadable = 0
    for o in offers:
        total += 1
        if o.topic is None:
            continue
        try:
            i = inspect_offer(o.offer, o.topic, o.text, cards, dealer=o.dealer, message_id=o.message_id)
        except (AttributeError, TypeError, ValueError, KeyError):  # a list where an object should be
            unreadable += 1
            continue
        known += 1
        counts[i.verdict] += 1
        row = by_dealer.setdefault(o.dealer, {"clean": 0, "block": 0, "flag": 0})
        row[i.verdict] += 1
        if i.verdict == "flag":
            flagged.append(i)
    return Evidence(total, known, counts, by_dealer, tuple(flagged), trusted, flag_dealers, unreadable)
