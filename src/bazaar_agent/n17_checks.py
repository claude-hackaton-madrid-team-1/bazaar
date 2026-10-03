"""N17-10: the team-thread spec's open questions (Q1-Q6), answered from what our agents already store.

Read-only and offline: the public feed (`feed_events`), our refused sends (`executions`) and the offers our
agents saw (`offers`), with no game request. Each answer is `yes`, `no` or `unknown` with the evidence that
decided it; `go_no_go` turns them into the call for `team_threads_enabled` at the 23:00 window.
Words persuade, structure binds: only structured fields are read, never a message's text.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from bazaar_agent.intel import TEAM_ID

Event = dict[str, Any]


@dataclass(frozen=True)
class Answer:
    q: str
    question: str
    verdict: str  # "yes" | "no" | "unknown"
    evidence: str


def _payload(e: Event) -> dict[str, Any]:
    p = e.get("payload")
    return p if isinstance(p, dict) else {}


def _team(x: Any) -> bool:
    return isinstance(x, str) and bool(TEAM_ID.match(x))


def team_threads(events: Iterable[Event]) -> dict[int, dict[str, Any]]:
    """Every team-to-team thread the feed shows opened: thread id -> its `thread.opened` payload."""
    out: dict[int, dict[str, Any]] = {}
    for e in events:
        p = _payload(e)
        team = p.get("kind") == "team" or (_team(p.get("team")) and _team(p.get("with")))
        if e.get("type") == "thread.opened" and isinstance(p.get("thread"), int) and team:
            out[int(p["thread"])] = p
    return out


def _thread_offers(events: Iterable[Event], ids: set[int]) -> list[dict[str, Any]]:
    offers = []
    for e in events:
        p = _payload(e)
        if e.get("type") == "thread.message" and p.get("thread") in ids and isinstance(p.get("offer"), dict):
            offers.append(p["offer"])
    return offers


def q1_inbound(threads: dict[int, dict[str, Any]], us: str, refusals: Sequence[dict[str, Any]]) -> Answer:
    inbound = [t for t in threads.values() if t.get("with") == us]
    too_many = [r for r in refusals if r.get("error_code") == "too_many_threads"]
    question = "Does an inbound team thread count toward our 6 conversations?"
    if not inbound:
        return Answer("Q1", question, "unknown", "no team has opened a thread with us in the stored feed")
    if too_many:
        return Answer(
            "Q1",
            question,
            "unknown",
            f"{len(inbound)} inbound thread(s), {len(too_many)} too_many_threads "
            "refusal(s): read the ticks together before turning the desk on",
        )
    return Answer("Q1", question, "unknown", f"{len(inbound)} inbound thread(s), no too_many_threads refusal yet")


def q2_listings(refusals: Sequence[dict[str, Any]]) -> Answer:
    says = [
        r
        for r in refusals
        if r.get("sdk_method") == "say" and r.get("error_code") in ("wait_for_tick", "too_many_offers")
    ]
    question = "Does a thread offer count toward 12 listings per tick and 30 open offers?"
    if says:
        return Answer("Q2", question, "yes", f"{len(says)} thread message(s) of ours refused on the listing/offer caps")
    return Answer(
        "Q2", question, "unknown", "no thread offer of ours was refused on those caps; the desk counts them anyway"
    )


def q3_retire(offers: Sequence[dict[str, Any]], us: str) -> Answer:
    """From our dealer threads (the same thread mechanism): does a new offer of ours leave the old one open?"""
    by_thread: dict[int, list[dict[str, Any]]] = {}
    for o in offers:
        if o.get("maker") == us and isinstance(o.get("thread_id"), int):
            by_thread.setdefault(int(o["thread_id"]), []).append(o)
    stacked = [t for t, os in by_thread.items() if sum(1 for o in os if o.get("status") == "open") > 1]
    replaced = Counter(str(o.get("status")) for os in by_thread.values() for o in sorted(os, key=_id)[:-1])
    question = "Does a new thread offer retire our previous one in the same thread?"
    if not by_thread:
        return Answer("Q3", question, "unknown", "no thread offer of ours stored")
    if stacked:
        return Answer("Q3", question, "no", f"{len(stacked)} thread(s) held two open offers of ours at once")
    return Answer(
        "Q3",
        question,
        "yes",
        f"older offers of ours in {len(by_thread)} thread(s) read {dict(replaced)} "
        "(dealer threads; the desk cancels first either way)",
    )


def _id(o: dict[str, Any]) -> int:
    return int(o["id"]) if isinstance(o.get("id"), int) else -1


def q4_public(threads: dict[int, dict[str, Any]]) -> Answer:
    question = "Is a team thread (and its topic) public in the feed?"
    if not threads:
        return Answer("Q4", question, "unknown", "no team thread in the stored feed (none opened, or not public)")
    with_topic = sum(1 for t in threads.values() if t.get("topic"))
    return Answer("Q4", question, "yes", f"{len(threads)} team thread(s) in the public feed, {with_topic} with a topic")


def q5_expiry(thread_offers: Sequence[dict[str, Any]]) -> Answer:
    lives = Counter(
        int(o["expires_tick"]) - int(o["created_tick"])
        for o in thread_offers
        if isinstance(o.get("expires_tick"), int) and isinstance(o.get("created_tick"), int)
    )
    question = "How long does an offer in a team message stand (expires_in_ticks)?"
    if not lives:
        return Answer("Q5", question, "unknown", "no team-thread offer with its ticks in the stored feed")
    return Answer("Q5", question, "yes", f"lives in ticks: {dict(sorted(lives.items()))}")


def _mixed(o: dict[str, Any]) -> bool:
    give, want = o.get("give") or {}, o.get("want") or {}
    return (
        bool(give.get("assets"))
        and bool(give.get("cash"))
        or (bool(want.get("cards") or want.get("types") or want.get("assets")) and bool(want.get("cash")))
    )


def q6_mixed(events: Iterable[Event], thread_offers: Sequence[dict[str, Any]]) -> Answer:
    listed = [_payload(e).get("offer") for e in events if e.get("type") == "offer.listed"]
    mixed = [o for o in [*listed, *thread_offers] if isinstance(o, dict) and _mixed(o)]
    question = "Are mixed sides (cards and cash on one side) accepted?"
    if mixed:
        return Answer("Q6", question, "yes", f"{len(mixed)} offer(s) with cards and cash on one side were posted")
    return Answer("Q6", question, "unknown", "no such offer in the stored feed")


def answers(
    events: Sequence[Event], us: str, refusals: Sequence[dict[str, Any]], offers: Sequence[dict[str, Any]]
) -> list[Answer]:
    threads = team_threads(events)
    thread_offers = _thread_offers(events, set(threads))
    return [
        q1_inbound(threads, us, refusals),
        q2_listings(refusals),
        q3_retire(offers, us),
        q4_public(threads),
        q5_expiry(thread_offers),
        q6_mixed(events, thread_offers),
    ]


def go_no_go(found: Sequence[Answer]) -> tuple[str, str]:
    """GO only when nothing found contradicts the desk's assumptions; unknowns are carried by its fail-safe
    defaults (it cancels before replacing, counts thread offers as listings, closes idle inbound threads)."""
    by = {a.q: a for a in found}
    if by["Q3"].verdict == "no":
        return "NO-GO", "a new thread offer leaves the old one open: re-check the desk's cancel-first path live"
    unknown = [a.q for a in found if a.verdict == "unknown"]
    note = f"unknown: {', '.join(unknown)} (covered by the desk's fail-safe defaults)" if unknown else "all answered"
    return "GO (supervised)", f"turn it on with BAZAAR_TEAM_THREADS ready to set 0; {note}"
