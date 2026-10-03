"""The team desk: negotiate a direct deal with another team in a thread, structured offers only.

W4's plan proposes swaps and direct deals (`trade_desk`); this desk carries one through a TEAM thread on a
venue: open the thread with our structured proposal, read the other team's structured counter every tick,
then accept, counter (one message per tick) or walk.

Words persuade, structure binds (RULES.md). The other team's text is never an input to a decision:
  - what an offer is worth to us comes from its structure only (`value_of`): the cards and cash it binds;
  - the text is inspected for one thing, a claim the structure does not back (`inspect_team_offer`: a card,
    a rarity or more cash named in the words but not bound, B3's `CardIndex` reads the words). Such an offer
    is `bait`: never accepted, logged; our next counter is computed as if it had not been made;
  - our own messages are fixed templates naming our structured offer, never an echo of theirs.
An offer is accepted only when its surplus at our values (fee included: the accepting side pays it) clears
`floor`, it binds exactly what it says (no assets we do not hold, no card we already hold), and every action
it implies passes `guardrails.check()` (the sale floor on each copy we give, the cash floor and spend cap on
cash we give, the price cap of what we buy, the counterparty share, the kill switch).
We concede on one dimension only, the cash leg of our own proposal, in steps, never past the limit at which
our surplus would fall below `floor`; their structure is never adopted as our counter.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any, Literal

from bazaar_agent.agents.inspector import RARITY_RANK, CardIndex, rarity_claimed
from bazaar_agent.agents.market import Venue
from bazaar_agent.guardrails import Action, Context, Guardrails, check
from bazaar_agent.strategy import Market, StrategyParams, bonus_at_stake, buy_case, copy_value

MoveKind = Literal["open", "counter", "accept", "wait", "walk"]
CASH_WORDS = re.compile(r"(\d{1,6})\s*(?:p\b|primas?\b|€|eur\b|cash\b)", re.IGNORECASE)


# ---------------------------------------------------------------- terms: one structured offer, our side


@dataclass(frozen=True)
class Terms:
    """A structured offer between us and one team, seen from OUR side: what we give and what we get.
    Cards we get are refs (any copy, or the copies the other team names); cards we give are our asset ids
    (or refs when the other side asks for any copy of a card)."""

    give_assets: tuple[int, ...] = ()
    give_refs: tuple[str, ...] = ()  # "any copy of" cards they want from us
    give_cash: int = 0
    get_refs: tuple[str, ...] = ()
    get_assets: tuple[int, ...] = ()  # their copies, when their offer names them
    get_cash: int = 0

    def offer(self) -> dict[str, Any]:
        """The `{give, want}` of the structured offer WE post."""
        give: dict[str, Any] = {"assets": list(self.give_assets)}
        if self.give_cash:
            give["cash"] = self.give_cash
        want: dict[str, Any] = {"cards": list(self.get_refs)}
        if self.get_cash:
            want["cash"] = self.get_cash
        return {"give": give, "want": want}

    @property
    def cards_moving(self) -> int:
        return len(self.give_assets) + len(self.give_refs) + len(self.get_refs)


def _ids(side: Mapping[str, Any]) -> list[int]:
    return [int(a["id"] if isinstance(a, dict) else a) for a in side.get("assets") or [] if _id_like(a)]


def _id_like(a: Any) -> bool:
    return isinstance(a, int) or (isinstance(a, dict) and isinstance(a.get("id"), int))


def _refs(side: Mapping[str, Any]) -> list[str]:
    refs = [str(t).split(":", 1)[-1] for t in (side.get("types") or []) + (side.get("cards") or [])]
    refs += [str(a["ref"]) for a in side.get("assets") or [] if isinstance(a, dict) and a.get("ref")]
    return refs


def their_terms(offer: Mapping[str, Any]) -> Terms:
    """Their offer (maker = the other team) as our terms: their `give` is what we get, their `want` what we
    give. Their assets carry refs on the wire (`{id, ref}`); a bare id we cannot read is kept as an id."""
    give, want = offer.get("give") or {}, offer.get("want") or {}
    get_assets = tuple(_ids(give))
    get_refs = tuple(_refs(give))
    give_ids = tuple(_ids(want))
    give_refs = tuple(str(t).split(":", 1)[-1] for t in (want.get("types") or []) + (want.get("cards") or []))
    return Terms(give_ids, give_refs, int(want.get("cash") or 0), get_refs, get_assets, int(give.get("cash") or 0))


# ---------------------------------------------------------------- value at our values (structure only)


@dataclass(frozen=True)
class Value:
    received: float
    given: float
    fee: int  # what we pay when WE accept (0 when they accept our offer)
    problems: tuple[str, ...]  # why the structure cannot be valued (an asset we do not hold, ...)

    @property
    def surplus(self) -> float:
        return round(self.received - self.given - self.fee, 2)


def value_of(
    t: Terms, m: Market, me: Mapping[str, Any], params: StrategyParams, venue: Venue | None, *, we_accept: bool
) -> Value:
    """What these terms are worth to us, from the structure alone: each card we get at its worth to us (a
    missing page card with its page bonus share, else the next copy's marginal), each copy we give at what
    losing it costs (your_value + the page bonus it carries), cash at face value, the fee when we accept."""
    assets = {int(a["id"]): a for a in me.get("assets") or [] if isinstance(a.get("id"), int)}
    held = Counter(m.held)
    problems: list[str] = []
    given = float(t.give_cash)
    for asset_id in t.give_assets:
        a = assets.get(asset_id)
        if a is None or a.get("kind") != "card" or not isinstance(a.get("your_value"), int | float):
            problems.append(f"asset {asset_id} is not a card we hold with a your_value")
            continue
        card = m.cards.get(str(a.get("ref")))
        given += float(a["your_value"]) + (bonus_at_stake(m, card, params) if card else 0.0)
        held[str(a.get("ref"))] -= 1
    for ref in t.give_refs:  # any copy: the one we lose least by
        copies = [a for a in assets.values() if a.get("ref") == ref and isinstance(a.get("your_value"), int | float)]
        if not copies:
            problems.append(f"they want {ref}, which we do not hold")
            continue
        card = m.cards.get(ref)
        given += min(float(a["your_value"]) for a in copies) + (bonus_at_stake(m, card, params) if card else 0.0)
        held[ref] -= 1
    received = float(t.get_cash)
    for ref in t.get_refs:
        card = m.cards.get(ref)
        if card is None:
            problems.append(f"{ref} is not a card in the catalog")
            continue
        if held[ref] > 0:  # a copy we already hold: its next copy's marginal, never a page share
            received += copy_value(m, card, held[ref])
        else:
            received += buy_case(m, card, params).value
        held[ref] += 1
    cash = max(t.give_cash, t.get_cash)
    fee = venue.fee(cash, t.cards_moving) if (venue is not None and we_accept) else 0
    return Value(round(received, 2), round(given, 2), fee, tuple(problems))


# ---------------------------------------------------------------- the inspector: words vs structure


@dataclass(frozen=True)
class TeamInspection:
    offer_id: int | None
    verdict: Literal["clean", "block", "bait"]
    findings: tuple[str, ...]


def inspect_team_offer(
    offer: Mapping[str, Any], text: str | None, cards: CardIndex, me: Mapping[str, Any]
) -> TeamInspection:
    """The other team's structured offer against what it can bind and what its words claim.

    `block`: the structure cannot be what it looks like: it gives no named card copy (a giver must name the
    asset), wants an asset of ours we do not hold, or gives nothing. `bait`: the words claim something the
    structure does not bind: a card named but not given, more cash than bound, a higher rarity than any card
    given (B3's `CardIndex.mentioned` / `rarity_claimed`; a card named only to say it is gone is no claim).
    `clean` otherwise. The verdict only ever stops us accepting; the words never move a price."""
    oid = int(offer["id"]) if isinstance(offer.get("id"), int) else None
    give, want = offer.get("give") or {}, offer.get("want") or {}
    findings: list[str] = []
    if give.get("types") or give.get("cards"):
        findings.append("it gives 'any copy' of a card: a giver must name the copy (asset)")
    if not (give.get("assets") or give.get("cash")):
        findings.append("it gives nothing")
    ours = {int(a["id"]) for a in me.get("assets") or [] if isinstance(a.get("id"), int)}
    missing = [i for i in _ids(want) if i not in ours]
    if missing:
        findings.append(f"it wants assets {missing} we do not hold")
    if findings:
        return TeamInspection(oid, "block", tuple(findings))
    words = text or ""
    bound_refs = set(_refs(give))
    bound_cash = int(give.get("cash") or 0)
    claims: list[str] = []
    for info in cards.mentioned(words):
        if info.ref not in bound_refs and info.ref not in set(_refs(want)) and not cards.negated(words, info.ref):
            claims.append(f"the words name {info.ref} ({info.name}), which the structure does not give")
    named_cash = [int(n) for n in CASH_WORDS.findall(words)]
    if named_cash and max(named_cash) > bound_cash and max(named_cash) != int(want.get("cash") or 0):
        claims.append(f"the words name {max(named_cash)} P, the structure gives {bound_cash}")
    claimed = rarity_claimed(words)
    ranks = [cards.by_ref[r].rank for r in bound_refs if r in cards.by_ref]
    if claimed is not None and claimed > max(ranks, default=-1):
        word = next(k for k, v in RARITY_RANK.items() if v == claimed)
        claims.append(f"the words claim a {word} card, the structure gives none")
    return TeamInspection(oid, "bait" if claims else "clean", tuple(claims))


# ---------------------------------------------------------------- the negotiation (pure policy)


@dataclass(frozen=True)
class TeamPlan:
    """One direct deal to negotiate with `team` on `venue`: our opening terms and how far we may move."""

    team: str
    venue: str
    opening: Terms
    floor: float = 2.0  # our least surplus on a deal (min_buy_surplus); never accept or offer below it
    step: int = 2  # P our cash leg moves per counter
    max_rounds: int = 8  # our messages before we walk
    expires_in_ticks: int = 10  # life of each structured offer we make in the thread


@dataclass
class TeamNegotiation:
    plan: TeamPlan
    thread_id: int | None = None
    ours: list[Terms] = field(default_factory=list)  # our offers, newest last
    seen: set[int] = field(default_factory=set)  # their offer ids already judged
    accepted: int | None = None  # the offer id we accepted (settles next tick)
    ticks: int = 0

    @property
    def current(self) -> Terms:
        return self.ours[-1] if self.ours else self.plan.opening


@dataclass(frozen=True)
class TeamMove:
    kind: MoveKind
    terms: Terms | None = None
    offer_id: int | None = None  # accept: their offer
    reason: str = ""


def concede(t: Terms, step: int) -> Terms:
    """Our next offer: `step` P better for them on the cash leg (we ask less, or add more)."""
    if t.get_cash > 0:
        return replace(t, get_cash=max(0, t.get_cash - step))
    return replace(t, give_cash=t.give_cash + step)


def decide(
    neg: TeamNegotiation,
    their: Mapping[str, Any] | None,
    inspection: TeamInspection | None,
    surplus_if_accepted: Callable[[Terms], Value],
    surplus_if_offered: Callable[[Terms], Value],
    guard: Callable[[Terms, Mapping[str, Any] | None], str | None],
) -> TeamMove:
    """This tick's move. `their` is the other team's newest standing offer in the thread (None: none);
    the callables value terms (structure only) and say why the guardrails would refuse a deal."""
    plan = neg.plan
    if neg.thread_id is None:
        return TeamMove("open", plan.opening, reason="open the thread with our proposal")
    if their is not None and isinstance(their.get("id"), int) and inspection is not None:
        terms = their_terms(their)
        value = surplus_if_accepted(terms)
        if inspection.verdict != "clean":
            why = f"their offer {their['id']} is {inspection.verdict}: {'; '.join(inspection.findings)}"
        elif value.problems:
            why = f"their offer {their['id']} cannot be valued: {'; '.join(value.problems)}"
        elif value.surplus < plan.floor:
            why = f"their offer {their['id']} gives us {value.surplus:+.1f} (< floor {plan.floor:g})"
        elif (refused := guard(terms, their)) is not None:
            why = f"their offer {their['id']}: guardrails refuse ({refused})"
        else:
            return TeamMove("accept", terms, int(their["id"]), f"their offer gives us {value.surplus:+.1f}")
    else:
        why = "no standing offer from them"
    if len(neg.ours) >= plan.max_rounds:
        return TeamMove("walk", reason=f"{plan.max_rounds} offers without a deal; {why}")
    nxt = concede(neg.current, plan.step) if neg.ours else neg.current
    if nxt == neg.current and neg.ours:
        return TeamMove("wait", reason=f"our offer stands; {why}")
    if surplus_if_offered(nxt).surplus < plan.floor or guard(nxt, None) is not None:
        return TeamMove("wait", reason=f"at our limit, our last offer stands; {why}")
    return TeamMove("counter", nxt, reason=why)


def newest_offer_from(thread: Mapping[str, Any], team: str) -> dict[str, Any] | None:
    """The other team's newest OPEN standing offer in the thread, if any."""
    offers = [
        o
        for o in thread.get("standing_offers") or []
        if isinstance(o, dict) and o.get("maker") == team and o.get("status") in (None, "open")
    ]
    return max(offers, key=lambda o: int(o.get("id") or 0), default=None)


def text_for_offer(thread: Mapping[str, Any], offer_id: int | None) -> str | None:
    """The words sent with a structured offer (untrusted: for the inspector only)."""
    for msg in thread.get("messages") or []:
        if not isinstance(msg, dict):
            continue
        offer = msg.get("offer")
        oid = offer.get("id") if isinstance(offer, dict) else offer
        if offer_id is not None and oid == offer_id:
            return msg.get("text") if isinstance(msg.get("text"), str) else None
    return None


# ---------------------------------------------------------------- our words: fixed templates


def words_for(kind: MoveKind, t: Terms, cards: CardIndex) -> str:
    """Our message, a template naming only OUR structured offer (never their words)."""

    def name(ref: str) -> str:
        info = cards.by_ref.get(ref)
        return f"{ref}" + (f" ({info.name})" if info else "")

    gives = [f"our copy #{i}" for i in t.give_assets] + [f"a {name(r)}" for r in t.give_refs]
    gives += [f"{t.give_cash} P"] if t.give_cash else []
    gets = [f"your {name(r)}" for r in t.get_refs] + ([f"{t.get_cash} P"] if t.get_cash else [])
    deal = f"{' + '.join(gives) or 'nothing'} for {' + '.join(gets) or 'nothing'}"
    if kind == "open":
        return f"Proposal: {deal}. The structured offer is attached; accept it if it works for you."
    return f"Counter: {deal}. The structured offer is attached."


# ---------------------------------------------------------------- guardrails for a team deal


def deal_actions(t: Terms, m: Market, team: str, notional: int, worth_received: float) -> list[Action]:
    """The guardrail actions a deal implies: each copy we give is a sale at what we receive for the whole
    deal (never below its your_value × sell_min_value_ratio, checked per copy at its share), each card we get
    a buy of the cash we pay (its price cap, never a card we hold: `block_buying_held_cards`), the cash we
    give the cash floor and the spend cap; all count the deal's notional toward the team's share."""
    out: list[Action] = []
    copies = len(t.give_assets) + len(t.give_refs)
    per_copy = math.floor((worth_received + t.get_cash) / copies) if copies else 0
    for _ in range(copies):
        out.append(Action("sell", "", None, per_copy, counterparty=team, volume=notional))
    for i, ref in enumerate(t.get_refs):
        card = m.cards.get(ref)
        cash = t.give_cash if i == 0 else 0  # the cash leg is one payment, checked once
        out.append(Action("bid", ref, card.rarity if card else None, cash, counterparty=team, volume=notional))
    return out


def your_values(t: Terms, me: Mapping[str, Any]) -> list[float | None]:
    """The your_value of each copy we give, in `deal_actions` order (asset ids first, then refs)."""
    assets = {int(a["id"]): a for a in me.get("assets") or [] if isinstance(a.get("id"), int)}
    out: list[float | None] = []
    for i in t.give_assets:
        v = (assets.get(i) or {}).get("your_value")
        out.append(float(v) if isinstance(v, int | float) else None)
    for ref in t.give_refs:
        vals = [
            float(a["your_value"])
            for a in assets.values()
            if a.get("ref") == ref and isinstance(a.get("your_value"), int | float)
        ]
        out.append(min(vals) if vals else None)
    return out


def guard_deal(
    t: Terms,
    m: Market,
    me: Mapping[str, Any],
    team: str,
    ctx: Context,
    rules: Guardrails,
    notional: int,
    received: float,
) -> str | None:
    """Why the guardrails refuse this deal (None: every action passes)."""
    actions = deal_actions(t, m, team, notional, received)
    values = your_values(t, me)
    problems: list[str] = []
    for i, a in enumerate(actions):
        if a.kind == "sell":
            a = replace(a, item=_give_ref(t, me, i), your_value=values[i] if i < len(values) else None)
        problems += check(a, ctx, rules).violations
    return "; ".join(dict.fromkeys(problems)) or None


def _give_ref(t: Terms, me: Mapping[str, Any], i: int) -> str:
    assets = {int(a["id"]): a for a in me.get("assets") or [] if isinstance(a.get("id"), int)}
    if i < len(t.give_assets):
        return str((assets.get(t.give_assets[i]) or {}).get("ref") or "")
    return t.give_refs[i - len(t.give_assets)]


def notional_of(t: Terms, book: Mapping[str, float], me: Mapping[str, Any]) -> int:
    """The deal's notional for the counterparty share: the larger of the cash and the book of the cards."""
    assets = {int(a["id"]): a for a in me.get("assets") or [] if isinstance(a.get("id"), int)}
    refs = [str((assets.get(i) or {}).get("ref") or "") for i in t.give_assets] + list(t.give_refs) + list(t.get_refs)
    return max(t.give_cash + t.get_cash, round(sum(book.get(r, 0.0) for r in refs)))


def plan_from_trade(trade: Mapping[str, Any], venue: str = "rastro", floor: float = 2.0) -> TeamPlan:
    """A team plan from one of `bazaar trade-plan`'s trades (its JSON): our give/want as the opening."""
    give, want = trade.get("give") or {}, trade.get("want") or {}
    opening = Terms(
        tuple(int(a) for a in give.get("assets") or []),
        (),
        int(give.get("cash") or 0),
        tuple(str(c) for c in want.get("cards") or []),
        (),
        int(want.get("cash") or 0),
    )
    return TeamPlan(str(trade["counterparty"]), venue, opening, floor, expires_in_ticks=int(trade.get("expires") or 10))


def assets_for_accept(t: Terms, me: Mapping[str, Any], listed: Sequence[int] = ()) -> list[int]:
    """The copies we hand over when we accept their offer: the ids it names, then for each 'any copy of'
    the copy we lose least by (never one already in another open offer of ours)."""
    out = list(t.give_assets)
    for ref in t.give_refs:
        copies = sorted(
            (
                a
                for a in me.get("assets") or []
                if a.get("ref") == ref
                and a.get("kind") == "card"
                and isinstance(a.get("id"), int)
                and int(a["id"]) not in out
                and int(a["id"]) not in listed
            ),
            key=lambda a: (float(a.get("your_value") or 0), -int(a["id"])),
        )
        if copies:
            out.append(int(copies[0]["id"]))
    return out
