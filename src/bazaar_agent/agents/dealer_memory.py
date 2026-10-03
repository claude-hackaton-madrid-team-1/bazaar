"""A dealer's memory, recalled before a thread with it opens or prices: what we learned about it, the last
words it said to us, and how to address it.

- Learnings: the newest `MAX_LEARNINGS` of kind `behaviour` or `lesson` about the dealer, from the rules
  reader or our own outcomes (an LLM reading of feed text is left out: it may quote another team's words).
  Read from the store's memory by default (no I/O inside the tick); the live learner pulls the stored ones in
  after the sends. Any error is an empty memory, never a raise.
- Texts: its last `MAX_TEXTS` messages TO US in a dealer thread of the feed window, untrusted: one line,
  `TEXT_MAX` characters, and a text with an injection shape is withheld (only its tags are kept). They reach
  only our words, as quoted data; Jev sees their counts and tags, never their text (`jev_facts`).
- Address (`address_for`): an etiquette learning ("address chato as Don Chato"), then for the three dealers
  #211 named the short form in `DEALER_NAMES` ("Chato", not the published "El Chato"), for any other dealer its
  published name (`/api/dealers`); never a word the dealer forbade (at most `NEVER_ADDRESS_MAX` of them), never
  "amigo" or "amiga" (Doña Pilar, Sat 3 Oct). Everything here is quoted data; it never sets a price.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from bazaar_agent.agents.dealer import DEALER_NAMES
from bazaar_agent.learn.etiquette import (
    NEVER_ADDRESS,
    fold,
    forbidden_addresses,
    parse_etiquette,
    plain_address,
    uses_forbidden,
)
from bazaar_agent.learn.model import Learning
from bazaar_agent.llm.chooser import injection_flags

MAX_LEARNINGS = 5
MAX_TEXTS = 3
TEXT_MAX = 200
NEVER_ADDRESS_MAX = 8  # forbidden addresses kept for the words and the prompt; NEVER_ADDRESS always among them
KINDS = frozenset({"behaviour", "lesson"})
SOURCES = frozenset({"rules", "outcome"})
RECALL_POOL = 50  # learnings read before the source filter keeps the newest MAX_LEARNINGS


@dataclass(frozen=True)
class DealerText:
    event_id: int
    tick: int
    text: str  # one line, truncated; "[withheld: ...]" when it carried an injection shape
    flags: tuple[str, ...] = ()
    forbids: tuple[str, ...] = ()  # the addresses this text forbids (read before truncation)


@dataclass(frozen=True)
class DealerMemory:
    dealer: str
    learnings: tuple[Learning, ...] = ()
    texts: tuple[DealerText, ...] = ()
    status: str = "ok"  # ok | error:<Type>
    # Etiquette rows ("never address X as Y"), kept apart: another team can steer a dealer's words, so they never
    # take one of the `MAX_LEARNINGS` lesson slots nor reach a Jev state as text (#221 reviews).
    etiquette: tuple[Learning, ...] = ()

    def never_address(self) -> tuple[str, ...]:
        """The addresses the dealer forbade, folded: "amigo" and "amiga" always, then its newest words to us, then
        its newest learnings, at most `NEVER_ADDRESS_MAX` in all (a steered flood cannot fill the prompt)."""
        out = [*NEVER_ADDRESS, *(x for t in reversed(self.texts) for x in t.forbids)]
        for lr in (*self.etiquette, *self.learnings):
            parsed = parse_etiquette(lr.text)
            if parsed is not None and parsed[0] == "never" and parsed[1] == self.dealer:
                out.append(fold(parsed[2]))
        return tuple(dict.fromkeys(out))[:NEVER_ADDRESS_MAX]

    def preferred(self) -> list[str]:
        """The addresses its etiquette learnings ask for, newest first."""
        found = (parse_etiquette(lr.text) for lr in (*self.etiquette, *self.learnings))
        return [p[2] for p in found if p is not None and p[0] == "as" and p[1] == self.dealer]

    def facts(self) -> dict[str, Any]:
        """JSON-safe, for the decision row and the Jev state."""
        return {
            "dealer": self.dealer,
            "learnings": [
                {"kind": lr.kind, "tick": lr.tick, "source": lr.source, "text": lr.text} for lr in self.learnings
            ],
            "etiquette": [{"tick": lr.tick, "source": lr.source, "text": lr.text} for lr in self.etiquette],
            "their_recent_texts": [{"tick": t.tick, "text": t.text, "flags": list(t.flags)} for t in self.texts],
            "never_address": list(self.never_address()),
            "status": self.status,
        }

    def jev_facts(self) -> dict[str, Any]:
        """For a Jev state: structure only (#212 security r2). Our learnings from rules or outcomes, and how many
        texts it sent us, how many were withheld and their injection tags; never the words themselves."""
        return {
            "dealer": self.dealer,
            "learnings": [
                {"kind": lr.kind, "tick": lr.tick, "source": lr.source, "text": lr.text}
                for lr in self.learnings
                if lr.source in SOURCES
            ],
            "etiquette_rows": len(self.etiquette),
            "their_recent_texts": {
                "count": len(self.texts),
                "withheld": sum(1 for t in self.texts if t.flags),
                "flags": sorted({f for t in self.texts for f in t.flags}),
            },
            "status": self.status,
        }

    def lines(self) -> tuple[str, ...]:
        """Quoted lines for the words: what we learned, then what it last said to us."""
        return tuple(lr.text for lr in self.learnings) + tuple(t.text for t in self.texts if not t.flags)


def recall_dealer(
    store: Any,
    dealer_id: str,
    events: Iterable[Mapping[str, Any]] | None,
    *,
    us: str | None = None,
    tick: int | None = None,
    use_db: bool = False,
) -> DealerMemory:
    """The dealer's memory; an empty one (with `status` saying why) on any error."""
    try:
        lessons, etiquette = _learnings(store, dealer_id, us, tick, use_db)
        return DealerMemory(dealer_id, lessons, _texts(events, dealer_id, us), etiquette=etiquette)
    except Exception as e:  # noqa: BLE001 — fail open: no memory is today's behaviour
        return DealerMemory(dealer_id, status=f"error:{type(e).__name__}")


def _learnings(
    store: Any, dealer: str, us: str | None, tick: int | None, use_db: bool
) -> tuple[tuple[Learning, ...], tuple[Learning, ...]]:
    """(lessons, etiquette rows): the newest `MAX_LEARNINGS` of each from rules or outcomes."""
    if store is None:
        return (), ()
    found = store.recall(dealer, KINDS, tick, subject_kind="dealer", team=us, limit=RECALL_POOL, use_db=use_db)
    kept = [lr for lr in found if lr.source in SOURCES]
    etiquette = [lr for lr in kept if parse_etiquette(lr.text) is not None]
    lessons = [lr for lr in kept if parse_etiquette(lr.text) is None]
    return tuple(lessons[:MAX_LEARNINGS]), tuple(etiquette[:NEVER_ADDRESS_MAX])


def _texts(events: Iterable[Mapping[str, Any]] | None, dealer: str, us: str | None) -> tuple[DealerText, ...]:
    if events is None or not us:
        return ()
    mine = []
    for e in events:
        p = e.get("payload")
        if e.get("type") != "thread.message" or not isinstance(p, Mapping) or not isinstance(e.get("id"), int):
            continue
        if p.get("kind", "persona") != "persona":  # a team thread is never the dealer speaking (#212 security r2)
            continue
        if p.get("sender") == dealer and p.get("team") == us and isinstance(p.get("text"), str) and p["text"].strip():
            mine.append(e)
    mine.sort(key=lambda e: int(e["id"]))
    return tuple(_text(e) for e in mine[-MAX_TEXTS:])


def _text(e: Mapping[str, Any]) -> DealerText:
    raw = str(e["payload"]["text"])
    flags = injection_flags(raw)
    tick = e.get("tick") if isinstance(e.get("tick"), int) else 0
    if flags:
        return DealerText(int(e["id"]), int(tick or 0), f"[withheld: {', '.join(flags)}]", flags)
    line = " ".join("".join(ch if ch.isprintable() else " " for ch in raw).split())
    line = line[:TEXT_MAX] + "…" if len(line) > TEXT_MAX else line
    return DealerText(int(e["id"]), int(tick or 0), line, (), tuple(forbidden_addresses(raw)))


# ---------------------------------------------------------------- the address


def _published_name(persona: Any, dealer: str) -> str:
    name = persona if isinstance(persona, str) else getattr(persona, "name", "")
    return "" if not isinstance(name, str) or name.strip().lower() == dealer.lower() else name


def address_for(dealer_id: str, memory: DealerMemory | None, personas: Mapping[str, Any] | None) -> str:
    """How we address the dealer: an etiquette learning first; then `DEALER_NAMES` for the three dealers #211
    named (its short forms outrank the published "El Chato" and "Abuela Carmen"), or the published name for any
    other dealer; "" when none is safe (the words then leave the address out). Never a forbidden word, never
    "amigo"."""
    memory = memory or DealerMemory(dealer_id)
    never = memory.never_address()
    known = DEALER_NAMES.get(dealer_id)
    fallback = known if known is not None else _published_name((personas or {}).get(dealer_id), dealer_id)
    for raw in [*memory.preferred(), fallback]:
        candidate = plain_address(raw or "")
        if candidate is not None and not uses_forbidden(candidate, never):
            return candidate
    return ""
