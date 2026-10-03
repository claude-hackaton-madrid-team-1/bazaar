"""What the agent runtime may write, and the guardrail gate every write passes first.

Each write is the code path of a CLI command: `dealer_buy` (`bazaar dealer buy`), `sell_list` and
`sell_bid` (`bazaar sell list|bid` → `seller.post`), `sell_cancel` (`bazaar sell cancel`), `duel_move`
(one move of `bazaar duel run --play`), and `steer` (the clamp of `bazaar steer`, without its LLM call).
All of them are DRY RUN unless BAZAAR_LIVE=1 is in the process environment (the taker/maker rule).

`check_write()` is the gate: the tool code calls it before it acts (first line of defense) and the
desk's PreToolUse hook calls it again (second line). On top of `guardrails.check()` a live write meets
the game's own caps before it is sent, so a client of ours never earns a 429 for the team: the tick
budget, one thread per dealer and at most `max_open_threads_per_team`, `offers_per_team_per_tick` new
listings (counted team-wide in the shared ledger), `max_open_offers_per_team`, one accept per tick.
"""

from __future__ import annotations

import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from bazaar_agent.agents.dealer import BidPlan, bid_schedule
from bazaar_agent.agents.seller import (
    Listing,
    OfferError,
    bid_listing,
    committed_context,
    post,
    sell_listing,
)
from bazaar_agent.config import REPO_ROOT
from bazaar_agent.guardrails import Action, Context, Verdict, check, context_from
from bazaar_agent.llm.steering import SteerDelta
from bazaar_agent.runtime.backend import Backend
from bazaar_agent.ticks import Clock, action_budget_s

ITEM = r"^([A-Z]{3}-[0-9]{2}|sobre_[a-z_]+)$"  # a card ref (LAV-09) or a pack id (sobre_barrio)
CARD = r"^[A-Z]{3}-[0-9]{2}$"
SLUG = r"^[a-z0-9][a-z0-9_-]{0,31}$"  # dealer, venue: never starts with "-" (it lands in an argv)
MAX_DEALER_PRICE = 1000
MAX_OFFER_PRICE = 10_000_000  # RULES.md: whole primas from 1 to 10,000,000


class Args(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class DealerBuyArgs(Args):
    item: str = Field(pattern=ITEM, description="Card ref (LAV-03) or pack id (sobre_barrio)")
    max_price: int = Field(ge=1, le=MAX_DEALER_PRICE, description="Hard limit: never pay above this")
    start: int = Field(ge=1, le=MAX_DEALER_PRICE, description="Opening bid (dealers move only when we move)")
    step: int = Field(default=1, ge=1, le=50, description="Raise per tick (small steps earn small steps)")
    dealer: str = Field(default="abuela", pattern=SLUG, description="Dealer id")

    @model_validator(mode="after")
    def _start_inside_max(self) -> DealerBuyArgs:
        if self.start > self.max_price:
            raise ValueError(f"start {self.start} is above max_price {self.max_price}")
        return self


class SellListArgs(Args):
    target: str = Field(pattern=r"^([0-9]{1,9}|[A-Z]{3}-[0-9]{2})$", description="Asset id, or a card ref")
    price: int = Field(ge=1, le=MAX_OFFER_PRICE, description="Cash we want for it")
    venue: str = Field(default="rastro", pattern=SLUG)
    expires: int = Field(default=40, ge=1, le=240, description="Ticks the offer stays open")


class SellBidArgs(Args):
    ref: str = Field(pattern=CARD, description="Card ref we want (any copy)")
    price: int = Field(ge=1, le=MAX_OFFER_PRICE, description="Cash we offer")
    venue: str = Field(default="rastro", pattern=SLUG)
    expires: int = Field(default=40, ge=1, le=240, description="Ticks the offer stays open")


class SellCancelArgs(Args):
    offer_id: int = Field(ge=1, description="One of our open offers")


class DuelMoveArgs(Args):
    duel_id: int = Field(ge=1, description="A live duel from /api/duels")


class SteerArgs(Args):
    text: str = Field(min_length=1, max_length=500, description="The operator's style instruction, verbatim")
    summary: str = Field(min_length=1, max_length=300, description="One sentence: what changes and why")
    ttl_ticks: int = Field(ge=1, le=1000, description="How many game ticks the change lasts")
    deltas: list[SteerDelta] = Field(min_length=1, max_length=7, description="Parameters and signed changes")


@dataclass(frozen=True)
class Planned:
    """One write as judged before anything is sent: the guardrail verdict and what the executor reuses."""

    tool: str
    verdict: Verdict
    clock: Clock
    read_at: float  # time.monotonic() when the clock was read: the tick budget runs from there
    action: Action | None = None  # None: not a trade (cancel, steer)
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def tick(self) -> int:
        return self.clock.tick

    def budget_left(self, now: Callable[[], float] = time.monotonic) -> float:
        return action_budget_s(self.clock) - (now() - self.read_at)


def _denied(*reasons: str) -> Verdict:
    return Verdict(False, tuple(reasons))


def kill_switch(b: Backend) -> list[str]:
    """`touch .local/PAUSE` or `trading_enabled = false` stops every write, cancels included."""
    reasons = [] if b.rules.trading_enabled else ["trading_enabled = false"]
    if (REPO_ROOT / b.rules.pause_file).exists():
        reasons.append(f"pause file {b.rules.pause_file} exists")
    return reasons


@dataclass(frozen=True)
class Base:
    """Album first: /me, our open offers, the plain guardrail context and what the offers promise."""

    me: dict[str, Any]
    offers: list[dict[str, Any]]
    ctx: Context
    commitments: Any


def _base(b: Backend, clock: Clock) -> Base:
    me, offers = b.team.me(), b.my_offers()
    ctx = context_from(me, clock.tick, clock.t_hours, b.ledger, b.rules)
    return Base(me, offers, ctx, b.commitments(me, offers))


def _plan_listing(
    b: Backend, clock: Clock, read_at: float, build: Callable[[dict[str, Any]], Listing], name: str
) -> Planned:
    base = _base(b, clock)
    try:
        listing = build(base.me)
    except OfferError as e:
        return Planned(name, _denied(str(e)), clock, read_at)
    if listing.asset_id is not None and listing.asset_id in base.commitments.listed:
        refused = _denied(f"asset {listing.asset_id} is already in one of our open offers")
        return Planned(name, refused, clock, read_at)
    verdict = check(listing.action(), committed_context(base.ctx, base.commitments), b.rules)
    detail = {"listing": listing, "base": base}
    return Planned(name, verdict, clock, read_at, listing.action(), detail)


def _plan_dealer(b: Backend, clock: Clock, read_at: float, args: DealerBuyArgs) -> Planned:
    """A buy at `max_price`, with one live `dealer buy` from this process at a time: a child checks
    the guardrails per move against the live /me, so two children would each count the same cash."""
    running = b.dealer_running()
    if running is not None:
        refused = _denied(f"a live dealer negotiation with {running} is still running from this runtime")
        return Planned("dealer_buy", refused, clock, read_at)
    action = Action("buy", args.item, b.rarity_of(args.item, clock.tick), args.max_price, dealer=args.dealer)
    base = _base(b, clock)
    verdict = check(action, committed_context(base.ctx, base.commitments), b.rules)
    return Planned("dealer_buy", verdict, clock, read_at, action)


def _plan_duel(b: Backend, clock: Clock, read_at: float, args: DuelMoveArgs) -> Planned:
    from bazaar_agent.agents.duelist import duel_id, duel_move
    from bazaar_agent.llm.steering import STEERING_FILE, steered_duel_params

    duels = [d for d in b.team.duels().get("duels") or [] if isinstance(d, dict)]
    duel = next((d for d in duels if duel_id(d) == args.duel_id), None)
    if duel is None:
        return Planned("duel_move", _denied(f"duel {args.duel_id} is not one of our live duels"), clock, read_at)
    if (args.duel_id, clock.tick) in b.duel_said:
        return Planned("duel_move", _denied(f"already moved in duel {args.duel_id} this tick"), clock, read_at)
    anchor, floor = steered_duel_params(b.rules, b.settings.data_dir / STEERING_FILE, clock.tick)
    started = duel.get("started_tick") or duel.get("created_tick") or clock.tick  # unknown: open at the anchor
    move = duel_move(
        duel, clock.tick, int(started), anchor=anchor, floor=floor, endgame_ticks=b.rules.duel_endgame_ticks
    )
    if move.kind == "hold":
        return Planned("duel_move", Verdict(True), clock, read_at, None, {"duel": duel, "move": move})
    ctx = Context(
        cash=0,
        held={},
        tick=clock.tick,
        t_hours=clock.t_hours,
        accepts_this_tick=b.ledger.accepts_in_tick(clock.tick),
        paused=(REPO_ROOT / b.rules.pause_file).exists(),
    )
    action = Action("duel_accept" if move.kind == "accept" else "duel_offer", str(args.duel_id))
    detail = {"duel": duel, "move": move}
    return Planned("duel_move", check(action, ctx, b.rules), clock, read_at, action, detail)


def check_write(b: Backend, tool: str, args: BaseModel) -> Planned:
    """The guardrail verdict for one write with the live /me, clock, open offers and shared ledger.

    Raises on a read failure: the callers fail closed (no write without a fresh view of what we hold).
    """
    b.flush_pending()  # our own sends first: never judge on totals that miss them
    clock, read_at = b.clock(), time.monotonic()
    if isinstance(args, DealerBuyArgs):
        return _plan_dealer(b, clock, read_at, args)
    if isinstance(args, SellListArgs):
        build_sell = lambda me: sell_listing(me, args.target, args.price, args.venue)  # noqa: E731
        return _plan_listing(b, clock, read_at, build_sell, tool)
    if isinstance(args, SellBidArgs):
        rarity = b.rarity_of(args.ref, clock.tick)
        build_bid = lambda me: bid_listing(args.ref, rarity, args.price, args.venue)  # noqa: E731
        return _plan_listing(b, clock, read_at, build_bid, tool)
    if isinstance(args, SellCancelArgs):
        stops = kill_switch(b)
        return Planned(tool, _denied(*stops) if stops else Verdict(True), clock, read_at)
    if isinstance(args, DuelMoveArgs):
        return _plan_duel(b, clock, read_at, args)
    if isinstance(args, SteerArgs):  # style, not a trade (clamped by steering.clamp), but still a write
        stops = kill_switch(b)
        return Planned(tool, _denied(*stops) if stops else Verdict(True), clock, read_at)
    raise TypeError(f"no guardrail plan for {type(args).__name__}")


# ---------------------------------------------------------------- executing a write


def outcome(planned: Planned, status: str, **fields: Any) -> dict[str, Any]:
    """The answer every write returns (the hooks record it in `decisions` / `executions`)."""
    return {
        "tool": planned.tool,
        "tick": planned.tick,
        "status": status,  # approved (dry run) | done | failed | rejected | expired | hold
        "sent": False,
        "guardrail": str(planned.verdict),
        **fields,
    }


def _book(b: Backend, entries: list[tuple[str, int, float, int, str]]) -> dict[str, str]:
    """Ledger rows for a request that WAS sent. A failure here never turns a sent request into "not
    sent": the answer stays `done`, with the bookkeeping error next to it, and the rows wait for the
    next write (`Backend.flush_pending`), which approves nothing until they are in."""
    error = b.book(entries)
    return {"bookkeeping_error": error} if error else {}


def _dealer_buy(b: Backend, args: DealerBuyArgs, planned: Planned, clock: Clock | None) -> dict[str, Any]:
    command = (
        f"uv run bazaar dealer buy {args.item} --max {args.max_price} --start {args.start} "
        f"--step {args.step} --dealer {args.dealer}"
    )
    topic = {"buy": {"pack": args.item}} if "-" not in args.item else {"buy": {"card": args.item}}
    would = {
        "dealer": args.dealer,
        "topic": topic,
        "bids": bid_schedule(BidPlan(args.start, args.step, args.max_price)),
    }
    if clock is None:
        return outcome(planned, "approved", dry_run=True, request=would, command=command)
    if b.server:  # one place starts live negotiations (the desk or the CLI): two could count the same cash
        why = "live dealer negotiations start from the desk or the CLI, never from the remote server"
        return outcome(planned, "rejected", reason=why, request=would, command=command)
    open_threads = b.team.my_threads("open").get("threads") or []
    busy = [t for t in open_threads if t.get("with") == args.dealer]
    if busy:
        return outcome(
            planned, "rejected", reason=f"already negotiating with {args.dealer} (thread {busy[0].get('id')})"
        )
    if len(open_threads) >= clock.limits.max_open_threads_per_team:
        return outcome(planned, "rejected", reason=f"{len(open_threads)} open threads: the team's cap is reached")
    log_path = b.settings.data_dir / "runtime" / f"dealer-{args.dealer}-{args.item}-t{clock.tick}.log"
    argv = [sys.executable, "-m", "bazaar_agent.cli", *command.split()[3:], "--live"]
    child = b.spawn(argv, log_path)
    b.dealer_runs[args.dealer] = child
    response = {"pid": child.pid, "log": log_path.name}
    return outcome(planned, "done", sent=True, method="spawn dealer buy --live", request=would, response=response)


def _listing_caps(b: Backend, clock: Clock, offers: list[dict[str, Any]]) -> str | None:
    listed = b.ledger.count_in_tick("listing", clock.tick)
    if listed >= clock.limits.offers_per_team_per_tick:
        return f"{listed} listing(s) this tick: offers_per_team_per_tick {clock.limits.offers_per_team_per_tick}"
    open_offers = [o for o in offers if o.get("status") in (None, "open", "queued")]
    if len(open_offers) >= clock.limits.max_open_offers_per_team:
        return f"{len(open_offers)} open offers: max_open_offers_per_team {clock.limits.max_open_offers_per_team}"
    return None


def _listing(b: Backend, args: SellListArgs | SellBidArgs, planned: Planned, clock: Clock | None) -> dict[str, Any]:
    """`seller.post`, exactly as `bazaar sell list|bid` calls it: live only with a clock (BAZAAR_LIVE=1)."""
    listing: Listing = planned.detail["listing"]
    flag = "list" if isinstance(args, SellListArgs) else "bid"
    target = args.target if isinstance(args, SellListArgs) else args.ref
    command = f"uv run bazaar sell {flag} {target} --price {args.price} --venue {args.venue} --expires {args.expires}"
    request = {"give": listing.give, "want": listing.want, "venue": listing.venue, "expires_in_ticks": args.expires}
    base: Base = planned.detail["base"]
    if clock is not None and (cap := _listing_caps(b, clock, base.offers)) is not None:
        return outcome(planned, "rejected", reason=cap, request=request)
    from bazaar_agent.sdk import BazaarError

    try:  # no ledger handed to `post`: its spend row is written below, where a failure cannot hide the send
        posted = post(
            b.team,
            listing,
            base.ctx,
            b.rules,
            live=clock is not None,
            expires_in_ticks=args.expires,
            commitments=base.commitments,
        )
    except BazaarError as e:
        return outcome(planned, "failed", method="list_offer", request=request, error_code=e.code)
    if not posted.verdict.allowed:  # `post` re-checked the guardrails and refused
        return outcome(planned, "rejected", reason=posted.message, request=request)
    if clock is None:
        return outcome(planned, "approved", dry_run=True, request=request, would=posted.message, command=command)
    rows = [("listing", clock.tick, clock.t_hours, listing.price, listing.ref)]
    if listing.kind == "bid":  # an open bid can fill on any later tick: its cash counts as spent now
        rows.append(("spend", clock.tick, clock.t_hours, listing.price, listing.ref))
    booked = _book(b, rows)
    return outcome(planned, "done", sent=True, method="list_offer", request=request, response=posted.offer, **booked)


def _cancel(b: Backend, args: SellCancelArgs, planned: Planned, clock: Clock | None) -> dict[str, Any]:
    ours = {o.get("id"): o for o in b.my_offers()}
    offer = ours.get(args.offer_id)
    if offer is None or offer.get("status") not in (None, "open", "queued"):
        return outcome(planned, "rejected", reason=f"offer {args.offer_id} is not one of our open offers")
    request = {"offer_id": args.offer_id}
    if clock is None:
        return outcome(
            planned, "approved", dry_run=True, request=request, command=f"uv run bazaar sell cancel {args.offer_id}"
        )
    from bazaar_agent.sdk import BazaarError

    try:
        response = b.team.cancel(args.offer_id)
    except BazaarError as e:
        return outcome(planned, "failed", method="cancel", request=request, error_code=e.code)
    return outcome(
        planned, "done", sent=True, method="cancel", request=request, response=response, **_refund(b, offer, clock)
    )


def _refund(b: Backend, offer: dict[str, Any], clock: Clock) -> dict[str, str]:
    """A withdrawn bid gives its spend back, in the game hour it was spent: a refund booked now would
    make this hour's spend negative and loosen max_spend_per_game_hour."""
    give, want = offer.get("give") or {}, offer.get("want") or {}
    wanted = want.get("cards") or want.get("types")
    if not give.get("cash") or not wanted:
        return {}
    created = offer.get("created_tick")
    ticks_ago = clock.tick - created if isinstance(created, int) and created <= clock.tick else 0
    t_hours = clock.t_hours - ticks_ago * clock.tick_seconds / 3600
    tick = clock.tick - ticks_ago
    return _book(b, [("spend", tick, t_hours, -int(give["cash"]), str(wanted[0]).split(":")[-1])])


def _duel(b: Backend, args: DuelMoveArgs, planned: Planned, clock: Clock | None) -> dict[str, Any]:
    from bazaar_agent.agents.duelist import DUEL_WORDS

    move = planned.detail["move"]
    request = {"duel": args.duel_id, "kind": move.kind, "price": move.price, "days": move.days, "reason": move.reason}
    if move.kind == "hold":
        return outcome(planned, "hold", request=request, reason=move.reason)
    if clock is None:
        return outcome(planned, "approved", dry_run=True, request=request, command="uv run bazaar duel run --play")
    from bazaar_agent.sdk import BazaarError

    limit = min(b.rules.max_accepts_per_tick, clock.limits.accepts_per_team_per_tick)
    if move.kind == "accept" and not b.ledger.reserve_accept(
        clock.tick, clock.t_hours, 0, f"duel:{args.duel_id}", limit
    ):
        return outcome(planned, "rejected", reason="another process took the team's accept this tick", request=request)
    method = "duel_accept" if move.kind == "accept" else "duel_say"
    b.duel_said.add((args.duel_id, clock.tick))  # one message per duel per tick, sent or refused
    try:
        if move.kind == "accept":
            response = b.team.duel_accept(args.duel_id)
        else:
            response = b.team.duel_say(args.duel_id, DUEL_WORDS, price=move.price, days=move.days)
    except BazaarError as e:
        return outcome(planned, "failed", method=method, request=request, error_code=e.code)
    return outcome(planned, "done", sent=True, method=method, request=request, response=response)


def _steer(b: Backend, args: SteerArgs, planned: Planned, clock: Clock | None) -> dict[str, Any]:
    from bazaar_agent.llm.intent import Clarification
    from bazaar_agent.llm.steering import (
        STEERING_FILE,
        SteerDraft,
        apply_steering,
        base_params,
        save_steering,
        steering_from_draft,
    )

    draft = SteerDraft(deltas=list(args.deltas), ttl_ticks=args.ttl_ticks, summary=args.summary, question=None)
    steering = steering_from_draft(draft, args.text, planned.tick, b.rules, "desk")
    if isinstance(steering, Clarification):
        return outcome(planned, "rejected", reason=steering.question)
    base = base_params(b.rules)
    applied = apply_steering(base, steering, planned.tick, b.rules)
    preview = [
        {"param": p, "base": base[p], "delta": d, "applied": applied[p]}
        for p, d in steering.deltas.items()
        if p in base
    ]
    request = {"summary": steering.summary, "ticks": [steering.created_tick, steering.expires_tick], "preview": preview}
    if clock is None:
        return outcome(planned, "approved", dry_run=True, request=request, command="uv run bazaar steer --show")
    if b.server:  # steering.json lives on each machine's volume: saving it here would steer nothing
        why = "the remote server previews steering only: run `bazaar steer` where the agents run"
        return outcome(planned, "approved", dry_run=True, request=request, reason=why)
    save_steering(b.settings.data_dir / STEERING_FILE, steering)
    return outcome(
        planned, "done", sent=True, method="save steering", request=request, response={"saved": STEERING_FILE}
    )


EXECUTORS: dict[type[BaseModel], Callable[..., dict[str, Any]]] = {
    DealerBuyArgs: _dealer_buy,
    SellListArgs: _listing,
    SellBidArgs: _listing,
    SellCancelArgs: _cancel,
    DuelMoveArgs: _duel,
    SteerArgs: _steer,
}


def run_write(b: Backend, tool: str, args: BaseModel) -> dict[str, Any]:
    """Check, then act: a dry run says what would be sent; a live write sends only inside this tick's
    action budget, counted from the clock read of the check. One write at a time per process."""
    with b.write_lock:
        planned = check_write(b, tool, args)
        if not planned.verdict.allowed:
            return outcome(planned, "rejected", reason=str(planned.verdict))
        if not b.live:
            return EXECUTORS[type(args)](b, args, planned, None)
        if planned.budget_left() <= 0:
            return outcome(planned, "expired", reason=f"no time left in tick {planned.tick}: try again next tick")
        return EXECUTORS[type(args)](b, args, planned, planned.clock)
