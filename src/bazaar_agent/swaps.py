"""Team-to-team swap threads (N17), the pure part: how we concede on a planned swap, how we read a team's
counter, and the fairness check every proposal and every accept passes.

The swaps themselves come from the trade desk (`trade_desk.build_plan(...).threads`, #79): our copy for a
missing page card a team holds, priced on the rival affinity map at an even split of the expected pie
(`Trade.ours` + `Trade.theirs`), checked against GUARDRAILS and the per-team share of the plan. A thread
opens at an anchor (`Ladder.anchor_share` of the pie for us) and concedes once per message down to that
even split, never below it.

Words persuade, structure binds: a counter is read from its structured offer only, and anything that is
not the planned swap (other cards, a pack, an unknown key, cash on both sides) is not ours to take.
Fair play (RULES.md): we never hand a team most of a deal's value, and a repeat deal never hands us most of
theirs ("when one team keeps handing another the whole value of their deals, those deals count for nothing").
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from bazaar_agent.agents.market import SIDE_KEYS
from bazaar_agent.guardrails import Guardrails
from bazaar_agent.trade_desk import Trade


@dataclass(frozen=True)
class Ladder:
    anchor_share: float = 0.65  # our first proposal asks for this share of the expected pie
    steps: int = 3  # proposals from the anchor down to the even split (the plan's price), then it holds


def pie(t: Trade) -> float:
    """The expected pie of a planned swap: our gain plus theirs (the cash leg moves value, it adds none)."""
    return t.ours + t.theirs


def ours_before_cash(t: Trade) -> float:
    """Our gain from the cards alone: `Trade.ours` is this plus the cash leg (+ they add, − we add)."""
    return t.ours - t.price


def theirs_before_cash(t: Trade) -> float:
    """Their expected gain from the cards alone, net of the fee they pay as the accepting side."""
    return t.theirs + t.price


def cash_at(t: Trade, step: int, ladder: Ladder) -> int:
    """The cash leg of our `step`-th proposal (0 is the anchor): our share of the pie goes from
    `anchor_share` down to an even split at the last step, and stays there. + they add cash, − we do."""
    last = max(1, ladder.steps - 1)
    share = ladder.anchor_share - (ladder.anchor_share - 0.5) * min(step, last) / last
    return round(max(0.5, share) * pie(t) - ours_before_cash(t))


def offer_terms(t: Trade, cash: int) -> dict[str, Any]:
    """The structured offer of a swap from our side, `{give, want}`, with the cash on one side only."""
    give: dict[str, Any] = {"assets": [t.asset_id]}
    want: dict[str, Any] = {"cards": [t.refs[1]]}
    if cash < 0:
        give["cash"] = -cash
    elif cash > 0:
        want["cash"] = cash
    return {"give": give, "want": want}


# ---------------------------------------------------------------- reading a counter


@dataclass(frozen=True)
class TheirOffer:
    """A team's standing offer to us, from our side: what we would get and what we would hand over."""

    offer_id: int
    team: str
    get_assets: tuple[int, ...]  # their copies we receive
    get_refs: tuple[str, ...]
    cash_in: int  # they pay us
    give_assets: tuple[int, ...]  # our copies they name
    give_refs: tuple[str, ...]  # any copy of these cards of ours
    cash_out: int  # we pay them

    @property
    def net_cash(self) -> int:
        return self.cash_in - self.cash_out


def _plain_side(side: Any) -> bool:
    return isinstance(side, dict) and all(k in SIDE_KEYS or value in (None, 0, [], {}, "") for k, value in side.items())


def _whole(x: Any) -> int | None:
    return x if isinstance(x, int) and not isinstance(x, bool) and x >= 0 else None


def read_offer(o: Any, us: str) -> TheirOffer | None:
    """A team's open offer addressed to us, read from its structure only; None for anything else (our own
    offer, an offer to another team, a dealer's, unknown keys, packs, cash on both sides, nothing given)."""
    if not isinstance(o, dict) or not isinstance(o.get("id"), int) or o.get("status") not in (None, "open"):
        return None
    maker = o.get("maker")
    if not isinstance(maker, str) or maker == us or o.get("to") != us:
        return None
    give, want = o.get("give"), o.get("want")
    if not (isinstance(give, dict) and isinstance(want, dict) and _plain_side(give) and _plain_side(want)):
        return None
    if give.get("cards") or give.get("types"):  # a giver names its own copies
        return None
    got = give.get("assets") or []
    if not all(isinstance(a, dict) and isinstance(a.get("id"), int) and a.get("kind", "card") == "card" for a in got):
        return None
    raw = [a.get("id") if isinstance(a, dict) else a for a in want.get("assets") or []]
    named = [a for a in raw if isinstance(a, int) and not isinstance(a, bool)]
    wanted = [str(t).split(":", 1)[-1] for t in (want.get("types") or []) + (want.get("cards") or [])]
    if len(named) != len(raw) or any(str(t).startswith("pack:") for t in want.get("types") or []):
        return None
    cash_in, cash_out = _whole(give.get("cash") or 0), _whole(want.get("cash") or 0)
    if cash_in is None or cash_out is None or (cash_in and cash_out) or not (got or cash_in):
        return None
    return TheirOffer(
        int(o["id"]),
        maker,
        tuple(int(a["id"]) for a in got),
        tuple(str(a.get("ref")) for a in got),
        cash_in,
        tuple(named),
        tuple(wanted),
        cash_out,
    )


def is_the_planned_swap(o: TheirOffer, t: Trade) -> bool:
    """Their offer moves exactly the planned cards: their one copy of the card we want, for our planned copy
    (named, or as any copy of its card), with cash either way."""
    ours = (o.give_assets == (t.asset_id,) and not o.give_refs) or (not o.give_assets and o.give_refs == t.refs[:1])
    return o.team == t.counterparty and o.get_refs == t.refs[1:] and len(o.get_assets) == 1 and ours


# ---------------------------------------------------------------- fairness


@dataclass(frozen=True)
class SwapVerdict:
    ok: bool
    ours: float
    theirs: float
    reason: str


def judge(t: Trade, net_cash: int, fee_ours: int, rules: Guardrails, *, repeat: bool = False) -> SwapVerdict:
    """The planned cards at `net_cash` (+ they pay us, − we pay them): our gain, their expected gain and the
    fairness band. `fee_ours` is the venue fee when WE accept (a counter); our own proposal is accepted by
    them, who pay it (already in `Trade.theirs`). `repeat`: we already settled a deal with this team."""
    ours = ours_before_cash(t) + net_cash - fee_ours
    theirs = theirs_before_cash(t) + (t.fee if fee_ours else 0) - net_cash
    total = ours + theirs
    if ours < rules.team_swap_min_surplus:
        return SwapVerdict(
            False, ours, theirs, f"our gain {ours:.1f} < team_swap_min_surplus {rules.team_swap_min_surplus}"
        )
    if total <= 0:
        return SwapVerdict(False, ours, theirs, f"no pie ({total:.1f})")
    if theirs / total > rules.team_swap_max_their_share:
        return SwapVerdict(
            False,
            ours,
            theirs,
            f"feeds {t.counterparty}: {theirs / total:.0%} of the pie > team_swap_max_their_share "
            f"{rules.team_swap_max_their_share:g}",
        )
    if repeat and ours / total > rules.team_swap_max_our_share:
        return SwapVerdict(
            False,
            ours,
            theirs,
            f"a repeat deal handing us {ours / total:.0%} of the pie > team_swap_max_our_share "
            f"{rules.team_swap_max_our_share:g}",
        )
    return SwapVerdict(True, ours, theirs, f"+{ours:.1f} to us, ~+{theirs:.1f} to {t.counterparty}")
