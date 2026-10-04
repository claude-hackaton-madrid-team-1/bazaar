"""The human-only MCP tools (HA2): read the approval requests, approve or revoke a big trade.

An approval (`approvals.py`, HA1) is the human's veto over our own agents, so no agent may ever grant one:
- these specs are NOT in `tools.TOOLS`, so the desk's in-process server, its subagents' allow-lists, the desk's
  hooks and `bazaar agent chat` never see them;
- only the remote server (`runtime.mcp_server`) serves them, and only on a request that carries
  `X-Approver-Token` equal to BAZAAR_APPROVER_TOKEN, a second secret beside the bearer token. Unset, too weak or
  equal to the bearer token, the tools are not even listed (fail closed); the bearer alone never shows them. An
  approver request sees ONLY these three tools, so counterparty text never shares a context with `approve`.

Every input is validated (a card of the catalog, a price in [1, 1000], a ttl in [1, 480] ticks), and an approval the
agents could never use is refused. A buy approval of an off-page card (an epic or legendary) is also an ORDER: with
`buy_targets_enabled` the agents pursue it as a buy target (`buy_targets.py`: the maker's bid ladder, the taker's
accepts), always strictly below our value (`off_page_min_surplus`); the answer says the ladder. An approval lifts
`human_approval_above` and, for a sell, `max_score_loss_per_move` (MI1 asks for the same approval); never a hard cap
(the rarity cap, the official value, the hourly spend cap), never `protect_page_sets` (a page's last copy) and never a
sell below `sell_min_value_ratio` × our value. Reads fail closed: an unreadable /me, catalog or official value
refuses. Every call that changes something writes a `decisions` row (agent `guard`): approval_granted,
approval_refused, approval_revoked or approval_denied. `by` is "human:mcp", or "human:<via>" when the caller names its
relay (a slug: one approver token, so the relay's name is the caller's word, never free text). A revoke never waits
for a game read: an approve still checking when a revoke of its card and side comes in is refused at its write.
"""

from __future__ import annotations

import contextlib
import math
import threading
from collections.abc import Callable, Iterable, Iterator
from dataclasses import asdict
from typing import Any, Literal, Protocol

import psycopg
from pydantic import Field

from bazaar_agent import approvals, buy_targets
from bazaar_agent.approvals import Approval
from bazaar_agent.guardrails import OFF_PAGE_RARITIES
from bazaar_agent.llm.intent import lowest_value_copy
from bazaar_agent.runtime import actions as ac
from bazaar_agent.runtime.backend import Backend
from bazaar_agent.runtime.tools import ToolSpec, safe_text

APPROVER_VARIABLE = "BAZAAR_APPROVER_TOKEN"
APPROVER_HEADER = b"x-approver-token"
WRITES_PER_MINUTE = 10
PRICE_MIN, PRICE_MAX = 1, 1000
TTL_MIN, TTL_MAX = 1, 480
VIA = r"^[a-z][a-z0-9-]{0,23}$"  # who relays the human's click (bazaar-live): part of `by`, never free text
STATEMENT_TIMEOUT_MS = 3000
REVOKE_CLOCK_BUDGET_S = 2.0  # a revoke needs the tick only for its audit row: it never waits longer for the game
_WRITES = threading.Lock()  # held only around the store writes, never across a game read
_REVOKES: dict[tuple[str, str], int] = {}  # (card, side) -> revokes in this process: an approve checks it last


class ApprovalsArgs(ac.Args):
    pass


class ApproveArgs(ac.Args):
    card: str = Field(pattern=ac.CARD, description="The card ref, e.g. SAL-09")
    side: Literal["buy", "sell"]
    price: int = Field(
        ge=PRICE_MIN, le=PRICE_MAX, description="buy: the most to pay, fee included; sell: the least to take"
    )
    ttl_ticks: int = Field(default=approvals.DEFAULT_TTL_TICKS, ge=TTL_MIN, le=TTL_MAX, description="Ticks it lasts")
    reason: str = Field(default="", max_length=300, description="Why (stored with it)")
    via: str | None = Field(default=None, pattern=VIA, description="The relay of the human's click, e.g. bazaar-live")


class RevokeArgs(ac.Args):
    card: str = Field(pattern=ac.CARD, description="The card ref, e.g. SAL-09")
    side: Literal["buy", "sell"]
    reason: str = Field(default="", max_length=300, description="Why (stored with it)")
    via: str | None = Field(default=None, pattern=VIA, description="The relay of the human's click, e.g. bazaar-live")


class ApprovalStore(Protocol):
    """Where approvals live: `PgApprovalStore` on the shared Postgres; tests use a list."""

    def active(self, tick: int) -> list[Approval]: ...
    def pending(self, since_tick: int) -> list[dict[str, Any]]: ...
    def denials(self, since_tick: int) -> list[tuple[str, str, int]]: ...
    def approve(self, card: str, side: str, price: int, tick: int, until_tick: int, by: str, reason: str) -> Approval:
        """Write the approval and its `approval_granted` row on one connection."""
        ...

    def revoke(self, card: str, side: str, tick: int | None, by: str, reason: str) -> bool:
        """Remove the approval and write `approval_revoked`, or `approval_denied` when there was none (the reason
        defaults to "revoked" / "denied")."""
        ...

    def record(self, kind: str, tick: int, inputs: dict[str, Any]) -> None: ...


class PgApprovalStore:
    """`approvals.py` on the shared Postgres, one short connection per call (the server answers a human, rarely)."""

    def __init__(self, connect: Callable[[], psycopg.Connection] | None = None) -> None:
        self._connect = connect or _default_connect

    @contextlib.contextmanager
    def _conn(self) -> Iterator[psycopg.Connection]:
        with self._connect() as conn:
            conn.execute(f"set statement_timeout = {STATEMENT_TIMEOUT_MS}")
            yield conn

    def active(self, tick: int) -> list[Approval]:
        with self._conn() as conn:
            return approvals.active(conn, tick)

    def pending(self, since_tick: int) -> list[dict[str, Any]]:
        with self._conn() as conn:
            return approvals.pending(conn, since_tick)

    def denials(self, since_tick: int) -> list[tuple[str, str, int]]:
        with self._conn() as conn:
            return approvals.denials(conn, since_tick)

    def approve(self, card: str, side: str, price: int, tick: int, until_tick: int, by: str, reason: str) -> Approval:
        with self._conn() as conn:
            a = approvals.approve(conn, card, side, price, until_tick, by, reason)
            audit = {"card": card, "side": side, "price": price, "until_tick": until_tick, "by": by, "reason": reason}
            approvals.record(conn, "approval_granted", tick, audit)
            return a

    def revoke(self, card: str, side: str, tick: int | None, by: str, reason: str) -> bool:
        with self._conn() as conn:
            was = approvals.revoke(conn, card, side)
            kind = "approval_revoked" if was else "approval_denied"
            audit = {"card": card, "side": side, "by": by, "reason": reason or kind.removeprefix("approval_")}
            approvals.record(conn, kind, tick, audit)
            return was

    def record(self, kind: str, tick: int, inputs: dict[str, Any]) -> None:
        with self._conn() as conn:
            approvals.record(conn, kind, tick, inputs)


def _default_connect() -> psycopg.Connection:
    from bazaar_agent import pgconn
    from bazaar_agent.breakers import CONNECT_TIMEOUT_S

    return pgconn.connect(app="bazaar-mcp-approvals", connect_timeout_s=CONNECT_TIMEOUT_S)


# ---------------------------------------------------------------- what an approval may cover


def _copies(me: dict[str, Any], card: str) -> int:
    return sum(1 for a in me.get("assets") or [] if a.get("kind") == "card" and a.get("ref") == card)


def is_page_card(rarity: str | None) -> bool:
    return str(rarity or "").strip().lower() not in OFF_PAGE_RARITIES


def buy_cap(b: Backend, rarity: str | None) -> tuple[int, str] | None:
    """The most any buy of this rarity may pay, whatever a human approves: the rarity cap (lifted for a dealer's
    final by `dealer_final_lift`, commons to rares only), else None (buying this rarity is not allowed)."""
    cap = b.rules.final_cap_for(rarity)
    if cap is None:
        return None
    return cap, f"max_price_{rarity}"


def album(me: dict[str, Any] | None, card: str, rarity: str | None) -> dict[str, Any] | None:
    if me is None:
        return None
    held, page_card = _copies(me, card), is_page_card(rarity)
    return {"set": card[:3], "held": held, "page_card": page_card, "last_copy": page_card and held == 1}


def buy_refusals(b: Backend, card: str, price: int, rarity: str, tick: int, me: dict[str, Any]) -> list[str]:
    rules, out = b.rules, []
    cap = buy_cap(b, rarity)
    if cap is None:
        out.append(f"no max_price for rarity {rarity}: buying it is not allowed")
    elif price > cap[0]:
        out.append(f"{cap[1]} {cap[0]}: an approval never lifts it")
    if price > rules.max_spend_per_game_hour:
        out.append(f"max_spend_per_game_hour {rules.max_spend_per_game_hour}: an approval never lifts it")
    if not is_page_card(rarity) and _copies(me, card) > 0:
        out.append(f"we already hold {card} (block_buying_held_cards): an approval never lifts it")
    official = b.values.value(card, tick, _copies(me, card))
    margin = rules.value_margin_for(rarity)
    if official is None:
        out.append(f"the official value of {card} could not be read (fail closed)")
    elif price > official - margin + 1e-9 and is_page_card(rarity):
        out.append(f"official value {official:g} (official_value_margin {margin:g}): every buy is capped at it")
    elif price > official - margin + 1e-9:
        top = math.floor(official - margin + 1e-9)
        out.append(
            f"official value {official:g} (off_page_min_surplus {margin:g}): an off-page card is bought strictly "
            f"below our value, at {top} at most; an approval never lifts it"
        )
    return out


def target_plan(b: Backend, card: str, price: int, until_tick: int, rarity: str, tick: int) -> dict[str, Any] | None:
    """What the agents will do with an approved buy of an off-page card: the ladder from the first bid to the
    ceiling. None for a page card (the approval only lifts `human_approval_above` there)."""
    if is_page_card(rarity):
        return None
    rules = b.rules
    if not rules.buy_targets_enabled:
        return {"pursued": False, "why": "buy_targets_enabled = false: approved, but no agent pursues it"}
    t = buy_targets.BuyTarget(card, rarity, price, until_tick, tick)
    top = buy_targets.ceiling(t, b.values.value(card, tick, 0), rules)
    if top is None:
        return {"pursued": False, "why": "no ceiling under our value and the caps (or the value is unread)"}
    return {
        "pursued": True,
        "first_bid": buy_targets.first_bid(top, rules),
        "ceiling": top,
        "steps": rules.buy_target_steps,
        "step_ticks": rules.buy_target_step_ticks,
        "until_tick": until_tick,
        "how": "the maker bids for any copy on the board, stepping up to the ceiling; the taker takes an ask "
        "at or under the ceiling (fee included); revoke stops it",
    }


def sell_refusals(b: Backend, card: str, price: int, rarity: str, me: dict[str, Any]) -> list[str]:
    rules, held = b.rules, _copies(me, card)
    if held == 0:
        return [f"we hold no copy of {card}"]
    out = []
    if rules.protects(card, rarity, held):
        out.append(f"{card} is our only copy of a page card (protect_page_sets): never sold")
    value = lowest_value_copy(me, card)
    if value is None:
        out.append(f"our value of {card} could not be read (fail closed)")
    elif price < value * rules.sell_min_value_ratio - 1e-9:
        out.append(f"sell_min_value_ratio {rules.sell_min_value_ratio:g} × our value {value:g}: never sold below it")
    return out


# ---------------------------------------------------------------- the three tools


def _by(via: str | None) -> str:
    return f"human:{via or 'mcp'}"


def _why(row: dict[str, Any], threshold: int) -> str:
    if row.get("score_impact") is not None or row.get("reason"):
        return f"score impact {row.get('score_impact')} (max_score_loss_per_move): {row.get('reason') or 'unknown'}"
    return f"price {row.get('price')} ≥ human_approval_above {threshold}"


def _state(
    row: dict[str, Any], now: int, granted: dict[tuple[str, str], Approval], denied: set[tuple[str, str, int]]
) -> str:
    """approved: an active approval covers its price now; denied: a human said no since it was asked."""
    key = (str(row.get("card")), str(row.get("side")))
    asked, price = int(row.get("tick") or 0), float(row.get("price") or 0)
    found = granted.get(key)
    if found is not None and found.covers(price, now):
        return "approved"
    if any(c == key[0] and s == key[1] and t >= asked for c, s, t in denied):
        return "denied"
    return "waiting"


def read_approvals(b: Backend, store: ApprovalStore) -> dict[str, Any]:
    """The requests of the last 2 game hours, each with what a human needs to decide, and the active approvals."""
    tick = b.clock().tick
    since = tick - approvals.PENDING_TICKS
    active = store.active(tick)
    granted = {(a.card, a.side): a for a in active}
    denied = set(store.denials(since))
    notes: list[str] = []
    try:
        me: dict[str, Any] | None = b.me_now().me
    except Exception as e:  # the requests still list; the album impact says unknown
        notes.append(f"/me unreadable ({type(e).__name__}): album impact unknown")
        me = None
    threshold = b.rules.human_approval_above
    pending: list[dict[str, Any]] = []
    for row in store.pending(since):
        card, side = str(row.get("card")), str(row.get("side"))
        rarity = b.rarity_of(card, tick)
        cap = buy_cap(b, rarity) if side == "buy" and rarity is not None else None
        asked = int(row.get("tick") or 0)
        pending.append(
            {
                "card": card,
                "side": side,
                "price": row.get("price"),
                "asked_tick": asked,
                "stale_after_tick": asked + approvals.PENDING_TICKS,
                "state": _state(row, tick, granted, denied),
                "counterparty": row.get("counterparty"),
                "asked_by": row.get("kind"),
                "why": _why(row, threshold),
                "official_value": row.get("official_value"),
                "our_value": row.get("our_value"),
                "score_impact": row.get("score_impact"),
                "album": album(me, card, rarity),
                "cap": None if cap is None else {"max_price": cap[0], "rule": cap[1]},
            }
        )
    order = {"waiting": 0, "approved": 1, "denied": 2}
    pending.sort(key=lambda r: (order[r["state"]], -r["asked_tick"]))
    return {
        "tick": tick,
        "threshold": threshold,
        "pending": pending,
        "active": [asdict(a) for a in active],
        "limits": {
            "price_min": PRICE_MIN,
            "price_max": PRICE_MAX,
            "ttl_min": TTL_MIN,
            "ttl_max": TTL_MAX,
            "ttl_default": approvals.DEFAULT_TTL_TICKS,
            "writes_per_minute": WRITES_PER_MINUTE,
        },
        "notes": notes,
    }


def grant(b: Backend, store: ApprovalStore, args: ApproveArgs, secrets: Iterable[str] = ()) -> dict[str, Any]:
    """Approve one card on one side, after every check an approval can never lift; refused with the reasons. The
    checks read the game outside the lock; a revoke of the same card and side meanwhile refuses the write."""
    key = (args.card, args.side)
    with _WRITES:
        revokes = _REVOKES.get(key, 0)
    tick, by = b.clock().tick, _by(args.via)
    rarity = b.rarity_of(args.card, tick)
    base = {"card": args.card, "side": args.side, "price": args.price}
    if rarity is None:
        reasons = [f"{args.card} is not in the catalog"]
    else:
        me = b.me_now().me  # album first; an unreadable /me raises: the tool fails, nothing is written
        if args.side == "buy":
            reasons = buy_refusals(b, args.card, args.price, rarity, tick, me)
        else:
            reasons = sell_refusals(b, args.card, args.price, rarity, me)
    reason = safe_text(args.reason.strip(), secrets) or f"approved by {by}"
    with _WRITES:
        if not reasons and _REVOKES.get(key, 0) != revokes:
            reasons = [f"{args.card} {args.side} was revoked while this approval was checked: approve again"]
        if reasons:
            store.record("approval_refused", tick, {**base, "by": by, "reason": "; ".join(reasons)})
            return {"status": "refused", **base, "reasons": reasons}
        a = store.approve(args.card, args.side, args.price, tick, tick + args.ttl_ticks, by, reason)
    notes = []
    threshold = b.rules.human_approval_above
    target = target_plan(b, a.card, args.price, a.until_tick, str(rarity), tick) if args.side == "buy" else None
    if args.side == "buy" and args.price < threshold and target is None:  # a sell may still need it (MI1)
        notes.append(f"a buy at {args.price} is below human_approval_above {threshold}: it needed no approval")
    return {
        **({"target": target} if target is not None else {}),
        "status": "approved",
        "card": a.card,
        "side": a.side,
        "max_price": a.max_price,
        "min_price": a.min_price,
        "until_tick": a.until_tick,
        "tick": tick,
        "by": by,
        "notes": notes,
    }


def _tick_within(b: Backend, budget_s: float) -> int | None:
    """The game tick, or None when the public clock fails or does not answer within `budget_s` (its SDK retries a
    429 for 15 s). The read goes on in a daemon thread; nobody waits for it."""
    out: list[int] = []

    def read() -> None:
        with contextlib.suppress(Exception):
            out.append(b.clock().tick)

    reader = threading.Thread(target=read, name="revoke-clock", daemon=True)
    reader.start()
    reader.join(budget_s)
    return out[0] if out else None


def withdraw(b: Backend, store: ApprovalStore, args: RevokeArgs, secrets: Iterable[str] = ()) -> dict[str, Any]:
    """Remove the approval of a card on a side; with none, record a denial. Either way its request reads denied,
    and an approve of the same card and side still checking is refused."""
    by, reason, key = _by(args.via), safe_text(args.reason.strip(), secrets), (args.card, args.side)
    tick = _tick_within(b, REVOKE_CLOCK_BUDGET_S)
    if tick is None:  # the veto never waits for the game: the approval goes, its row has no tick
        b.log(f"bazaar-mcp: revoke without a tick (no clock within {REVOKE_CLOCK_BUDGET_S:g} s)")
    with _WRITES:
        _REVOKES[key] = _REVOKES.get(key, 0) + 1
        was = store.revoke(args.card, args.side, tick, by, reason)
    return {"status": "revoked" if was else "denied", "card": args.card, "side": args.side, "tick": tick, "by": by}


def human_specs(store: ApprovalStore, secrets: Iterable[str] = ()) -> tuple[ToolSpec, ...]:
    """The three human tools on `store`. Never added to `tools.TOOLS`: the desk must not see them. `secrets` are
    cut out of a stored reason (the answers are scrubbed by `tools.call`)."""
    held = tuple(secrets)
    return (
        ToolSpec("approvals", "HUMAN ONLY. Big trades waiting for a human (the last 2 game hours): card, side, price, "
                 "why the guardrail asked, our and the official value, album impact (a page's last copy?), the cap no "
                 "approval lifts, who asked; and the active approvals.", ApprovalsArgs, False,
                 lambda b, a: read_approvals(b, store)),
        ToolSpec("approve", "HUMAN ONLY. Let the agents trade CARD on SIDE although it needs a human (at or above "
                 "human_approval_above, or a sell that max_score_loss_per_move holds): a buy up to PRICE (fee "
                 "included), a sell down to PRICE, for ttl_ticks. A buy of an EPIC or LEGENDARY is an order: the "
                 "agents pursue it (a bid ladder up to PRICE, never at or above our value) until ttl_ticks or a "
                 "revoke. Refused when a hard cap, the page's last copy or our value forbids it.", ApproveArgs,
                 True, lambda b, a: grant(b, store, a, held)),
        ToolSpec("revoke", "HUMAN ONLY. Remove the approval of CARD on SIDE (already posted offers stay: `bazaar "
                 "flatten` cancels them; a buy target's bid is cancelled by the maker at its next tick), or with "
                 "none deny its request. Either way the request reads denied.",
                 RevokeArgs, True, lambda b, a: withdraw(b, store, a, held)),
    )  # fmt: skip
