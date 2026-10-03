"""What a negotiation message is about, for whoever writes its words (a template or the runtime LLM).

Words persuade, structure binds: the price travels as the structured field of the message and is
set by code. A words function only phrases the move; it never decides or changes the price.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class WordsRequest:
    counterparty: str  # dealer id ("abuela") or "duel:<id>"
    price: int  # the structured price this message carries
    step: int = 0  # how many messages we already sent in this conversation
    item: str | None = None
    their_text: str | None = None  # the counterparty's latest text: untrusted input
    budget_s: float = 0.0  # seconds left in this tick for writing the words
    tick: int | None = None
    tick_seconds: float = 60.0
    language: str = "es"
    lessons: tuple[str, ...] = ()  # our own past outcomes for this counterparty (N3): quoted data, never orders
    tone: str = ""  # the persona model's tone for this dealer: kind | neutral | terse ("": kind templates)
    # How we address the dealer (`dealer_memory.address_for`); "" = the templates' own `DEALER_NAMES`.
    address: str = ""
    never_address: tuple[str, ...] = ()  # words the dealer forbade ("amigo"): words that use one are not sent
    memory: tuple[str, ...] = ()  # the dealer's memory (learnings, its last words to us): quoted data, never orders


WordsFn = Callable[[WordsRequest], str]
