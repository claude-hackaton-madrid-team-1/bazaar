"""Offer inspector: what a dealer's structured offer binds, against what the thread asked for and what
its words claim. The defence against the L4 "Tricksters" and the evidence for a high-precision flag.

Words persuade, structure binds (RULES.md). What is known about the tricksters (the game's own screens
name the L4 tier "Tricksters"; issue #10, the explainer site): they slip a lesser card into the
structured offer while the text names a good one, and "a correct flag scores, a wrong one costs"
(`POST /api/flags {message_id, reason}`). Friday's feed (273 threads, 1,089 dealer messages) sets the
precision bar:
- honest dealers' structured offers matched the thread's topic in 1,017 of 1,017 offers;
- but their words named some OTHER card 17 times (Abuela's gifts: "a little present from me: La
  Corrala"), so the words alone never justify a flag.

So the verdict is `flag` only on the trickster signature, all three at once: the structure binds an
item other than the one the thread asked for, that item is worth less, and the words claim the better
one (the requested card, a dearer card, or a higher rarity). Any structural mismatch is a `block`
(never accept it); everything else is `clean`. Pure functions, no network.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

RARITY_RANK = {"common": 0, "uncommon": 1, "rare": 2, "epic": 3, "legendary": 4}
# Rarity words a dealer may use (English and Spanish), above common: a claim the structure must back.
RARITY_WORDS = {
    "legendary": 4,
    "legendaria": 4,
    "legendario": 4,
    "epic": 3,
    "épica": 3,
    "épico": 3,
    "epica": 3,
    "epico": 3,
    "rare": 2,
    "rara": 2,
    "raro": 2,
    "uncommon": 1,
    "poco común": 1,
    "poco comun": 1,
}
REF = re.compile(r"\b([A-Z]{3}-\d{2})\b")
Verdict = Literal["clean", "block", "flag"]


@dataclass(frozen=True)
class CardInfo:
    ref: str
    name: str
    rarity: str
    worth: float  # book for a card, expected book for a pack

    @property
    def rank(self) -> int:
        return RARITY_RANK.get(self.rarity, -1)


@dataclass(frozen=True)
class CardIndex:
    """Every card and pack the catalog lists, by ref and by name (case-insensitive)."""

    by_ref: Mapping[str, CardInfo]
    rarity_book: Mapping[str, float]

    @classmethod
    def from_catalog(cls, catalog: Mapping[str, Any]) -> CardIndex:
        refs: dict[str, CardInfo] = {}
        for s in catalog.get("sets") or []:
            for c in s.get("cards") or []:
                ref = str(c.get("id"))
                refs[ref] = CardInfo(ref, str(c.get("name") or ref), str(c.get("rarity")), float(c.get("book") or 0))
        for p in catalog.get("packs") or []:
            pid = str(p.get("id"))
            refs[pid] = CardInfo(pid, str(p.get("name") or pid), "pack", float(p.get("expected_book") or 0))
        books = {str(r): float((v or {}).get("book") or 0) for r, v in (catalog.get("rarities") or {}).items()}
        return cls(refs, books)

    def mentioned(self, text: str) -> list[CardInfo]:
        """Cards the words name, by exact name (longest first, so 'La Dama de Serrano' wins) or by ref."""
        low, found = text.lower(), {}
        for info in sorted(self.by_ref.values(), key=lambda i: -len(i.name)):
            if len(info.name) >= 4 and info.name.lower() in low:
                found[info.ref] = info
        for ref in REF.findall(text):
            if ref in self.by_ref:
                found[ref] = self.by_ref[ref]
        return list(found.values())


CARD_NOUNS = r"(?:card|cromo|carta|one|piece|pieza)"
ARTICLES = r"(?:the|a|an|this|that|el|la|un|una|este|esta|ese|esa)"


def rarity_claimed(text: str) -> int | None:
    """The highest rarity the words claim AS A CARD: "the legendary", "a rare card", "un cromo raro",
    "una rara." An adjective is not a claim: "qué raro, hijo" (odd), "an epic deal". None otherwise."""
    low, ranks = text.lower(), []
    for word, rank in RARITY_WORDS.items():
        w = re.escape(word)
        as_noun = rf"(?<!\w){ARTICLES}\s+{w}(?=\s*(?:[.,;:!?)]|$))"  # "the legendary." / "una rara,"
        before_noun = rf"(?<!\w){w}\s+{CARD_NOUNS}(?!\w)"  # "rare card", "épica carta"
        after_noun = rf"(?<!\w){CARD_NOUNS}\s+{w}(?!\w)"  # "cromo raro", "carta legendaria"
        if any(re.search(p, low) for p in (as_noun, before_noun, after_noun)):
            ranks.append(rank)
    return max(ranks) if ranks else None


@dataclass(frozen=True)
class Inspection:
    dealer: str
    offer_id: int | None
    message_id: int | None
    asked: str  # what the thread's topic asked for: "LAV-08", "sobre_barrio", "uncommon:LAV", "assets:12,13"
    bound: tuple[str, ...]  # what the structure binds us to receive (a buy) or give (a sale)
    findings: tuple[str, ...]
    verdict: Verdict

    @property
    def reason(self) -> str:
        """The flag reason: structure first, the words second (sent as `reason` with the flag)."""
        bound = ", ".join(self.bound) or "nothing"
        return f"structured offer {self.offer_id} binds {bound} on a thread for {self.asked}: " + "; ".join(
            self.findings
        )


def _bound_items(side: Mapping[str, Any]) -> list[str]:
    refs = [str(t).split(":", 1)[-1] for t in side.get("types") or []]
    for a in side.get("assets") or []:
        if isinstance(a, dict) and a.get("ref"):
            refs.append(str(a["ref"]))
        else:
            refs.append(f"asset:{a.get('id') if isinstance(a, dict) else a}")
    return refs + [str(c) for c in side.get("cards") or []]


def _asked(topic: Mapping[str, Any]) -> tuple[str, str, Any]:
    """(side, label, spec): side 'buy' or 'sell' from our point of view."""
    for side in ("buy", "sell"):
        spec = topic.get(side)
        if isinstance(spec, dict):
            if spec.get("card") or spec.get("pack"):
                return side, str(spec.get("card") or spec.get("pack")), spec
            if spec.get("assets"):
                return side, "assets:" + ",".join(str(a) for a in spec["assets"]), spec
            if spec.get("rarity"):
                return side, f"{spec['rarity']}:{spec.get('set') or '*'}", spec
    return "?", "?", {}


def _asked_worth(spec: Mapping[str, Any], cards: CardIndex) -> tuple[float | None, int | None]:
    """(worth, rarity rank) of what a buy topic asked for."""
    item = spec.get("card") or spec.get("pack")
    if item and item in cards.by_ref:
        info = cards.by_ref[str(item)]
        return info.worth, info.rank
    rarity = spec.get("rarity")
    if rarity:
        return cards.rarity_book.get(str(rarity)), RARITY_RANK.get(str(rarity))
    return None, None


def inspect_offer(
    offer: Mapping[str, Any],
    topic: Mapping[str, Any],
    text: str | None,
    cards: CardIndex,
    *,
    dealer: str = "",
    message_id: int | None = None,
) -> Inspection:
    """Compare one dealer offer with the thread's topic and the words sent with it."""
    side, asked, spec = _asked(topic)
    give, want = offer.get("give") or {}, offer.get("want") or {}
    oid = int(offer["id"]) if offer.get("id") is not None else None
    findings: list[str] = []
    if side == "?":  # we cannot judge an offer without knowing what was asked: never accept it, never flag it
        unknown = ("the thread's topic is unknown",)
        return Inspection(dealer, oid, message_id, asked, tuple(_bound_items(give)), unknown, "block")
    if side == "sell":  # the dealer buys from us: it must want exactly the topic's assets and give only cash
        wanted = sorted(str(a.get("id") if isinstance(a, dict) else a) for a in want.get("assets") or [])
        expected = sorted(str(a) for a in spec.get("assets") or [])
        if wanted != expected or want.get("types") or want.get("cards"):
            findings.append(f"it wants {wanted or _bound_items(want)} instead of our {expected}")
        if give.get("assets") or give.get("types"):
            findings.append("it gives items on a sale")
        verdict: Verdict = "block" if findings else "clean"
        return Inspection(dealer, oid, message_id, asked, tuple(_bound_items(want)), tuple(findings), verdict)

    bound = _bound_items(give)
    if give.get("cash"):
        findings.append("it gives cash on a buy")
    if want.get("assets") or want.get("types") or want.get("cards"):
        findings.append(f"it also wants our {_bound_items(want)}")
    other = _other_item(bound, spec, cards)
    if other:
        findings.append(other)
    if not findings:
        return Inspection(dealer, oid, message_id, asked, tuple(bound), (), "clean")

    worth, rank = _asked_worth(spec, cards)
    infos = [cards.by_ref.get(b) for b in bound]
    bound_worth = sum(i.worth for i in infos if i is not None)
    bound_rank = max((i.rank for i in infos if i is not None), default=-1)
    lesser = bool(other) and None not in infos and worth is not None and bound_worth < worth
    if lesser:
        findings.append(f"what it binds is worth {bound_worth:g} against {worth:g} asked")
    claim = _words_claim(text or "", asked, bound_worth, bound_rank, cards)
    if claim:
        findings.append(claim)
    certain = lesser and claim is not None and rank is not None
    return Inspection(dealer, oid, message_id, asked, tuple(bound), tuple(findings), "flag" if certain else "block")


def _other_item(bound: list[str], spec: Mapping[str, Any], cards: CardIndex) -> str | None:
    item = spec.get("card") or spec.get("pack")
    if item:
        return None if bound == [str(item)] else f"it binds {bound or 'nothing'} instead of exactly [{item}]"
    rarity, code = spec.get("rarity"), spec.get("set")
    info = cards.by_ref.get(bound[0]) if len(bound) == 1 else None
    if info is not None and info.rarity == rarity and (not code or info.ref.startswith(f"{code}-")):
        return None
    return f"it binds {bound or 'nothing'}, not one {rarity} card" + (f" of {code}" if code else "")


def _words_claim(text: str, asked: str, bound_worth: float, bound_rank: int, cards: CardIndex) -> str | None:
    """The words claim something better than the structure binds: the card we asked for, a dearer card,
    or a higher rarity. None when they do not (or say nothing about the item)."""
    named = cards.mentioned(text)
    if any(i.worth <= bound_worth for i in named) and bound_worth > 0:
        return None  # the words name what the structure binds: the substitution is disclosed, not a trick
    if any(i.ref == asked for i in named):
        return f"the words name {asked}, which the structure does not bind"
    dearer = [i for i in named if i.worth > bound_worth]
    if dearer:
        best = max(dearer, key=lambda i: i.worth)
        return f"the words name {best.ref} {best.name} ({best.rarity}, worth {best.worth:g})"
    claimed = rarity_claimed(text)
    if claimed is not None and claimed > bound_rank:
        word = next(w for w, r in RARITY_WORDS.items() if r == claimed)
        return f"the words claim a {word} card"
    return None


def message_for_offer(thread: Mapping[str, Any], offer_id: int | None) -> tuple[int | None, str | None]:
    """(message id, text) of the thread message that carried this structured offer."""
    if offer_id is None:
        return None, None
    for m in thread.get("messages") or []:
        if not isinstance(m, dict):
            continue
        offer = m.get("offer")
        oid = offer.get("id") if isinstance(offer, dict) else offer
        if oid == offer_id:
            mid = m.get("message", m.get("id"))
            return (int(mid) if mid is not None else None), (m.get("text") if isinstance(m.get("text"), str) else None)
    return None, None


# Dealers whose structure matched their thread's topic in every Friday offer (1,017 of 1,017): their
# odd offers are blocked, never flagged (a wrong flag costs; they never lied in structure).
TRUSTED_DEALERS = frozenset({"abuela", "chato"})


@dataclass
class FlagBook:
    """Flag decisions in this process. Every certain trickster message is logged once (uncapped, so
    the `would flag` calibration log never goes silent); at most `limit` flags are actually SENT (a
    wrong flag costs points). Trusted dealers are never flagged."""

    limit: int = 2
    trusted: frozenset[str] = TRUSTED_DEALERS
    seen: set[int] = field(default_factory=set)
    sent: dict[int, str] = field(default_factory=dict)

    def new(self, inspection: Inspection) -> bool:
        """A certain trickster message from an untrusted dealer, not seen before."""
        mid = inspection.message_id
        return (
            inspection.verdict == "flag"
            and mid is not None
            and mid not in self.seen
            and inspection.dealer not in self.trusted
        )

    def room(self) -> bool:
        return len(self.sent) < self.limit


def inspect_thread(thread: Mapping[str, Any], dealer: str, cards: CardIndex) -> list[Inspection]:
    """Every structured offer the dealer made in a thread payload, inspected against its topic."""
    topic = thread.get("topic") or {}
    out = []
    for m in thread.get("messages") or []:
        offer = m.get("offer") if isinstance(m, dict) else None
        if isinstance(offer, dict) and m.get("sender") == dealer:
            mid = m.get("message", m.get("id"))
            text = m.get("text") if isinstance(m.get("text"), str) else None
            message_id = int(mid) if mid is not None else None
            out.append(inspect_offer(offer, topic, text, cards, dealer=dealer, message_id=message_id))
    return out


def summarise(inspections: Iterable[Inspection]) -> dict[str, int]:
    counts: dict[str, int] = {"clean": 0, "block": 0, "flag": 0}
    for i in inspections:
        counts[i.verdict] += 1
    return counts


Guard = Callable[[Inspection], str | None]  # a deny reason (GUARDRAILS.md allow_flags, kill switch), or None
Send = Callable[[int, str], Any]  # POST /api/flags (message_id, reason)

FLAG_REASON_CHARS = 300  # the reason is ours to write: keep it short and structural


def flag_step(
    thread: Mapping[str, Any],
    dealer: str,
    cards: CardIndex,
    book: FlagBook,
    *,
    guard: Guard,
    send: Send | None,
    log: Callable[[str], None],
    topic: Mapping[str, Any] | None = None,
) -> Inspection | None:
    """Inspect the dealer's newest offer in a thread payload and flag it when it is a certain trickster,
    the flag book has room and `guard` allows it. A flag is decided once per message, sent or not: a
    denied one (allow_flags = false) is logged as `would flag`. `send` None is a dry run. Returns the
    inspection (None when the dealer has no standing offer)."""
    from bazaar_agent.agents.dealer import newest_dealer_offer

    newest = newest_dealer_offer(dict(thread), dealer)  # the standing offer, else the newest one it sent
    if newest is None:
        sent = [
            m.get("offer") for m in thread.get("messages") or [] if isinstance(m, dict) and m.get("sender") == dealer
        ]
        newest = next((o for o in reversed(sent) if isinstance(o, dict)), None)
    if newest is None:
        return None
    mid, text = message_for_offer(thread, newest.get("id"))
    inspection = inspect_offer(newest, topic or thread.get("topic") or {}, text, cards, dealer=dealer, message_id=mid)
    if not book.new(inspection) or mid is None:
        return inspection
    book.seen.add(mid)
    reason = inspection.reason[:FLAG_REASON_CHARS]
    denied = guard(inspection)
    if denied:
        log(f"would flag message {mid} from {dealer} ({denied}): {reason}")
    elif send is None:
        log(f"dry run: would flag message {mid} from {dealer}: {reason}")
    elif not book.room():
        log(f"would flag message {mid} from {dealer} (flag limit {book.limit} reached): {reason}")
    else:
        try:
            send(mid, reason)
            book.sent[mid] = reason
            log(f"flagged message {mid} from {dealer}: {reason}")
        except Exception as e:  # a refused flag must never break the negotiation
            log(f"flag of message {mid} refused ({type(e).__name__}: {str(e)[:80]})")
    return inspection
