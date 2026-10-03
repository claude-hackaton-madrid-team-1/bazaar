"""The tactic bank (N16): what a message SAYS, never what it binds.

RULES.md: "Words persuade, structure binds. Your agent may say anything." So a tactic may lie, but only in
the text: the structured price (and days, and every accept) is decided and guard-checked by the caller before
any tactic is rendered, and nothing here can see or change it. Every number a tactic prints is the message's
structured price (`{p}`), the counterparty's OWN structured price (`{their}`, public to both sides: the one
safe token we mirror), or a number invented from our price (`{alt}`). No template holds a digit, so our limit,
`your_value` or a set multiplier cannot reach the text, and a tactic whose invented number would equal one of
them is not used for that message (`avoid`): moving the number by one would hint where the private one sits.
The counterparty's words are never parsed or quoted.

Three families. Kindness (Abuela: "Abuela likes kindness"). Psychology, from the vendored `negotiation`
(Voss) and `influence-psychology` (Cialdini) skills in `.ai/skills/`: labeling, calibrated questions, an
accusation audit (first message only), no-oriented questions, reciprocity, mirroring. Bluffs: claims a trader
could plausibly make at El Rastro (budget cap, outside option, low need, walk threat, fake demand, cost floor,
scarcity, social proof). Abuela gets kindness, labeling and calibrated questions only. No tactic names a rule,
the organisers or another team as its source, and none impersonates anyone.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Literal

Side = Literal["buy", "sell"]
CounterpartyKind = Literal["dealer", "rival", "team"]
Family = Literal["kindness", "psychology", "bluff"]
LANGUAGES = ("es", "en")
ABUELA = frozenset({"abuela"})  # RULES.md: "Abuela likes kindness": her own allow-list (`Tactic.abuela`)
NAMES = {"abuela": "Carmen", "chato": "Chato"}  # how we address a dealer
NEUTRAL_NAME = {"es": "amigo", "en": "friend"}
PLACE = {"dealer": {"es": "el puesto", "en": "the stall"}, "other": {"es": "el mercado", "en": "the market"}}
ALT_DEALER = {"chato": "Abuela Carmen"}  # where a walk threat to this dealer says we go; default below
DEFAULT_ALT_DEALER = "Chato"
ELSEWHERE_DEAL = {"es": "a otro trato", "en": "to another deal"}  # a duel or team: there are always other deals
ELSEWHERE_DEALER = {"es": "con {other}", "en": "to {other}"}
_NUMBER = re.compile(r"\d+")


def _below(share: float) -> Callable[[int], int]:
    """An invented number a little below the structured price: `share` of it lower, at least one."""

    def invent(price: int) -> int:
        return price - max(1, round(price * share))

    return invent


@dataclass(frozen=True)
class Tactic:
    id: str
    family: Family
    lines: Mapping[str, Mapping[str, str]]  # side -> language -> template
    invent: Mapping[str, Callable[[int], int]]  # side -> how `{alt}` is invented from our price
    abuela: bool = False  # on Abuela's allow-list (kindness, labeling, calibrated questions)
    others: bool = True  # for every other counterparty (the kindness lines name Carmen: hers only)
    first_only: bool = False  # an accusation audit opens a conversation, never later
    quotes_their: bool = False  # prints `{their}`: needs the counterparty's price, on the far side of ours

    @property
    def sides(self) -> frozenset[str]:
        return frozenset(self.lines)

    @property
    def kindness(self) -> bool:
        return self.family == "kindness"


def _t(
    tid: str,
    family: Family,
    buy: tuple[str, str] | None,
    sell: tuple[str, str] | None,
    *,
    invent: Mapping[str, Callable[[int], int]] | None = None,
    abuela: bool = False,
    others: bool = True,
    first_only: bool = False,
    quotes_their: bool = False,
) -> Tactic:
    """A tactic from its (es, en) lines per side; `invent` maps a side to how its `{alt}` is invented."""
    lines: dict[str, dict[str, str]] = {}
    for side, pair in (("buy", buy), ("sell", sell)):
        if pair is not None:
            lines[side] = {"es": pair[0], "en": pair[1]}
    return Tactic(tid, family, lines, invent or {}, abuela, others, first_only, quotes_their)


TACTICS: tuple[Tactic, ...] = (
    # ------------------------------------------------------------ kindness (Abuela)
    _t(
        "kind_gratitude",
        "kindness",
        (
            "Muchas gracias por su paciencia, {n}. ¿Le parece bien {p} primas?",
            "Thank you so much for your patience, {n}. Would {p} primas be all right?",
        ),
        (
            "Muchas gracias por atenderme, {n}. ¿Me daría {p} primas por él?",
            "Thank you so much for seeing me, {n}. Would you give me {p} primas for it?",
        ),
        abuela=True,
        others=False,
    ),
    _t(
        "kind_flattery",
        "kindness",
        (
            "Qué puesto tan bonito tiene, {n}; da gusto pasar por aquí. ¿Lo dejamos en {p}?",
            "What a lovely stall, {n}; it is a pleasure to stop by. Shall we say {p}?",
        ),
        (
            "Siempre es un placer tratar con usted, {n}. ¿Le parecen justas {p} primas?",
            "It is always a pleasure to deal with you, {n}. Is {p} primas fair?",
        ),
        abuela=True,
        others=False,
    ),
    _t(
        "kind_patience",
        "kindness",
        (
            "Sin ninguna prisa, {n}, por favor. Le ofrezco {p} primas con todo el cariño.",
            "No rush at all, {n}, please. I offer {p} primas, with all my affection.",
        ),
        (
            "Sin prisa, {n}, por favor. Se lo dejo en {p} primas con mucho cariño.",
            "No rush, {n}, please. It is yours for {p} primas, with affection.",
        ),
        abuela=True,
        others=False,
    ),
    # ------------------------------------------------------------ psychology (negotiation + influence skills)
    _t(  # tactical empathy / labeling: "It seems like..."
        "empathy_label",
        "psychology",
        (
            "Parece que ha sido una mañana larga en {place}, {n}. Le ofrezco {p} primas.",
            "It seems like it has been a long morning at {place}, {n}. I can offer {p} primas.",
        ),
        (
            "Parece que le hace mucha falta, {n}. Se lo dejo en {p} primas.",
            "It sounds like you really need it, {n}. It is yours for {p} primas.",
        ),
        abuela=True,
    ),
    _t(  # calibrated question: "How am I supposed to...?"
        "calibrated_question",
        "psychology",
        (
            "¿Cómo voy a pagar {their}, {n}? Le ofrezco {p}.",
            "How am I supposed to pay {their}, {n}? I can offer {p}.",
        ),
        (
            "¿Cómo voy a dejarlo en {their}, {n}? Mi precio es {p}.",
            "How am I supposed to let it go for {their}, {n}? My price is {p}.",
        ),
        abuela=True,
        quotes_their=True,
    ),
    _t(  # accusation audit: the first message only
        "accusation_audit",
        "psychology",
        (
            "Seguramente piensa que vengo a regatear a lo bruto y a hacerle perder el tiempo. Le ofrezco {p}.",
            "You probably think I came to lowball you and waste your time. I can offer {p}.",
        ),
        (
            "Seguramente piensa que le voy a pedir una barbaridad. Mi precio es {p}.",
            "You probably think I am going to ask for the moon. My price is {p}.",
        ),
        first_only=True,
    ),
    _t(  # no-oriented question: "Would it be unreasonable...?"
        "no_question",
        "psychology",
        ("¿Sería descabellado dejarlo en {p}?", "Would it be unreasonable to meet at {p}?"),
        ("¿Sería descabellado cerrarlo en {p}?", "Would it be unreasonable to close at {p}?"),
    ),
    _t(  # reciprocity: thanks and a small promised return
        "reciprocity",
        "psychology",
        (
            "Gracias por su tiempo; si cerramos en {p}, vuelvo mañana a por más cromos.",
            "Thanks for your time; if we close at {p}, I will be back tomorrow for more cards.",
        ),
        (
            "Gracias por su tiempo; si cerramos en {p}, le aviso primero de mis próximos cromos.",
            "Thanks for your time; if we close at {p}, you get first pick of my next cards.",
        ),
    ),
    _t(  # mirroring: their own structured price, the one safe token, never their words
        "mirror",
        "psychology",
        ("¿{their} primas? Le puedo dar {p}.", "{their} primas? I can give you {p}."),
        ("¿{their} primas? Por menos de {p} no puedo.", "{their} primas? I cannot go below {p}."),
        quotes_their=True,
    ),
    # ------------------------------------------------------------ bluffs
    _t(
        "budget_cap",
        "bluff",
        (
            "Es todo lo que me queda en el presupuesto: {p} primas.",
            "That is all I have left in my budget: {p} primas.",
        ),
        None,
    ),
    _t(
        "outside_option",
        "bluff",
        (
            "Otro vendedor me lo deja en {alt}, pero prefiero cerrarlo con usted: {p}.",
            "Another seller offered it to me for {alt}, but I would rather close with you: {p}.",
        ),
        (
            "Otro equipo me ofrece {alt} por él; a usted se lo dejo en {p}.",
            "Another team offered me {alt} for it; you can have it for {p}.",
        ),
        invent={"buy": _below(0.12), "sell": _below(0.05)},
    ),
    _t(
        "low_need",
        "bluff",
        (
            "Solo lo quiero para un cambio, no tengo prisa. {p} primas.",
            "I only need it for a swap, no hurry. {p} primas.",
        ),
        (
            "No necesito venderlo, me lo puedo quedar. {p} y es suyo.",
            "I do not need to sell it, I am happy to keep it. {p} and it is yours.",
        ),
    ),
    _t(
        "walk_threat",
        "bluff",
        (
            "Si no, en el próximo tick me voy {elsewhere}. Mi oferta: {p}.",
            "Otherwise I go {elsewhere} next tick. My offer: {p}.",
        ),
        (
            "Si no cerramos ya, me voy {elsewhere}. Mi precio: {p}.",
            "If we do not close now, I go {elsewhere}. My price: {p}.",
        ),
    ),
    _t(
        "fake_demand",
        "bluff",
        None,
        (
            "Tres equipos me lo han pedido hoy. {p} y es suyo.",
            "Three teams asked me for it today. {p} and it is yours.",
        ),
    ),
    _t(
        "cost_floor",
        "bluff",
        None,
        (
            "Me costó casi {alt}; por debajo de {p} pierdo dinero.",
            "It cost me almost {alt}; below {p} I lose money.",
        ),
        invent={"sell": _below(0.08)},
    ),
    _t(  # scarcity (influence): our offer expires / the card is running out
        "scarcity",
        "bluff",
        ("Esta oferta solo vale hoy: {p} primas.", "This offer is only good today: {p} primas."),
        (
            "Es de los últimos que quedan; a {p} no volverá a salir.",
            "It is one of the last ones left; it will not be {p} again.",
        ),
    ),
    _t(  # social proof (influence): what "everyone" pays
        "social_proof",
        "bluff",
        (
            "Casi todos los equipos lo han cerrado por {alt} o menos; le ofrezco {p}.",
            "Most teams closed it for {alt} or less; I can offer {p}.",
        ),
        (
            "Hoy ya se ha vendido varias veces a este precio: {p}.",
            "It has sold at this price several times today: {p}.",
        ),
        invent={"buy": _below(0.15)},
    ),
)
BY_ID: dict[str, Tactic] = {t.id: t for t in TACTICS}
ABUELA_ALLOWED: frozenset[str] = frozenset(t.id for t in TACTICS if t.abuela)


def language_of(language: str) -> str:
    """'es' or 'en' (an English tag of any shape is English; everything else is Spanish, our default)."""
    return "en" if language.strip().lower().startswith("en") else "es"


def abuela_only(kind: str, counterparty: str) -> bool:
    return kind == "dealer" and counterparty.strip().lower() in ABUELA


def allowed(tactic: Tactic, kind: str, counterparty: str) -> bool:
    return tactic.abuela if abuela_only(kind, counterparty) else tactic.others


def eligible(kind: str, counterparty: str, side: str) -> tuple[str, ...]:
    """The tactic ids this counterparty may get on this side, in bank order (Abuela: her allow-list only)."""
    return tuple(t.id for t in TACTICS if side in t.sides and allowed(t, kind, counterparty))


def _elsewhere(kind: str, counterparty: str, language: str) -> str:
    if kind != "dealer":
        return ELSEWHERE_DEAL[language]
    other = ALT_DEALER.get(counterparty.strip().lower(), DEFAULT_ALT_DEALER)
    return ELSEWHERE_DEALER[language].format(other=other)


def _invented(tactic: Tactic, side: str, price: int, avoid: frozenset[int]) -> int | None:
    """The invented number below the price; None when it is below 1 or equals a private number (the tactic is
    then not used for this message, never shifted: a shifted number would point at the private one)."""
    alt = tactic.invent[side](price)
    return alt if 1 <= alt < price and alt not in avoid else None


def _their_fits(side: str, price: int, their: int | None) -> bool:
    """`{their}` only quotes a counterparty price on the far side of ours (an ask above our bid, a bid below
    our ask): a question about a price we already beat would make no sense."""
    if their is None or isinstance(their, bool) or their < 1:
        return False
    return their > price if side == "buy" else their < price


def render(
    tactic_id: str,
    price: int,
    *,
    side: str,
    language: str,
    kind: str,
    counterparty: str,
    avoid: Iterable[int] = frozenset(),
    step: int = 0,
    their: int | None = None,
) -> str | None:
    """The tactic's text for a message whose structured price is `price`. None when it does not fit: unknown id,
    wrong side, not on this counterparty's list, an accusation audit after the first message, no counterparty
    price to quote, no plausible invented number. `avoid`: our private numbers (limit, max price, value), which an
    invented number must never equal. `their`: the counterparty's own structured price, never parsed from text."""
    tactic = BY_ID.get(tactic_id)
    if tactic is None or side not in tactic.sides or price < 1 or not allowed(tactic, kind, counterparty):
        return None
    if (tactic.first_only and step != 0) or (tactic.quotes_their and not _their_fits(side, price, their)):
        return None
    lang = language_of(language)
    fields = {
        "p": str(price),
        "n": NAMES.get(counterparty.strip().lower(), NEUTRAL_NAME[lang]) if kind == "dealer" else NEUTRAL_NAME[lang],
        "place": PLACE["dealer" if kind == "dealer" else "other"][lang],
        "elsewhere": _elsewhere(kind, counterparty, lang),
        "their": str(their) if tactic.quotes_their else "",
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
