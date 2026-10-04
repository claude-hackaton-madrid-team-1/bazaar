"""`bazaar impact`: what selling, swapping away or buying one copy could do to our score, before it is sent.

Read-only: the guard's own estimate (`move_impact`, `max_score_loss_per_move` in GUARDRAILS.md) of a move a human is
about to make. /api/me gives our copies (each `your_value`), our complete pages and the tick; Postgres gives how we got
each copy and k (`impact_board.read_facts`, in a read-only transaction); a buy also reads GET /api/me/value. At most
two game requests per run, each sent once, and never a write. Facts that cannot be read price the move at the guard's
worst case: every copy counted as bought from a team, k at its fallback.
"""

from __future__ import annotations

import functools
import json
import math
from collections.abc import Callable
from typing import Any, NoReturn

import typer
from pydantic import ValidationError
from rich.console import Console
from rich.markup import escape

from bazaar_agent import impact_board, pgconn
from bazaar_agent import move_impact as mi
from bazaar_agent.approvals import CARD
from bazaar_agent.config import ConfigError, load_settings
from bazaar_agent.guardrails import Guardrails, GuardrailsError, load_guardrails
from bazaar_agent.official_values import CardValue
from bazaar_agent.sdk import BazaarError, team_client

console = Console()
err_console = Console(stderr=True)

SIDES = ("sell", "swap", "buy")
MAX_PRICE = 10_000_000  # RULES.md: whole primas from 1 to 10,000,000
CONNECT_TIMEOUT_S = 3
STATEMENT_TIMEOUT_MS = 5000  # a slow facts read fails (the worst case) instead of holding the terminal
WORST_CASE = "every copy counted as bought from a team, k at its fallback"
PRICE_HELP = (
    "what we get for the copy (sell), pay for it (buy, fee included), or receive for it "
    "(swap: the card we get at its value to us plus net cash)"
)

MeRead = Callable[[], Any]
ValueRead = Callable[[str], Any]
FactsRead = Callable[[int], mi.Facts | None]


def impact(
    side: str = typer.Argument(..., help="sell, swap (the copy we give away) or buy"),
    card: str = typer.Argument(..., help="the card ref, e.g. SAL-07"),
    price: float = typer.Argument(..., help=PRICE_HELP),
    to: str | None = typer.Option(
        None, "--to", help="sell/swap: a team id (t05) or a dealer (pilar). Default: anyone, a team trade"
    ),
    frm: str | None = typer.Option(
        None, "--from", help="buy: a team id (t05) or a dealer (abuela). Default: anyone, a team trade"
    ),
    asset: int | None = typer.Option(
        None, "--asset", help="sell/swap: which copy (asset id). Default: the copy that costs us most"
    ),
    as_json: bool = typer.Option(False, "--json", help="Print the estimate and the verdict as JSON (notes on stderr)"),
    tick: int | None = typer.Option(None, "--tick", help="the game tick of the facts (default: the tick in /api/me)"),
) -> None:
    """Read-only: the score impact of selling, swapping away or buying one copy, as the guard estimates it."""
    me_read, value_read = _game_readers()
    impact_cmd(side, card, price, _who(side, to, frm), asset, tick, as_json, me_read, value_read, postgres_facts)


def impact_cmd(
    side: str,
    ref: str,
    price: float,
    who: str | None,
    asset: int | None,
    tick: int | None,
    as_json: bool,
    me_read: MeRead,
    value_read: ValueRead,
    facts_read: FactsRead,
    rules: Guardrails | None = None,
) -> None:
    """Print the guard's estimate of one move (`as_json`: JSON on stdout, every note on stderr). Reads only."""
    ref = _checked(side, ref, price, asset)
    who = (who or "").strip().lower() or None
    rules = rules if rules is not None else _rules()
    me = _me(me_read)
    cards = mi.our_cards(me)
    facts = _facts(facts_read, tick if tick is not None else _tick_of(me), cards.team)
    fallback = rules.score_per_neg_point_fallback
    slope_ = facts.slope(fallback) if facts is not None else mi.fallback_slope(fallback)
    party = _counterparty(who)
    if side == "buy":
        value = _buy_value(value_read, ref)
        estimate = mi.estimate("buy", ref, price, value, party, slope_, rules.dealer_ladder_score)
    else:
        estimate = _sale(cards, ref, price, party, facts, rules, asset)
    verdict = _verdict(side, estimate, rules)
    if as_json:
        typer.echo(json.dumps(_payload(side, who, estimate, rules, verdict, facts is not None), indent=2))
    else:
        _show(side, who, estimate, slope_, rules, verdict)


# ---------------------------------------------------------------- reads (the game, Postgres, GUARDRAILS.md)


def _team() -> Any:
    """Our team client with no holdings write tracker and no re-send: this command only reads."""
    try:
        return team_client(load_settings(), track=False, retries=0)
    except ConfigError as e:  # names the missing setting, never its value
        _stop(escape(str(e)))


def _game_readers() -> tuple[MeRead, ValueRead]:
    """GET /api/me and GET /api/me/value on one team client, made at the first read: the only requests sent."""
    client = functools.cache(_team)
    return (lambda: client().me()), (lambda ref: client().value(ref))


def postgres_facts(tick: int) -> mi.Facts | None:
    """The guard's facts (`impact_board.read_facts`) in a read-only transaction: a write would raise."""
    with pgconn.connect(app="bazaar-impact-cli", connect_timeout_s=CONNECT_TIMEOUT_S) as conn:
        conn.read_only = True
        conn.execute(f"set statement_timeout = {STATEMENT_TIMEOUT_MS}")
        return impact_board.read_facts(conn, tick)


def _rules() -> Guardrails:
    try:
        return load_guardrails().rules
    except GuardrailsError as e:
        _stop(f"GUARDRAILS.md is invalid: {escape(str(e))}")


def _me(read: MeRead) -> dict[str, Any]:
    """GET /api/me: our copies, their your_value, our complete pages and the tick. Unreadable: exit 1."""
    try:
        me = read()
    except BazaarError as e:
        _stop(f"/api/me refused: {escape(e.code)} ({e.status})")
    if not isinstance(me, dict):
        _stop("/api/me answered something other than an object")
    return me


def _tick_of(me: dict[str, Any]) -> int | None:
    tick = me.get("tick")
    return tick if isinstance(tick, int) and not isinstance(tick, bool) else None


def _facts(read: FactsRead, tick: int | None, team: str | None) -> mi.Facts | None:
    """The guard's facts at `tick`, else None (the guard's worst case), said on stderr with why."""
    facts, why = _facts_or_why(read, tick, team)
    if why:
        _note(f"facts unreadable ({why}): {WORST_CASE}")
    return facts


def _facts_or_why(read: FactsRead, tick: int | None, team: str | None) -> tuple[mi.Facts | None, str]:
    if tick is None:
        return None, "no tick in /api/me: pass --tick"
    try:
        facts = read(tick)
    except Exception as e:  # the type only: a connect error can echo DATABASE_URL (.ai/memory.md)
        return None, type(e).__name__
    if facts is None:
        return None, "no /me snapshot in Postgres names us yet"
    if team is not None and facts.team != team:  # as `move_impact.sell_impact` rules: never another team's facts
        return None, f"the newest snapshot is {escape(facts.team)}'s, not ours ({team})"
    return facts, ""


def _buy_value(read: ValueRead, ref: str) -> float:
    """Our value of one more `ref` (GET /api/me/value, validated). Unknown: no estimate, exit 1."""
    try:
        answer = CardValue.model_validate(read(ref))
    except (BazaarError, ValidationError) as e:
        why = str(getattr(e, "code", None) or type(e).__name__)
    else:
        if answer.card == ref:
            return float(answer.your_value)
        why = f"the answer names {answer.card}"
    _stop(f"value of {ref} unknown ({escape(why)}, GET /api/me/value): no estimate without it")


# ---------------------------------------------------------------- the estimate and the verdict


def _checked(side: str, ref: str, price: float, asset: int | None) -> str:
    """The card ref, upper-cased. A bad move, card, price or --asset stops here (exit 2), before any read."""
    card = ref.strip().upper()
    if side not in SIDES:
        _usage(f"the move is sell, swap or buy, not {escape(side)}")
    if not CARD.match(card):
        _usage(f"not a card ref: {escape(ref)} (e.g. SAL-07)")
    if not (math.isfinite(price) and 1 <= price <= MAX_PRICE):
        _usage(f"the price is in primas, from 1 to {MAX_PRICE:,}")
    if side == "buy" and asset is not None:
        _usage("--asset names the copy a sale or a swap gives away; a buy has none")
    return card


def _who(side: str, to: str | None, frm: str | None) -> str | None:
    """--from names a buy's seller, --to the taker of a sale or a swap; the other one is a usage error."""
    if (side == "buy" and to is not None) or (side in ("sell", "swap") and frm is not None):
        _usage("a buy names its seller with --from; a sale or a swap its taker with --to")
    return frm if side == "buy" else to


def _counterparty(who: str | None) -> str | None:
    """The model's counterparty: anyone (no --to/--from) or a team id is a team trade, anything else a dealer
    (`move_impact.is_team`), named in the reason."""
    return mi.ANY_TEAM if who is None else who


def _sale(
    cards: mi.OurCards,
    ref: str,
    price: float,
    party: str | None,
    facts: mi.Facts | None,
    rules: Guardrails,
    asset: int | None,
) -> mi.Impact:
    """The guard's estimate for the copy that leaves: `asset`, else the copy of `ref` that costs us most."""
    held = cards.of(ref)
    if not held:
        _note(f"we hold no {ref} in /api/me: no value for the copy, the guard would refuse it")
    elif asset is not None and asset not in {c.asset for c in held}:
        _note(f"asset {asset} is not one of our {ref} copies in /api/me ({', '.join(str(c.asset) for c in held)})")
    rarity = held[0].rarity if held else None
    fallback, ladder = rules.score_per_neg_point_fallback, rules.dealer_ladder_score
    return mi.sell_impact(cards, ref, rarity, price, party, facts, fallback, ladder, asset)


def _verdict(side: str, estimate: mi.Impact, rules: Guardrails) -> str:
    """As `guardrails._impact_violations` rules: a buy is never checked; a sale below the bar (or unpriced) is
    refused unless a human approves it."""
    if side == "buy":
        return "informational"
    if rules.max_score_loss_per_move <= 0:
        return "off"
    if estimate.score is not None and estimate.score >= -rules.max_score_loss_per_move:
        return "within"
    return "refuse"


APPROVE_TTL_TICKS = 10  # an approval covers every sale of the card at or above its min until it lapses: keep it short


def _approve(estimate: mi.Impact) -> str:
    return (
        f"uv run bazaar approve {estimate.ref} --sell --min {math.floor(estimate.price)} "
        f"--ttl-ticks {APPROVE_TTL_TICKS}"
    )


def _payload(
    side: str, who: str | None, estimate: mi.Impact, rules: Guardrails, verdict: str, facts_read: bool
) -> dict[str, Any]:
    """`Impact.as_state()` (a decider's state) plus the move and the verdict."""
    return {
        **estimate.as_state(),
        "side": side,
        "card": estimate.ref,
        "price": estimate.price,
        "counterparty": who or mi.ANY_TEAM,
        "asset": estimate.asset,
        "value": estimate.value,
        "max_score_loss_per_move": rules.max_score_loss_per_move,
        "facts_read": facts_read,
        "verdict": verdict,
        "approve": _approve(estimate) if verdict == "refuse" else None,
    }


# ---------------------------------------------------------------- printing (every game or DB string escaped)


def _show(side: str, who: str | None, estimate: mi.Impact, slope_: mi.Slope, rules: Guardrails, verdict: str) -> None:
    party = escape(who) if who and who != mi.ANY_TEAM else "anyone on the board"
    move = f"{side} {estimate.ref} at {estimate.price:g} {'from' if side == 'buy' else 'to'} {party}"
    deal = "a team trade" if estimate.team_trade else "a dealer deal"
    lines = [
        f"[bold]{move}[/bold] · {deal}",
        f"  estimate: {_estimate_line(estimate, slope_, rules.dealer_ladder_score)}",
        f"  {'value' if side == 'buy' else 'copy'}: {_copy_line(side, estimate)}",
    ]
    if estimate.breaks_page:
        lines.append(f"  [bold red]BREAKS A COMPLETE PAGE[/bold red]: our only {estimate.ref} of a complete page")
    lines.append(f"  [dim]why: {escape(estimate.reason)}[/dim]")
    lines.append(f"  verdict: {_verdict_line(verdict, estimate, rules)}")
    for line in lines:
        console.print(line, soft_wrap=True, highlight=False)


def _estimate_line(estimate: mi.Impact, slope_: mi.Slope, ladder: float) -> str:
    """Score delta, neg_points delta, k and its source, and the ladder a dealer deal adds."""
    if estimate.score is None or estimate.neg_points is None:
        return "cannot be estimated (no value for the copy)"
    dn = estimate.neg_points
    extra = "" if estimate.team_trade else f" · dealer ladder +{ladder:g}"
    return f"score {estimate.score:+.2f} · neg_points {dn:+.1f} · {slope_.describe(dn)}{extra}"


def _copy_line(side: str, estimate: mi.Impact) -> str:
    """A buy: what one more copy is worth to us. A sale: which copy leaves, its your_value and how we got it."""
    value = "unknown" if estimate.value is None else f"{estimate.value:g}"
    if side == "buy":
        return f"one more {estimate.ref} is worth {value} to us (GET /api/me/value)"
    asset = "" if estimate.asset is None else f"asset {estimate.asset} · "
    worst = " · priced as a copy bought from a team (the worst case)" if estimate.assumed else ""
    return f"{asset}your_value {value} · {escape(estimate.origin.describe())}{worst}"


def _verdict_line(verdict: str, estimate: mi.Impact, rules: Guardrails) -> str:
    bar = rules.max_score_loss_per_move
    score = "unknown" if estimate.score is None else f"{estimate.score:+.2f}"
    if verdict == "refuse":
        cost = "cannot be estimated" if estimate.score is None else f"{score} < -{bar:g} = -max_score_loss_per_move"
        refuse = "[bold red]the guard would refuse this[/bold red]"
        return (
            f"{refuse} (score impact {cost}): approve it first with `{_approve(estimate)}` "
            "(this rule only: sell_min_value_ratio, protect_page_sets and the others still apply)"
        )
    return {
        "within": f"[green]within max_score_loss_per_move[/green] ({score} >= -{bar:g})",
        "off": "max_score_loss_per_move is 0 (off): the guard does not check the score impact",
    }.get(verdict, "informational: the guard checks sales only")


def _note(message: str) -> None:
    err_console.print(f"[yellow]{message}[/yellow]", soft_wrap=True, highlight=False)


def _stop(message: str) -> NoReturn:
    err_console.print(f"[red]{message}[/red]", soft_wrap=True, highlight=False)
    raise typer.Exit(1)


def _usage(message: str) -> NoReturn:
    err_console.print(message, soft_wrap=True, highlight=False)
    raise typer.Exit(2)
