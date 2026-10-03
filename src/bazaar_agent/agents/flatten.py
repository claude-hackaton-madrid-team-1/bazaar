"""FLATTEN: the operator's explicit "cancel everything" (issue #3), separate from the kill switch.

The kill switch HOLDS: nothing is sent and our open offers stay open. When we do want the book empty
(before the doors close overnight, or after a strategy went wrong), `bazaar flatten` cancels every open
offer of ours from `/api/me/offers` (board asks and bids on every venue) and, only with `--threads`,
closes our open threads too (closing a dealer thread is a walk the dealer remembers). These cancels and
closes are the ONLY writes that go out while the kill switch is on: pause first, then flatten. Every
agent loop still sends nothing under the kill switch.

One pass, paced under the key's 5 req/s. A refusal that says wait (`429`, `rate_limited`,
`wait_for_tick`) stops the pass and reports what is left: run it again next tick, never retry in a loop.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from bazaar_agent.agents.market import parse_offer
from bazaar_agent.agents.runtime import Recorder
from bazaar_agent.guardrails import LedgerStore, refund_row

WAIT_CODES = ("rate_limited", "wait_for_tick", "too_many_requests")
PACE_S = 0.3  # between sends: ~3 req/s leaves room in the key's 5 req/s for the agents' reads
REASON = "bazaar flatten (operator)"


@dataclass(frozen=True)
class Item:
    """One write flatten would send: cancel one offer, or close one thread."""

    kind: Literal["cancel", "close_thread"]
    id: int
    what: str  # "bid LAV-02 at 9 on rastro", "thread 85 with abuela"
    # a bid's (card, cash, created tick): its spend in the ledger is given back in the hour it was spent
    refund: tuple[str, int, int | None] | None = None


@dataclass
class Report:
    planned: list[Item]
    done: list[Item] = field(default_factory=list)
    failed: list[tuple[Item, str]] = field(default_factory=list)  # refused for another reason (e.g. already gone)
    left: list[Item] = field(default_factory=list)  # not sent: a rate limit stopped the pass
    stopped: str | None = None  # the refusal code that stopped the pass


def offers_to_cancel(response: dict[str, Any], us: str) -> list[Item]:
    """Every open or queued offer of ours outside a thread, on every venue. An offer another team
    addressed to us is not ours; an offer inside a thread goes away when its thread is closed."""
    items = []
    for rows in response.values():
        for o in rows if isinstance(rows, list) else []:
            if not isinstance(o, dict) or not isinstance(o.get("id"), int):
                continue
            if o.get("status") not in (None, "open", "queued") or o.get("thread") is not None:
                continue
            if o.get("to") == us and o.get("maker") != us:
                continue
            p = parse_offer(o)
            what = f"{p.side} {p.ref} at {p.price} on {p.venue}" if p else f"offer on {o.get('venue') or '-'}"
            refund = (p.ref, p.price, p.created_tick) if p is not None and p.side == "bid" else None
            items.append(Item("cancel", int(o["id"]), what, refund))
    return items


def threads_to_close(response: dict[str, Any]) -> list[Item]:
    """Every open thread we are part of (dealer conversations included)."""
    return [
        Item("close_thread", int(t["id"]), f"thread {t['id']} with {t.get('with') or '-'}")
        for t in response.get("threads") or []
        if isinstance(t, dict) and isinstance(t.get("id"), int) and t.get("status", "open") == "open"
    ]


def flatten(
    team: Any,
    items: Sequence[Item],
    *,
    rec: Recorder,
    tick: int,
    t_hours: float,
    ledger: LedgerStore | None,
    live: bool,
    kill_switch: Sequence[str] = (),
    tick_seconds: float = 60.0,
    pace_s: float | None = None,
    sleep: Callable[[float], None] | None = None,
) -> Report:
    """Record every item as a decision; when `live`, send them one by one. The kill switch is NOT a
    reason to hold here (that is the point of flatten); it is only written in each decision row."""
    from bazaar_agent.sdk import BazaarError

    pace_s, sleep = PACE_S if pace_s is None else pace_s, sleep or time.sleep
    guardrail = f"kill switch on ({'; '.join(kill_switch)}): operator flatten goes out" if kill_switch else "allowed"
    report = Report(list(items))
    for n, item in enumerate(items):
        did = rec.decide(
            tick,
            f"flatten_{item.kind}",
            f"{'cancel' if item.kind == 'cancel' else 'close'} {item.id}: {item.what}",
            inputs={"id": item.id, "what": item.what},
            reason=REASON,
            guardrail=guardrail,
            chosen=True,
            status="approved",
            move={item.kind: item.id},
        )
        if not live:
            continue
        if n:
            sleep(pace_s)
        refused: list[BazaarError] = []

        def call(item: Item = item, refused: list[BazaarError] = refused) -> Any:
            try:
                return team.cancel(item.id) if item.kind == "cancel" else team.close_thread(item.id)
            except BazaarError as e:
                refused.append(e)
                raise

        if rec.send(did, tick, item.kind, {"id": item.id}, call) is not None:
            report.done.append(item)
            if item.refund is not None and ledger is not None:  # a bid's cash was counted as spend when posted
                ref, cash, created = item.refund
                ledger.record(*refund_row(cash, ref, created, tick, t_hours, tick_seconds))
            continue
        error = refused[0] if refused else None
        code = error.code if error is not None else "refused"
        if error is not None and (error.status == 429 or error.code in WAIT_CODES):
            report.stopped, report.left = code, list(items[n:])
            break
        report.failed.append((item, code))
    return report
