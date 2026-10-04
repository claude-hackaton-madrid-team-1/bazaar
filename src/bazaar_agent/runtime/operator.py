"""Exact human confirmations, fresh guards and durable at-most-once operator dispatch.

The proposal is a preview, never authority. Only the separate human MCP surface approves it.
Unknown results retain their claim; reconciliation reads evidence and never repeats a POST.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict, replace
from typing import Annotated, Any, Literal

import psycopg
from pydantic import Field

from bazaar_agent import pgconn
from bazaar_agent.agents import publication
from bazaar_agent.agents.market import parse_offer, venues_from
from bazaar_agent.agents.seller import committed_context, find_copy
from bazaar_agent.guardrails import Action, Verdict, action_kind, check
from bazaar_agent.ledger_pg import official_game, trade_lock
from bazaar_agent.runtime import actions as ac
from bazaar_agent.runtime.backend import Backend
from bazaar_agent.sdk import BazaarError
from bazaar_agent.ticks import action_budget_s


class Sell(ac.SellListArgs):
    kind: Literal["sell_list"]


class Bid(ac.SellBidArgs):
    kind: Literal["sell_bid"]


class Cancel(ac.SellCancelArgs):
    kind: Literal["sell_cancel"]


class Accept(ac.Args):
    kind: Literal["offer_accept"]
    offer_id: int = Field(ge=1)
    venue: str = Field(default="rastro", pattern=ac.SLUG)


class Open(ac.Args):
    kind: Literal["team_open"]
    team_id: str = Field(pattern=r"^t[0-9]{2,4}$")
    venue: str = Field(default="rastro", pattern=ac.SLUG)


class Say(ac.Args):
    kind: Literal["team_say"]
    thread_id: int = Field(ge=1)
    text: str = Field(min_length=1, max_length=1200)


class TeamOffer(ac.Args):
    kind: Literal["team_offer"]
    target: str = Field(pattern=r"^([0-9]{1,9}|[A-Z]{3}-[0-9]{2})$")
    price: int = Field(ge=1, le=ac.MAX_OFFER_PRICE)
    side: Literal["buy", "sell"]
    thread_id: int = Field(ge=1)
    text: str = Field(default="", max_length=1200)


class Close(ac.Args):
    kind: Literal["team_close"]
    thread_id: int = Field(ge=1)


OperatorAction = Annotated[Sell | Bid | Cancel | Accept | Open | Say | Close | TeamOffer, Field(discriminator="kind")]


class Propose(ac.Args):
    action: OperatorAction
    ttl_ticks: int = Field(default=4, ge=1, le=8)


class ProposalId(ac.Args):
    proposal_id: str = Field(pattern=r"^[a-f0-9]{32}$")


def world(b: Backend) -> str:
    return "real" if official_game(b.settings.bazaar_url) else "simulator"


def scope(b: Backend) -> str:
    # Keep endpoint credentials and private URLs out of the stored evidence.
    return hashlib.sha256(b.settings.bazaar_url.rstrip("/").encode()).hexdigest()[:16]


class Proposals:
    """No JSONL fallback: losing the shared claim must never permit a second send."""

    def __init__(self, connect: Callable[[], psycopg.Connection] | None = None) -> None:
        self.connect = connect or (lambda: pgconn.connect(app="bazaar-operator", connect_timeout_s=3))

    def unresolved(self, target: str) -> bool:
        with self.connect() as conn:
            conn.execute("set statement_timeout = 3000")
            row = conn.execute(
                "select 1 from operator_proposals where payload->>'scope'=%s "
                "and state in ('executing','unknown') limit 1",
                (target,),
            ).fetchone()
        return row is not None

    def create(self, payload: dict[str, Any]) -> None:
        with self.connect() as conn:
            conn.execute("set statement_timeout = 3000")
            conn.execute(
                "insert into operator_proposals (id,world,created_tick,expires_tick,state,payload) "
                "values (%s,%s,%s,%s,%s,%s::jsonb)",
                (
                    payload["proposal_id"],
                    payload["world"],
                    payload["created_tick"],
                    payload["expires_tick"],
                    payload["status"],
                    json.dumps(payload),
                ),
            )

    def get(self, ident: str) -> dict[str, Any]:
        with self.connect() as conn:
            conn.execute("set statement_timeout = 3000")
            row = conn.execute("select payload,state,result from operator_proposals where id=%s", (ident,)).fetchone()
        if row is None:
            raise ValueError("proposal not found")
        return {**row[0], "status": row[1], "result": row[2]}

    def transition(self, ident: str, before: str, after: str, result: dict[str, Any] | None = None) -> bool:
        with self.connect() as conn:
            conn.execute("set statement_timeout = 3000")
            row = conn.execute(
                "update operator_proposals set state=%s,result=%s::jsonb where id=%s and state=%s returning id",
                (after, json.dumps(result) if result is not None else None, ident, before),
            ).fetchone()
        return row is not None


def _plain(a: Sell | Bid | Cancel) -> ac.SellListArgs | ac.SellBidArgs | ac.SellCancelArgs:
    raw = a.model_dump(exclude={"kind"})
    if isinstance(a, Sell):
        return ac.SellListArgs.model_validate(raw)
    if isinstance(a, Bid):
        return ac.SellBidArgs.model_validate(raw)
    return ac.SellCancelArgs.model_validate(raw)


def _thread(b: Backend, ident: int) -> dict[str, Any]:
    raw = b.team.thread(ident)
    thread = raw.get("thread", raw)
    other = str(thread.get("with") or thread.get("counterparty") or "")
    if not other.startswith("t") or not other[1:].isdigit() or thread.get("status") != "open":
        raise ValueError("not an open team conversation")
    return thread


def _message_stops(b: Backend, thread: dict[str, Any], ident: int, me: dict[str, Any], tick: int) -> list[str]:
    own = str(me.get("id"))
    sent = any(m.get("sender") == own and m.get("tick") == tick for m in thread.get("messages") or [])
    if sent or b.ledger.count_in_tick(f"operator_say:{ident}", tick):
        return ["message slot already used this tick"]
    return []


def prepare(b: Backend, a: OperatorAction) -> ac.Planned:
    """Same guardrails and fresh holdings as other runtime writes. No game writes here."""
    clock, started = b.clock(), time.monotonic()
    b.holdings.me(clock, live_because="operator fresh validation")
    if isinstance(a, TeamOffer):
        thread = _thread(b, a.thread_id)
        raw = a.model_dump(exclude={"kind", "side", "thread_id", "text"})
        raw["venue"] = str(thread.get("venue") or "rastro")
        if a.side == "buy":
            raw["ref"] = raw.pop("target")
        plain = ac.SellBidArgs.model_validate(raw) if a.side == "buy" else ac.SellListArgs.model_validate(raw)
        plan = ac.check_write(b, a.kind, plain)
        if "listing" not in plan.detail:
            return plan
        listing = replace(plan.detail["listing"], to=str(thread.get("with") or thread.get("counterparty")))
        base = plan.detail["base"]
        verdict = check(listing.action(), committed_context(base.ctx, base.commitments), b.rules)
        violations = list(verdict.violations)
        violations.extend(_message_stops(b, thread, a.thread_id, base.me, clock.tick))
        return replace(
            plan,
            action=listing.action(),
            verdict=Verdict(not violations, tuple(violations)),
            detail={**plan.detail, "listing": listing, "thread": thread},
        )
    if isinstance(a, Sell | Bid | Cancel):
        return ac.check_write(b, a.kind, _plain(a))
    base = ac._base(b, clock, started)
    stops = ac.kill_switch(b)
    detail: dict[str, Any] = {"base": base}
    action = None
    if isinstance(a, Open | Say | Close):
        kind = "team_open" if isinstance(a, Open) else "team_say" if isinstance(a, Say) else "close_thread"
        stops.extend(check(Action(action_kind(kind)), base.ctx, b.rules).violations)
    if isinstance(a, Open):
        threads = b.team.my_threads("open").get("threads") or []
        venues = venues_from(b.public.venues(), clock.tick)
        venue = next((v for v in venues if v.id == a.venue), None)
        if venue is None or venue.status != "open" or venue.owner == str(base.me.get("id")):
            stops.append("venue is unavailable or owned by us")
        if a.team_id == str(base.me.get("id")):
            stops.append("cannot open a conversation with ourselves")
        if len(threads) >= clock.limits.max_open_threads_per_team:
            stops.append("open thread limit reached")
    elif isinstance(a, Say | Close):
        thread = _thread(b, a.thread_id)
        detail["thread"] = thread
        if isinstance(a, Say):
            stops.extend(_message_stops(b, thread, a.thread_id, base.me, clock.tick))
    elif isinstance(a, Accept):
        offer_raw = next((o for o in base.offers if o.get("id") == a.offer_id), None)
        if offer_raw is None:
            offer_raw = next((o for o in b.public.board(a.venue).get("offers", []) if o.get("id") == a.offer_id), None)
        # Read either an addressed offer or the named public board; never guess private terms.
        if offer_raw is None or offer_raw.get("status") not in (None, "open"):
            raise ValueError("offer is not currently open and visible to our team")
        offer = parse_offer(offer_raw)
        if (
            offer is None
            or offer.maker == str(base.me.get("id"))
            or offer_raw.get("to") not in (None, base.me.get("id"))
            or offer.expires_tick is not None
            and offer.expires_tick <= clock.tick
        ):
            raise ValueError("only another team's plain one-card cash offers are supported")
        venues = venues_from(b.public.venues(), clock.tick)
        venue = next((v for v in venues if v.id == offer.venue and v.status == "open"), None)
        if venue is None or venue.owner == str(base.me.get("id")):
            raise ValueError("venue or fee unavailable, or venue owned by us")
        fee = venue.fee(offer.price)
        asset = find_copy(base.me, offer.ref) if offer.side == "bid" else None
        if asset is not None and asset["id"] in base.commitments.listed:
            stops.append("the selected asset is already promised by another offer")
        action = Action(
            "accept_sell" if asset else "accept_buy",
            offer.ref,
            asset.get("rarity") if asset else (offer.rarity or b.rarity_of(offer.ref, clock.tick)),
            offer.price - fee if asset else offer.price + fee,
            your_value=asset.get("your_value") if asset else None,
            counterparty=offer.maker,
            volume=offer.price,
            asset=asset["id"] if asset else None,
        )
        detail.update(offer=asdict(offer), fee=fee, assets=[asset["id"]] if asset else [], raw=offer_raw)
        verdict = check(action, committed_context(base.ctx, base.commitments), b.rules)
        stops.extend(verdict.violations if not verdict.allowed else [])
    if not clock.is_live:
        stops.append("game is paused or doors are closed")
    return ac.Planned(a.kind, Verdict(not stops, tuple(stops)), clock, started, action, detail)


def _terms(p: ac.Planned) -> dict[str, Any]:
    if "listing" in p.detail:
        return asdict(p.detail["listing"])
    return {k: p.detail[k] for k in ("offer", "fee", "assets") if k in p.detail}


def propose(b: Backend, args: Propose, store: Proposals | None = None) -> dict[str, Any]:
    from bazaar_agent.runtime.tools import safe_value, secrets_of

    p = prepare(b, args.action)
    action = args.action
    # Approval binds an asset, not whichever copy a later lookup would happen to choose.
    if (isinstance(action, Sell) or isinstance(action, TeamOffer) and action.side == "sell") and "listing" in p.detail:
        action = action.model_copy(update={"target": str(p.detail["listing"].asset_id)})
    may_approve = (
        p.verdict.allowed
        or bool(p.verdict.violations)
        and all(v.startswith("needs human approval:") for v in p.verdict.violations)
    )
    payload = {
        "proposal_id": uuid.uuid4().hex,
        "created_tick": p.tick,
        "expires_tick": p.tick + args.ttl_ticks,
        "status": "proposed" if may_approve else "denied",
        "action": action.model_dump(),
        "allowed": may_approve,
        "reason": str(p.verdict),
        "summary": f"{action.kind}: {action.model_dump(exclude={'kind'})}",
        "world": world(b),
        "team": b.holdings.me(p.clock).me.get("id"),
        "scope": scope(b),
        "terms": _terms(p),
        "round": p.clock.round,
        "before_offer_ids": [o.get("id") for o in p.detail["base"].offers] if "base" in p.detail else [],
    }
    payload = safe_value(payload, secrets_of(b.settings))
    (store or Proposals()).create(payload)
    return payload


def review(b: Backend, args: ProposalId, store: Proposals | None = None) -> dict[str, Any]:
    payload = (store or Proposals()).get(args.proposal_id)
    if payload["scope"] != scope(b):
        raise ValueError("proposal belongs to another game endpoint")
    if not payload.get("team") or b.me_now().me.get("id") != payload["team"]:
        raise ValueError("proposal belongs to another team")
    if payload["status"] in ("unknown", "executing"):
        payload = _reconcile(b, payload, store or Proposals())
    return payload


def _reconcile(b: Backend, payload: dict[str, Any], store: Proposals) -> dict[str, Any]:
    """An exact new visible offer is evidence the post landed. Absence is never proof of failure."""
    offers = b.my_offers()
    terms = payload.get("terms") or {}
    matches = []
    for raw in offers:
        parsed = parse_offer(raw)
        if parsed is None or parsed.id in payload.get("before_offer_ids", []):
            continue
        if (
            parsed.ref == terms.get("ref")
            and parsed.price == terms.get("price")
            and parsed.venue == terms.get("venue")
            and parsed.maker == payload["team"]
            and parsed.asset_id == terms.get("asset_id")
            and parsed.side == ("ask" if terms.get("kind") == "sell" else "bid")
            and raw.get("thread") == payload["action"].get("thread_id")
            and raw.get("to") == terms.get("to")
        ):
            matches.append(parsed.id)
    evidence = {
        "matching_offer_ids": matches,
        "open_offer_ids": [o.get("id") for o in offers],
        "note": "Only a unique new exact offer proves publication; absence never permits a retry.",
    }
    payload["reconciliation"] = evidence
    if len(matches) == 1 and payload["action"]["kind"] in ("sell_list", "sell_bid", "team_offer"):
        result = {"status": "reconciled", "sent": True, "evidence": evidence}
        if store.transition(payload["proposal_id"], payload["status"], "reconciled", result):
            payload.update(status="reconciled", result=result)
    return payload


def approve(b: Backend, args: ProposalId, store: Proposals | None = None) -> dict[str, Any]:
    s = store or Proposals()
    payload = review(b, args, s)
    clock = b.clock()
    if clock.tick > payload["expires_tick"] or clock.tick < payload["created_tick"] or clock.round != payload["round"]:
        s.transition(args.proposal_id, "proposed", "expired")
    else:
        s.transition(args.proposal_id, "proposed", "approved")
    return review(b, args, s)


def _send(b: Backend, a: OperatorAction, p: ac.Planned) -> dict[str, Any]:
    if isinstance(a, TeamOffer):
        listing, base = p.detail["listing"], p.detail["base"]
        cap = ac._listing_caps(b, p.clock, base.offers)
        if cap:
            return {"status": "denied", "sent": False, "reason": cap}
        b.ledger.record(f"operator_say:{a.thread_id}", p.tick, p.clock.t_hours)
        b.ledger.record("listing", p.tick, p.clock.t_hours, listing.price, listing.ref)
        if a.side == "buy":
            b.ledger.record("spend", p.tick, p.clock.t_hours, listing.price, listing.ref)
        reservation = publication.reserve(
            b.ledger,
            p.tick,
            p.clock.t_hours,
            str(base.me.get("id")),
            listing.give,
            listing.want,
            a.thread_id,
            to=listing.to,
        )
        try:
            response = b.team.say(a.thread_id, text=a.text, offer={"give": listing.give, "want": listing.want})
        except BazaarError as exc:
            if 400 <= exc.status < 500 and exc.status != 408:
                publication.release(b.ledger, reservation, p.tick, p.clock.t_hours)
            raise
        offered = response.get("offer") or {}
        if isinstance(offered, dict) and isinstance(offered.get("id"), int):
            publication.confirm(b.ledger, reservation, offered["id"], p.tick, p.clock.t_hours)
        return {"status": "submitted", "sent": True, "response": response}
    if isinstance(a, Sell | Bid | Cancel):
        plain = _plain(a)
        return ac.EXECUTORS[type(plain)](b, plain, p, p.clock)
    if isinstance(a, Open):
        response = b.team.open_thread(a.team_id, venue=a.venue)
    elif isinstance(a, Say):
        b.ledger.record(f"operator_say:{a.thread_id}", p.tick, p.clock.t_hours)
        response = b.team.say(a.thread_id, text=a.text)
    elif isinstance(a, Close):
        response = b.team.close_thread(a.thread_id)
    else:
        assert isinstance(a, Accept) and p.action is not None and p.action.price is not None
        limit = min(b.rules.max_accepts_per_tick, p.clock.limits.accepts_per_team_per_tick)
        if not b.ledger.reserve_accept(p.tick, p.clock.t_hours, p.action.price, p.action.item, limit):
            return {"status": "denied", "sent": False, "reason": "accept slot already used"}
        # Reserve spend before dispatch. Unknown outcomes keep the reservation, conservatively.
        if p.action.kind == "accept_buy":
            b.ledger.record("spend", p.tick, p.clock.t_hours, p.action.price, p.action.item)
        buying = p.action.kind == "accept_buy"
        reservation = publication.reserve(
            b.ledger,
            p.tick,
            p.clock.t_hours,
            str(p.detail["base"].me.get("id")),
            {"cash": p.action.price} if buying else {"assets": p.detail["assets"]},
            {"assets": [p.detail["offer"]["asset_id"]]} if buying else {"cash": p.action.price},
            to=p.action.counterparty,
        )
        try:
            response = b.team.accept(a.offer_id, assets=p.detail["assets"] or None)
        except BazaarError as exc:
            if 400 <= exc.status < 500 and exc.status != 408:
                publication.release(b.ledger, reservation, p.tick, p.clock.t_hours)
            raise
    return {"status": "submitted", "sent": True, "response": response}


def _exact_approval(p: ac.Planned, b: Backend) -> ac.Planned:
    """A confirmed immutable proposal supplies approval only for this action and this call.

    It cannot authorize autonomous later trades, and all other guards still run.
    """
    from bazaar_agent.approvals import Approval, ApprovalBook
    from bazaar_agent.guardrails import approval_side

    if p.action is None or p.action.price is None or "base" not in p.detail:
        return p
    side = approval_side(p.action)
    if side is None:
        return p
    approval = Approval(
        p.action.item,
        side,
        p.action.price if side == "buy" else None,
        p.action.price if side == "sell" else None,
        p.tick + 1,
        "human:operator",
    )
    base = p.detail["base"]
    ctx = replace(base.ctx, approvals=ApprovalBook({(p.action.item, side): approval}))
    verdict = check(p.action, committed_context(ctx, base.commitments), b.rules)
    violations = tuple(
        dict.fromkeys(
            (*verdict.violations, *(v for v in p.verdict.violations if not v.startswith("needs human approval:")))
        )
    )
    return replace(p, verdict=Verdict(not violations, violations), detail={**p.detail, "base": replace(base, ctx=ctx)})


def execute(b: Backend, args: ProposalId, store: Proposals | None = None) -> dict[str, Any]:
    s = store or Proposals()
    payload = review(b, args, s)
    if payload["status"] != "approved":
        return {
            "proposal_id": args.proposal_id,
            "status": payload["status"],
            "sent": False,
            "reason": "not approved or already claimed; never redispatched",
            "result": payload.get("result"),
        }
    with b.write_lock, trade_lock(b.ledger):
        if s.unresolved(scope(b)):
            return {
                "proposal_id": args.proposal_id,
                "status": "denied",
                "sent": False,
                "reason": "an earlier dispatch is unresolved; review its evidence before any new write",
            }
        action = Propose.model_validate({"action": payload["action"]}).action
        p = prepare(b, action)
        p = _exact_approval(p, b)
        reason = str(p.verdict)
        expired = (
            p.tick > payload["expires_tick"] or p.tick < payload["created_tick"] or p.clock.round != payload["round"]
        )
        changed = _terms(p) != payload["terms"]
        if expired or changed or not p.verdict.allowed or p.budget_left() <= 0 or not p.clock.is_live:
            status = "expired" if expired or p.budget_left() <= 0 else "denied"
            reason = "approved terms changed; create a new proposal" if changed else reason
            result: dict[str, Any] = {
                "proposal_id": args.proposal_id,
                "status": status,
                "sent": False,
                "reason": reason,
            }
            s.transition(args.proposal_id, "approved", status, result)
            return result
        if not s.transition(args.proposal_id, "approved", "executing"):
            return {"proposal_id": args.proposal_id, "status": "denied", "sent": False, "reason": "already claimed"}
        result = {"status": "dry_run", "sent": False, "reason": "live writes disabled"}
        if b.live:
            try:
                if p.budget_left() <= 0 or b.clock().tick != p.tick:
                    result = {"status": "expired", "sent": False, "reason": "tick ended before dispatch"}
                else:
                    result = _send(b, action, p)
                result["status"] = {"done": "submitted", "rejected": "denied", "failed": "unknown"}.get(
                    result["status"], result["status"]
                )
            except BazaarError as exc:
                refused = 400 <= exc.status < 500 and exc.status != 408
                reason = (
                    f"game rejected request ({exc.code})"
                    if refused
                    else "uncertain game response; reconcile before retry"
                )
                result = {
                    "status": "denied" if refused else "unknown",
                    "sent": False if refused else None,
                    "reason": reason,
                }
            except Exception as exc:
                result = {
                    "status": "unknown",
                    "sent": None,
                    "reason": f"{type(exc).__name__}; reconcile before any new proposal",
                }
        result["proposal_id"] = args.proposal_id
        # A failure here leaves 'executing', also permanently ineligible for redispatch.
        s.transition(args.proposal_id, "executing", result["status"], result)
        if result.get("sent"):
            try:
                b.holdings.after_deal(p.clock, "operator dispatch")
            except Exception:
                result["holdings_refresh"] = "unavailable; next action must read fresh holdings"
        return result


def _recent_decisions(b: Backend, tick: int, round_id: int | None) -> tuple[list[dict[str, Any]], bool]:
    conn = b.decisions._db()
    if conn is None:
        return [], False
    try:
        rows = conn.execute(
            "select tick,agent,kind,status,reason,policy_checks,candidates from decisions "
            "where candidates->'evidence_context'->>'target'=%s "
            "and (candidates->'evidence_context'->>'round') is not distinct from %s "
            "and tick between %s and %s order by id desc limit 20",
            (scope(b), str(round_id) if round_id is not None else None, max(0, tick - 8), tick),
        ).fetchall()
        keys = ("tick", "agent", "kind", "status", "reason", "guardrails", "inputs")
        return [dict(zip(keys, row, strict=True)) for row in rows], True
    except psycopg.Error:
        return [], False


def _activity(candidates: list[dict[str, Any]], available: bool, clock: Any) -> dict[str, Any]:
    if not clock.is_live:
        return {"state": "expected_idle", "reason": "doors closed or game paused"}
    if not available:
        return {"state": "unknown", "reason": "scoped decision history unavailable"}
    if any(r["status"] == "done" or (r["kind"] == "duel_hold" and r["status"] == "approved") for r in candidates):
        return {"state": "active", "reason": "a completed action appears in the recent decision window"}
    blocked = [r for r in candidates if r["status"] in ("rejected", "failed")]
    if blocked:
        return {"state": "blocked", "reason": blocked[0]["reason"]}
    if any(r["kind"] in ("no_candidates", "no_work", "hold") for r in candidates):
        return {"state": "no_work", "reason": "the latest decision window explicitly reports no eligible work"}
    return {"state": "unknown", "reason": "no conclusive activity evidence in this decision window"}


def _incidents(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    distinct: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in candidates:
        if row["status"] == "failed" or row["kind"] == "activity_stall":
            distinct.setdefault((row["agent"], row["kind"], row["reason"]), row)
    return list(distinct.values())


def snapshot(b: Backend) -> dict[str, Any]:
    clock = b.clock()
    with trade_lock(b.ledger):
        read = b.holdings.me(clock, live_because="reconcile operator commitments")
        offers = publication.with_pending(
            b.ledger, read.me, b.my_offers(), str(read.me.get("id")), clock.tick, clock.t_hours
        )
    # Uncertain writes must stay visible even when the server already has 30 offers.
    offers.sort(key=lambda o: not o.get("publication_pending", False))
    candidates, history_available = _recent_decisions(b, clock.tick, clock.round)
    breakdown = read.me.get("score_breakdown")
    breakdown = breakdown if isinstance(breakdown, dict) else {}
    summary = read.me.get("score")
    summary = summary if isinstance(summary, dict) else {}
    try:
        events = [
            {k: e.get(k) for k in ("id", "tick", "type")} for e in b.public.feed_window(12) if isinstance(e, dict)
        ]
    except Exception:
        events = []
    return {
        "world": world(b),
        "context": {
            "round": clock.round,
            "service": os.getenv("RAILWAY_SERVICE_NAME"),
            "deploy": os.getenv("RAILWAY_DEPLOYMENT_ID"),
        },
        "clock": clock.model_dump(),
        "account": {"team": read.me.get("id"), "cash": read.me.get("cash")},
        "score": read.me.get("score"),
        "score_breakdown": read.me.get("score_breakdown"),
        "score_components": {
            key: read.me.get(key, breakdown.get(key, summary.get(key)))
            for key in (
                "rank",
                "negotiating",
                "market",
                "neg_points",
                "ladder_points",
                "duel_points",
                "bench_points",
                "organic_points",
            )
        },
        "source_tick": read.tick,
        "activity": _activity(candidates, history_available, clock),
        "links": {
            "logs": "https://railway.com/project/05a9de65-622b-4754-a0f0-be4d7f54ec51",
            "traces": "https://phoenix-production-6aa3.up.railway.app",
        },
        "holdings": read.meta(),
        "budget": {
            "action_seconds": action_budget_s(clock),
            "accepts_used": b.ledger.accepts_in_tick(clock.tick),
            "listings_used": b.ledger.count_in_tick("listing", clock.tick),
            "limits": clock.limits.model_dump(),
        },
        "pending": [
            {
                k: o.get(k)
                for k in ("id", "status", "venue", "thread", "give", "want", "publication_pending", "created_tick")
            }
            for o in offers[:30]
        ],
        "pending_count": len(offers),
        "candidates": candidates,
        "events": events,
        "incidents": _incidents(candidates),
        "history_available": history_available,
        "evidence": (
            "Current clock, holdings and commitments; last 20 decisions in 8 ticks from this target and round. "
            "Historical rows without provenance are excluded; history_available=false means unavailable."
        ),
    }
