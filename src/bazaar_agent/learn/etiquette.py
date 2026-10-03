"""How a dealer asks to be addressed, read from its own words: "no me llame amigo" → never address it as amigo.

Doña Pilar told us three times on Sat 3 Oct (threads 880-914): "es la tercera vez que me llama 'amigo'",
"Soy Doña Pilar, no 'amigo'… le he pedido que no me llame amigo". A fixed phrase is structure enough to read
deterministically (`source: rules`): "no me llame(s) X", "me llama(s) 'X'" (quoted only: "me llama la atención"
is no name), "don't / do not call me X". The text is untrusted: one injection shape and nothing is learned;
an address is at most a few letters-only words; the lesson only ever FORBIDS a word in our own messages.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from typing import Any

from pydantic import ValidationError

from bazaar_agent.learn.model import SUBJECT_PATTERN, Learning
from bazaar_agent.llm.chooser import injection_flags

QUOTES = "'\"‘’“”«»"
_QUOTED = rf"[{QUOTES}]([^{QUOTES}]{{1,40}})[{QUOTES}]"
_WORD = r"([^\W\d_]{2,24})"
# Folded text (lowercase, no accents, curly apostrophes made straight) → the forbidden address.
_SHAPES = (
    re.compile(rf"\bno me llames? (?:{_QUOTED}|{_WORD})"),
    re.compile(rf"\bme llam(?:as?|o) {_QUOTED}"),
    re.compile(rf"\b(?:don't|do not|dont) call me (?:{_QUOTED}|{_WORD})"),
)
# Words after "no me llame" that are not an address ("no me llame así", "no me llame de tú").
STOP_WORDS = frozenset(
    {"asi", "eso", "esto", "nada", "mas", "de", "por", "con", "tu", "usted", "that", "this", "so", "again", "more"}
)
ADDRESS = re.compile(r"[^\W\d_]+(?:[ .'\-][^\W\d_]+){0,3}\.?")
ADDRESS_MAX = 40
_LESSON = re.compile(r"^\s*(never\s+)?address\s+([A-Za-z0-9_.:\-]{1,64})\s+as\s+(.+?)\s*$", re.IGNORECASE)
_SUBJECT = re.compile(SUBJECT_PATTERN)
NEVER_ADDRESS = ("amigo",)  # no dealer is ever called this (#211: Doña Pilar, three times)
CONFIDENCE = 0.9  # a fixed phrase from the dealer itself; the words around it are still free text


def fold(text: str) -> str:
    """Lowercase, accents stripped, curly apostrophes straight, spaces collapsed: what the shapes are read on."""
    plain = "".join(ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch))
    return " ".join(plain.replace("’", "'").lower().split())


def plain_address(raw: str) -> str | None:
    """A few letters-only words, or None (a brace, a digit or a sentence is never an address)."""
    text = " ".join(raw.strip().strip(QUOTES).split())
    if not text or len(text) > ADDRESS_MAX or not ADDRESS.fullmatch(text):
        return None
    return text


def forbidden_addresses(text: str | None) -> list[str]:
    """The addresses a dealer's text forbids (folded), in order, once each; [] for ordinary words."""
    if not text or not isinstance(text, str):
        return []
    folded, out = fold(text), []
    for shape in _SHAPES:
        for match in shape.finditer(folded):
            word = next((g for g in match.groups() if g), "")
            found = plain_address(word)
            if found is not None and found not in STOP_WORDS and found not in out:
                out.append(found)
    return out


def uses_forbidden(text: str, never: Iterable[str]) -> bool:
    """`text` uses one of the `never` addresses as a whole word (case and accents ignored)."""
    folded = fold(text)
    return any(re.search(rf"(?<!\w){re.escape(fold(x))}(?!\w)", folded) for x in never if x and x.strip())


def etiquette_from_text(dealer_id: str, text: str | None) -> list[str]:
    """Lessons like "never address pilar as amigo" from one of the dealer's own texts."""
    return [f"never address {dealer_id} as {x}" for x in forbidden_addresses(text)]


def parse_etiquette(text: str) -> tuple[str, str, str] | None:
    """("never", dealer, X) or ("as", dealer, X) from a lesson's text; None for any other learning."""
    match = _LESSON.match(text or "")
    if match is None:
        return None
    return ("never" if match.group(1) else "as", match.group(2).lower(), match.group(3))


def etiquette_learnings(e: dict[str, Any]) -> list[Learning]:
    """One `behaviour` learning per address a dealer forbids in a `thread.message` it sent (about the dealer,
    so it binds everyone). A team's text, or a text with an injection shape, teaches nothing."""
    p = e.get("payload")
    if e.get("type") != "thread.message" or not isinstance(p, dict) or p.get("kind") != "persona":
        return []
    sender, text, eid = p.get("sender"), p.get("text"), e.get("id")
    if not isinstance(sender, str) or sender != p.get("with") or not _SUBJECT.match(sender):
        return []
    if not isinstance(text, str) or not isinstance(eid, int) or injection_flags(text):
        return []
    out = []
    for x in forbidden_addresses(text):
        try:
            out.append(_learning(sender, x, eid, e.get("tick")))
        except ValidationError:
            continue
    return out


def _learning(dealer: str, address: str, eid: int, tick: object) -> Learning:
    return Learning(
        subject_kind="dealer",
        subject=dealer,
        kind="behaviour",
        tick=tick if isinstance(tick, int) and tick >= 0 else 0,
        evidence=(eid,),
        confidence=CONFIDENCE,
        text=f"never address {dealer} as {address}",
        source="rules",
        detail={"pattern": f"never_address:{address}"},  # one row per distinct address
    )
