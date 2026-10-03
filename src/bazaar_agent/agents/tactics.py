"""The tactic bank (N16): what a message SAYS when we bluff, never what it binds.

RULES.md: "Words persuade, structure binds. Your agent may say anything." So a tactic may lie, but only in
the text: the structured price (and days, and every accept) is decided and guard-checked by the caller before
any tactic is rendered, and nothing here can see or change it. Every number a tactic prints is the message's
structured price (`{p}`) or a number invented from it (`{alt}`); no template holds a digit, so our limit,
`your_value` or a set multiplier cannot reach the text, and an invented number that happens to equal one of
them is moved (`avoid`). The counterparty's words are never quoted.

Abuela only ever gets kindness ("Abuela likes kindness"). Every other counterparty gets bluffs a trader could
plausibly say at El Rastro; none names a rule, the organisers or another team as its source, and none
impersonates anyone.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Literal

Side = Literal["buy", "sell"]
CounterpartyKind = Literal["dealer", "rival", "team"]
LANGUAGES = ("es", "en")
KINDNESS_ONLY = frozenset({"abuela"})  # RULES.md: "Abuela likes kindness"
NAMES = {"abuela": "Carmen", "chato": "Chato"}  # how we address a dealer; kindness only
NEUTRAL_NAME = {"es": "amigo", "en": "friend"}
ALT_DEALER = {"chato": "Abuela Carmen"}  # where a walk threat to this dealer says we go; default below
DEFAULT_ALT_DEALER = "Chato"
ELSEWHERE_DEAL = {"es": "a otro trato", "en": "to another deal"}  # a duel or team: there are always other deals
ELSEWHERE_DEALER = {"es": "con {other}", "en": "to {other}"}
ALT_TRIES = 4  # invented numbers tried below the price before a tactic gives up
_NUMBER = re.compile(r"\d+")


def _below(share: float) -> Callable[[int], Iterable[int]]:
    """Invented numbers a little below the structured price: `share` of it, then one lower each try."""

    def candidates(price: int) -> Iterable[int]:
        delta = max(1, round(price * share))
        return (price - d for d in range(delta, delta + ALT_TRIES))

    return candidates


@dataclass(frozen=True)
class Tactic:
    id: str
    kindness: bool
    lines: Mapping[str, Mapping[str, str]]  # side -> language -> template ({p}, {alt}, {n}, {elsewhere})
    invent: Mapping[str, Callable[[int], Iterable[int]]]  # side -> `{alt}` candidates from the price

    @property
    def sides(self) -> frozenset[str]:
        return frozenset(self.lines)


TACTICS: tuple[Tactic, ...] = (
    Tactic(
        "kind_gratitude",
        True,
        {
            "buy": {
                "es": "Muchas gracias por su paciencia, {n}. ¿Le parece bien {p} primas?",
                "en": "Thank you so much for your patience, {n}. Would {p} primas be all right?",
            },
            "sell": {
                "es": "Muchas gracias por atenderme, {n}. ¿Me daría {p} primas por él?",
                "en": "Thank you so much for seeing me, {n}. Would you give me {p} primas for it?",
            },
        },
        {},
    ),
    Tactic(
        "kind_flattery",
        True,
        {
            "buy": {
                "es": "Qué puesto tan bonito tiene, {n}; da gusto pasar por aquí. ¿Lo dejamos en {p}?",
                "en": "What a lovely stall, {n}; it is a pleasure to stop by. Shall we say {p}?",
            },
            "sell": {
                "es": "Siempre es un placer tratar con usted, {n}. ¿Le parecen justas {p} primas?",
                "en": "It is always a pleasure to deal with you, {n}. Is {p} primas fair?",
            },
        },
        {},
    ),
    Tactic(
        "kind_patience",
        True,
        {
            "buy": {
                "es": "Sin ninguna prisa, {n}, por favor. Le ofrezco {p} primas con todo el cariño.",
                "en": "No rush at all, {n}, please. I offer {p} primas, with all my affection.",
            },
            "sell": {
                "es": "Sin prisa, {n}, por favor. Se lo dejo en {p} primas con mucho cariño.",
                "en": "No rush, {n}, please. It is yours for {p} primas, with affection.",
            },
        },
        {},
    ),
    Tactic(
        "budget_cap",
        False,
        {
            "buy": {
                "es": "Es todo lo que me queda en el presupuesto: {p} primas.",
                "en": "That is all I have left in my budget: {p} primas.",
            },
        },
        {},
    ),
    Tactic(
        "outside_option",
        False,
        {
            "buy": {
                "es": "Otro vendedor me lo deja en {alt}, pero prefiero cerrarlo con usted: {p}.",
                "en": "Another seller offered it to me for {alt}, but I would rather close with you: {p}.",
            },
            "sell": {
                "es": "Otro equipo me ofrece {alt} por él; a usted se lo dejo en {p}.",
                "en": "Another team offered me {alt} for it; you can have it for {p}.",
            },
        },
        {"buy": _below(0.12), "sell": _below(0.05)},
    ),
    Tactic(
        "low_need",
        False,
        {
            "buy": {
                "es": "Solo lo quiero para un cambio, no tengo prisa. {p} primas.",
                "en": "I only need it for a swap, no hurry. {p} primas.",
            },
            "sell": {
                "es": "No necesito venderlo, me lo puedo quedar. {p} y es suyo.",
                "en": "I do not need to sell it, I am happy to keep it. {p} and it is yours.",
            },
        },
        {},
    ),
    Tactic(
        "walk_threat",
        False,
        {
            "buy": {
                "es": "Si no, en el próximo tick me voy {elsewhere}. Mi oferta: {p}.",
                "en": "Otherwise I go {elsewhere} next tick. My offer: {p}.",
            },
            "sell": {
                "es": "Si no cerramos ya, me voy {elsewhere}. Mi precio: {p}.",
                "en": "If we do not close now, I go {elsewhere}. My price: {p}.",
            },
        },
        {},
    ),
    Tactic(
        "fake_demand",
        False,
        {
            "sell": {
                "es": "Tres equipos me lo han pedido hoy. {p} y es suyo.",
                "en": "Three teams asked me for it today. {p} and it is yours.",
            },
        },
        {},
    ),
    Tactic(
        "cost_floor",
        False,
        {
            "sell": {
                "es": "Me costó casi {alt}; por debajo de {p} pierdo dinero.",
                "en": "It cost me almost {alt}; below {p} I lose money.",
            },
        },
        {"sell": _below(0.08)},
    ),
)
BY_ID: dict[str, Tactic] = {t.id: t for t in TACTICS}


def language_of(language: str) -> str:
    """'es' or 'en' (an English tag of any shape is English; everything else is Spanish, our default)."""
    return "en" if language.strip().lower().startswith("en") else "es"


def kindness_only(kind: str, counterparty: str) -> bool:
    return kind == "dealer" and counterparty in KINDNESS_ONLY


def eligible(kind: str, counterparty: str, side: str) -> tuple[str, ...]:
    """The tactic ids this counterparty may get on this side, in bank order: kindness for Abuela, bluffs for
    everyone else."""
    kind_only = kindness_only(kind, counterparty)
    return tuple(t.id for t in TACTICS if side in t.sides and t.kindness == kind_only)


def _elsewhere(kind: str, counterparty: str, language: str) -> str:
    if kind != "dealer":
        return ELSEWHERE_DEAL[language]
    other = ALT_DEALER.get(counterparty, DEFAULT_ALT_DEALER)
    return ELSEWHERE_DEALER[language].format(other=other)


def _invented(tactic: Tactic, side: str, price: int, avoid: frozenset[int]) -> int | None:
    """The first invented number below the price that is at least 1 and not a private number."""
    for alt in tactic.invent[side](price):
        if 1 <= alt < price and alt not in avoid:
            return alt
    return None


def render(
    tactic_id: str,
    price: int,
    *,
    side: str,
    language: str,
    kind: str,
    counterparty: str,
    avoid: Iterable[int] = frozenset(),
) -> str | None:
    """The tactic's text for a message whose structured price is `price`. None when this tactic does not fit
    (unknown id, wrong side, not kindness for Abuela, no plausible invented number). `avoid`: our private numbers
    (limit, max price, value), which an invented number must never equal."""
    tactic = BY_ID.get(tactic_id)
    if tactic is None or side not in tactic.sides or price < 1:
        return None
    if kindness_only(kind, counterparty) and not tactic.kindness:
        return None
    lang = language_of(language)
    fields = {
        "p": str(price),
        "n": NAMES.get(counterparty, NEUTRAL_NAME[lang]) if kind == "dealer" else NEUTRAL_NAME[lang],
        "elsewhere": _elsewhere(kind, counterparty, lang),
        "alt": "",
    }
    if side in tactic.invent:
        alt = _invented(tactic, side, price, frozenset(avoid))
        if alt is None:
            return None
        fields["alt"] = str(alt)
    return tactic.lines[side][lang].format(**fields)


def numbers_in(text: str) -> set[int]:
    return {int(m) for m in _NUMBER.findall(text)}


def leaks(text: str, private: Iterable[int], price: int) -> bool:
    """A private number in the text, other than the structured price (which the counterparty sees anyway)."""
    return bool((numbers_in(text) - {price}) & set(private))


def private_numbers(*values: object) -> frozenset[int]:
    """Our private numbers as the integers a text could show: each value, rounded down and up. Anything that is
    not a finite number (None, a string from an odd payload) is skipped."""
    out: set[int] = set()
    for value in values:
        if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
            continue
        out.update({math.floor(value), math.ceil(value), round(value)})
    return frozenset(out)
