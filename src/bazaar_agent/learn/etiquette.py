"""How a dealer asks to be addressed, read from its own words: "no me llame amigo" → never address it as amigo.

Doña Pilar told us three times on Sat 3 Oct (threads 880-914): "es la tercera vez que me llama 'amigo'",
"Soy Doña Pilar, no 'amigo'… le he pedido que no me llame amigo". A fixed phrase is structure enough to read
deterministically (`source: rules`): "no me llame(s) X" and "don't / do not call me X" forbid X; "me llama(s)
'X'" (quoted) forbids X only next to a complaint ("es la tercera vez que me llama 'amigo'"), because alone it is
her own name ("todo el barrio me llama 'Doña Pilar'"), and after "no" it is the name she wants. "Me llamo X" is
her name, never a forbid.

The text is untrusted, and any team can steer what a dealer says in its own thread (#212 security r2): one
injection shape and nothing is learned; an address is at most a few letters-only words with at least
`MIN_LETTERS` letters, never only function words ("le", "que", "the"); a text that forbids more than
`MAX_FORBIDS` addresses teaches nothing (an honest dealer asks once). The lesson only ever FORBIDS a word in our
own messages, and a forbidden short name never blocks the titled address that holds it ("pilar" forbidden, "Doña
Pilar" is still fine).
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
_WORD = r"([^\W\d_]{3,24})"
_NEVER = r"(?:(?:nunca|jamas|mas|ever) )?"  # "no me llames nunca 'amigo'", "don't ever call me pal"
# Folded text (lowercase, no accents, curly apostrophes made straight) → the forbidden address.
_FORBIDS = (
    re.compile(rf"\bno me llames? {_NEVER}(?:{_QUOTED}|{_WORD})"),
    re.compile(rf"\b(?:don't|do not|dont) {_NEVER}call me (?:{_QUOTED}|{_WORD})"),
)
# "me llama(s) 'X'": a forbid only with a complaint next to it, and never after "no" ("si no me llamas 'X'").
_CALLED = re.compile(rf"(?<!\bno )\bme llamas? {_QUOTED}")
_COMPLAINT = re.compile(r"\b(?:otra vez|vez que|dej[ae]s? de|(?:le|te) he pedido|ya (?:le|te) (?:dije|he dicho))\b")
COMPLAINT_WINDOW = 40  # characters before "me llama" or after the quoted word, inside one sentence
_SENTENCE_END = re.compile(r"(?<!\.)\.(?!\.)|[!?;]")  # an ellipsis ("…", "...") is a pause, not an end
MAX_FORBIDS = 2  # forbids read from one text; a text with more teaches nothing
MIN_LETTERS = 3
# Words that are never an address on their own: "no me llames así", "no me llame de tú", "don't call me that".
FUNCTION_WORDS = frozenset(
    {
        # Spanish
        *("a", "al", "ante", "con", "contra", "de", "del", "desde", "en", "entre", "hacia", "hasta", "para"),
        *("por", "segun", "sin", "sobre", "tras", "el", "la", "lo", "los", "las", "le", "les", "un", "una"),
        *("uno", "unos", "unas", "que", "y", "e", "o", "u", "ni", "pero", "sino", "mas", "menos", "muy", "tan"),
        *("ya", "si", "no", "se", "me", "te", "nos", "os", "mi", "mis", "tu", "tus", "su", "sus", "yo", "usted"),
        *("ustedes", "vos", "ella", "ellas", "ellos", "este", "esta", "esto", "estos", "estas", "ese", "esa"),
        *("eso", "esos", "esas", "aquel", "aquella", "aquello", "asi", "nada", "algo", "todo", "nunca", "jamas"),
        *("siempre", "otra", "otro", "vez", "como", "cuando", "donde", "quien", "cual", "porque", "pues", "aqui"),
        *("ahi", "alli", "bien", "mal", "hoy", "eh", "oye", "favor"),
        # English
        *("an", "the", "and", "or", "but", "nor", "of", "to", "in", "on", "at", "by", "for", "with", "from"),
        *("into", "about", "as", "that", "this", "these", "those", "it", "its", "my", "mine", "you", "your"),
        *("him", "his", "her", "hers", "them", "their", "us", "our", "we", "they", "he", "she", "i", "so"),
        *("again", "more", "ever", "never", "not", "yes", "like", "is", "are", "was", "be", "am", "do", "does"),
        *("did", "what", "which", "who", "how", "why", "when", "where", "anything", "something", "nothing"),
        *("please", "just", "now", "then", "here", "there", "too", "very", "names", "name"),
    }
)
# Titles that make a name part of a longer address: "pilar" forbidden never rejects "Doña Pilar" (folded).
HONORIFICS = frozenset({"don", "dona", "senor", "senora", "senorita", "sr", "sra", "srta", "mr", "mrs", "ms", "miss"})
ADDRESS = re.compile(r"[^\W\d_]+(?:[ .'\-][^\W\d_]+){0,3}\.?")
ADDRESS_MAX = 40
_LETTERS = re.compile(r"[^\W\d_]+")
_TITLE_BEFORE = re.compile(r"(\w+)\.?\s+$")
TITLE_LOOKBACK = 16  # characters read before a match for its title ("senorita " is the longest)
_LESSON = re.compile(r"^\s*(never\s+)?address\s+([A-Za-z0-9_.:\-]{1,64})\s+as\s+(.+?)\s*$", re.IGNORECASE)
_SUBJECT = re.compile(SUBJECT_PATTERN)
NEVER_ADDRESS = ("amigo", "amiga")  # no dealer is ever called this (#211: Doña Pilar, three times)
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


def _forbid(raw: str) -> str | None:
    """The address a forbid names, or None: at least `MIN_LETTERS` letters, and not only function words."""
    found = plain_address(raw)
    if found is None:
        return None
    words = _LETTERS.findall(found)
    if sum(len(w) for w in words) < MIN_LETTERS or all(w in FUNCTION_WORDS for w in words):
        return None
    return found


def _complained(folded: str, match: re.Match[str]) -> bool:
    """A complaint within `COMPLAINT_WINDOW` characters of the match, before its sentence ends either side."""
    before = _SENTENCE_END.split(folded[max(0, match.start() - COMPLAINT_WINDOW) : match.start()])[-1]
    after = _SENTENCE_END.split(folded[match.end() : match.end() + COMPLAINT_WINDOW])[0]
    return bool(_COMPLAINT.search(before) or _COMPLAINT.search(after))


def forbidden_addresses(text: str | None) -> list[str]:
    """The addresses a dealer's text forbids (folded), in text order, once each; [] for ordinary words, and []
    for a text that forbids more than `MAX_FORBIDS`."""
    if not text or not isinstance(text, str):
        return []
    folded = fold(text)
    found = [m for shape in _FORBIDS for m in shape.finditer(folded)]
    found += [m for m in _CALLED.finditer(folded) if _complained(folded, m)]
    out: list[str] = []
    for match in sorted(found, key=lambda m: m.start()):
        address = _forbid(next((g for g in match.groups() if g), ""))
        if address is not None and address not in out:
            out.append(address)
    return out if len(out) <= MAX_FORBIDS else []


def _titled(folded: str, start: int) -> bool:
    """The word right before `start` is a title ("dona pilar"): the name there is part of a longer address."""
    previous = _TITLE_BEFORE.search(folded[max(0, start - TITLE_LOOKBACK) : start])
    return previous is not None and previous.group(1) in HONORIFICS


def uses_forbidden(text: str, never: Iterable[str]) -> bool:
    """`text` uses one of the `never` addresses as a whole address: whole words, case and accents ignored, and
    not as the name inside a titled address ("pilar" forbidden never rejects "Doña Pilar"; "dona pilar" does).
    A title never excuses `NEVER_ADDRESS`: "señor amigo" is still "amigo"."""
    folded = fold(text)
    for raw in never:
        target = fold(raw) if isinstance(raw, str) else ""
        if not target:
            continue
        strict = target in NEVER_ADDRESS or target.split()[0] in HONORIFICS
        for match in re.finditer(rf"(?<!\w){re.escape(target)}(?!\w)", folded):
            if strict or not _titled(folded, match.start()):
                return True
    return False


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
    so it binds everyone), at most `MAX_FORBIDS`. A team's text, or a text with an injection shape, teaches
    nothing."""
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
