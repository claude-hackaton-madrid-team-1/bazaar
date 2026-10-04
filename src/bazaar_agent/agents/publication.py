"""Durable promises made before a game write, shared by maker, taker and operator.

Call under ``trade_lock`` with fresh holdings and offers. A missing HTTP answer never
frees a promise. Only a definitive refusal, observed offer or departed asset does.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from bazaar_agent.guardrails import LedgerStore


def reserve(
    ledger: LedgerStore,
    tick: int,
    t_hours: float,
    us: str,
    give: dict[str, Any],
    want: dict[str, Any],
    thread: int | None = None,
    *,
    to: str | None = None,
) -> str:
    token = uuid.uuid4().hex
    payload = {
        "token": token,
        "maker": us,
        "give": give,
        "want": want,
        "thread": thread,
        "to": to,
        "created_tick": tick,
        "world": getattr(ledger, "world", "unknown"),
    }
    ledger.record("publication_pending", tick, t_hours, item=json.dumps(payload))
    return token


def confirm(ledger: LedgerStore, token: str, offer_id: int, tick: int, t_hours: float) -> None:
    ledger.record("publication_confirm", tick, t_hours, item=json.dumps([token, offer_id]))


def release(ledger: LedgerStore, token: str, tick: int, t_hours: float) -> None:
    ledger.record("publication_release", tick, t_hours, item=token)


def _assets(offer: dict[str, Any]) -> set[int]:
    return {a["id"] if isinstance(a, dict) else a for a in offer.get("give", {}).get("assets", [])}


def _terms(side: dict[str, Any]) -> tuple[int, tuple[int, ...], tuple[str, ...]]:
    assets = tuple(sorted(a["id"] if isinstance(a, dict) else a for a in side.get("assets", [])))
    refs = tuple(sorted(str(r).removeprefix("card:") for r in [*side.get("cards", []), *side.get("types", [])]))
    return int(side.get("cash") or 0), assets, refs


def with_pending(
    ledger: LedgerStore,
    me: dict[str, Any],
    offers: list[dict[str, Any]],
    us: str,
    tick: int,
    t_hours: float,
) -> list[dict[str, Any]]:
    pending: dict[str, dict[str, Any]] = {}
    confirmed: dict[str, int] = {}
    for kind, item in ledger.publication_rows():
        if kind == "publication_pending":
            row = json.loads(item)
            pending[row["token"]] = row
        elif kind == "publication_confirm":
            token, oid = json.loads(item)
            confirmed[token] = oid
        elif kind == "publication_release":
            pending.pop(item, None)
    held = {a["id"]: a for a in me.get("assets", [])}
    result = list(offers)
    claimed: set[int] = set()
    for token, row in pending.items():
        if row["maker"] != us or row.get("world", "unknown") != getattr(ledger, "world", "unknown"):
            continue
        assets = _assets(row)
        received = _assets({"give": row["want"]})
        candidates = [
            o
            for o in offers
            if o.get("maker") == us
            and o.get("id") not in claimed
            and (
                o.get("id") == confirmed.get(token, -999999)
                or (
                    o.get("thread") == row["thread"]
                    and o.get("to") == row.get("to")
                    and o.get("status") in {"open", "queued", "accepted"}
                    and int(o.get("created_tick") or -1) >= row["created_tick"]
                    and _terms(o.get("give", {})) == _terms(row["give"])
                    and _terms(o.get("want", {})) == _terms(row["want"])
                )
            )
        ]
        observed = candidates[0] if len(candidates) == 1 else None
        # An accepted offer remains in the returned commitments until settlement. A
        # missing offer is NOT proof of failure (including after restart or expiry).
        if (
            observed is not None
            or (assets and not (assets & held.keys()))
            or (not assets and received and received <= held.keys())
        ):
            if observed is not None:
                claimed.add(observed["id"])
            release(ledger, token, tick, t_hours)
            continue
        give = dict(row["give"])
        give["assets"] = [{"id": asset, "ref": held[asset]["ref"]} for asset in assets if asset in held]
        result.append({**row, "id": -int(token, 16), "status": "open", "give": give, "publication_pending": True})
    return result
