"""One hand-sent Market Test probe: a bench pair whose quotes do NOT cross, priced at the midpoint.

Settles the one unknown of the bench (MM_STRATEGY.md §2.1): does POST /api/broker/matches check the quotes or the
hidden limits? Dry by default (reads the book, prints the candidate, sends nothing). `--send` POSTs ONCE.

    uv run python scripts/bench_probe_once.py                 # dry: book + candidate pair, no POST
    uv run python scripts/bench_probe_once.py --send --watch  # one POST, then re-read the book next tick
    uv run python scripts/bench_probe_once.py --after         # /api/me bench_points + public board (team key)

Rules it keeps: real game only (never a simulator key), at most one verdict POST per run (a 429 wait_for_tick
stops the run, not a verdict: rerun, so the pair comes from a fresh book), the pair is two bench traders who cross
NOBODY in the current book (so the running exact broker cannot be racing for them), gap <= --max-gap, keys never
printed.
Every response is appended as JSON to <data_dir>/bench_probe_once.jsonl.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from bazaar_agent import venue as vn
from bazaar_agent.agents.bench_probe import probe_price
from bazaar_agent.agents.matcher import BrokerBook, Fee, Quote, plan_matches, quotes_from
from bazaar_agent.config import load_settings
from bazaar_agent.sdk import Bazaar, BazaarError

SETTLE_S = 4.0  # seconds into the tick before a send (the first second of a tick answers wait_for_tick)


def log_line(path: Path, record: dict[str, Any]) -> None:
    record = {"at": datetime.now().isoformat(timespec="seconds"), **record}
    with path.open("a") as f:
        f.write(json.dumps(record, default=str) + "\n")
    print(json.dumps(record, default=str))


def bench_quotes(book: BrokerBook) -> list[Quote]:
    return [q for q in quotes_from(book, public=False).quotes if q.bench]


def exact_taken(quotes: list[Quote], fee: Fee) -> set[str]:
    """The traders the exact plan (what the running broker sends this tick) takes: never probe those."""
    return {str(q.id) for m in plan_matches(quotes, fee) for q in (m.sell, m.buy)}


def candidate(quotes: list[Quote], fee: Fee, max_gap: int) -> tuple[Quote, Quote, int, int] | None:
    """(sell, buy, gap, price): the tightest non-crossing pair among the traders the exact plan leaves out
    (the same rule as `BenchProbe.plan`, one pair only)."""
    taken = exact_taken(quotes, fee)
    free = [q for q in quotes if str(q.id) not in taken]
    best: tuple[Quote, Quote, int, int] | None = None
    for s in (q for q in free if q.side == "sell"):
        for b in (q for q in free if q.side == "buy" and q.item == s.item and q.maker != s.maker):
            gap = s.price + fee.of(s.price) - b.price
            if gap <= 0 or gap > max_gap:
                continue
            if best is None or gap < best[2]:
                best = (s, b, gap, probe_price(s.price, b.price, fee))
    return best


def show_book(tick: int, quotes: list[Quote], fee: Fee) -> None:
    runs = sorted({q.item for q in quotes})
    taken = exact_taken(quotes, fee)
    print(f"tick {tick}: {len(quotes)} bench quote(s) in run(s) {runs or '-'}; the exact plan takes {len(taken)}")
    for q in sorted(quotes, key=lambda q: (q.item, q.side, q.price)):
        flag = " (exact plan's)" if str(q.id) in taken else ""
        print(f"  {str(q.id):>8} {q.side:4} {q.price:4}{flag}")


def wait_into_tick(broker: Any) -> dict[str, Any]:
    """Return the clock a few seconds into a tick (sleeps through a tick's first seconds)."""
    clock = broker.clock()
    elapsed = float(clock["tick_seconds"]) - float(clock.get("next_tick_in") or 0.0)
    if elapsed < SETTLE_S:
        time.sleep(SETTLE_S - elapsed)
        clock = broker.clock()
    return clock


def send_once(broker: Any, sell: Quote, buy: Quote, price: int, log: Path) -> dict[str, Any]:
    """One verdict POST. rate_limited -> retry after 1.5 s (same pair, same tick); wait_for_tick -> stop, not a
    verdict (a pair read last tick may cross by quote now, and a queued crossing pair would prove nothing)."""
    for attempt in range(1, 4):
        try:
            res = broker.match(sell.id, buy.id, price)
        except BazaarError as e:
            rec = {
                "event": "refused",
                "attempt": attempt,
                "status": e.status,
                "code": e.code,
                "message": e.message,
                "extra": e.extra,
                "sell": sell.id,
                "buy": buy.id,
                "ask": sell.price,
                "bid": buy.price,
                "price": price,
            }
            log_line(log, rec)
            if e.code == "wait_for_tick":  # never resend a stale pair: by the next tick its quotes may cross
                return {**rec, "event": "not_a_verdict", "reason": "wait_for_tick: rerun to read a fresh book"}
            if e.code == "rate_limited":
                time.sleep(1.5)
                continue
            return rec
        rec = {
            "event": "sent",
            "attempt": attempt,
            "response": res,
            "sell": sell.id,
            "buy": buy.id,
            "ask": sell.price,
            "bid": buy.price,
            "price": price,
        }
        log_line(log, rec)
        return rec
    return {"event": "gave_up", "reason": "3 attempts, all rate_limited"}


def watch(broker: Any, sell: Quote, buy: Quote, log: Path) -> None:
    """Next tick: are the two traders back in the book (dropped) or gone (settled or removed: the score tells)?"""
    clock = broker.clock()
    time.sleep(float(clock.get("next_tick_in") or 0) + SETTLE_S)
    book = BrokerBook.model_validate(broker.book())
    ids = {q.id for q in bench_quotes(book)}
    rec = {"event": "watch", "tick": broker.clock()["tick"], "sell_back": sell.id in ids, "buy_back": buy.id in ids}
    rec["reading"] = (
        "DROPPED (a trader is back)"
        if rec["sell_back"] or rec["buy_back"]
        else "GONE (settled or removed: read /api/me bench_points after the session)"
    )
    log_line(log, rec)


def after(settings: Any, log: Path) -> None:
    """The decisive numbers after the session: /api/me bench_points (team key) and the public board."""
    team = Bazaar(settings.bazaar_url, settings.require_team_key(), wait_on_tick=False, retries=2)
    me = team.me()
    score = me.get("score") or {}
    keys = {k: score.get(k) for k in ("bench_points", "bench_efficiency", "mm_points", "market") if k in score}
    board = team._call("GET", "/api/leaderboard")
    ours: dict[str, Any] = next((t for t in board.get("teams", []) if t.get("team") == "t01"), {})
    rec = {
        "event": "after",
        "tick": board.get("tick"),
        "me_score": keys or score,
        "board_market": ours.get("market"),
        "board_rank": ours.get("rank"),
    }
    rec["reading"] = (
        "LIMIT RULE: above the stall"
        if (keys.get("bench_points") or 0) > 0.5005
        else "no lift (refused, dropped, or a probe that gained nothing)"
    )
    log_line(log, rec)


def broker_from_vault(settings: Any) -> Any:
    """The broker connection with the key from BAZAAR_BROKER_KEY / <data_dir>/broker.env when set, else from the
    key vault (Postgres `venue_broker_keys`, where a live `venue open` saved it; DATABASE_URL from .env)."""
    if settings.broker_key is not None and settings.broker_key.get_secret_value():
        return vn.broker_client(settings)
    from bazaar_agent import db

    vault = vn.KeyVault.from_settings(settings, lambda: db.connect(app="bench-probe-once", connect_timeout_s=5))
    stored = vault.load(settings.venue_id or "v19")
    if stored is None:
        print("no broker key: not in the environment, the data dir or the key vault (DATABASE_URL set?)")
        raise SystemExit(2)
    print(f"broker key loaded from {stored.where} for {stored.venue} (never shown)")
    return vn.broker_client(settings, key=stored.key)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--send", action="store_true", help="POST the candidate once (default: dry, no POST)")
    ap.add_argument("--watch", action="store_true", help="after --send: re-read the book next tick")
    ap.add_argument("--after", action="store_true", help="read /api/me bench_points and the public board, nothing else")
    ap.add_argument("--max-gap", type=int, default=10, help="largest ask - bid gap to probe (P)")
    ap.add_argument("--yes", action="store_true", help="with --send: skip the confirmation prompt")
    args = ap.parse_args()

    settings = load_settings()
    if settings.simulated or not vn.is_game_host(settings.bazaar_url):
        print("refusing: this is not the real game (BAZAAR_SIM set, or the URL is not the game host)")
        return 2
    log = Path(settings.data_dir) / "bench_probe_once.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)

    if args.after:
        after(settings, log)
        return 0

    broker = broker_from_vault(settings)  # raises if the broker key is missing or a simulator key
    clock = wait_into_tick(broker)
    book = BrokerBook.model_validate(broker.book())
    fee = Fee(book.fee_bps, book.fee_per_card)
    quotes = bench_quotes(book)
    show_book(int(clock["tick"]), quotes, fee)
    if not quotes:
        print("no bench offers in the book: not a Market Test tick (or it has not started). Nothing sent.")
        return 1
    pick = candidate(quotes, fee, args.max_gap)
    if pick is None:
        print(f"no non-crossing pair with gap <= {args.max_gap} among traders who cross nobody. Nothing sent.")
        return 1
    sell, buy, gap, price = pick
    print(f"candidate: sell {sell.id} ask {sell.price} x buy {buy.id} bid {buy.price} (gap {gap}) -> price {price}")
    log_line(
        log,
        {
            "event": "candidate",
            "tick": clock["tick"],
            "sell": sell.id,
            "buy": buy.id,
            "ask": sell.price,
            "bid": buy.price,
            "gap": gap,
            "price": price,
            "dry": not args.send,
        },
    )
    if not args.send:
        print("dry run: nothing sent. Add --send to POST it once.")
        return 0
    if not args.yes:
        answer = input("POST this one match now? [y/N] ").strip().lower()
        if answer != "y":
            print("not sent.")
            return 0
    rec = send_once(broker, sell, buy, price, log)
    if rec.get("event") == "sent":
        print(
            "QUEUED by the server: the quotes were NOT checked at POST time. Read /api/me after the session (--after)."
        )
        if args.watch:
            watch(broker, sell, buy, log)
    elif rec.get("event") == "not_a_verdict":
        print(f"NOT A VERDICT ({rec.get('reason')}). Nothing matched; rerun the same command.")
    elif rec.get("event") == "refused":
        print(
            f"REFUSED {rec.get('status')} {rec.get('code')}: {rec.get('message')} "
            f"(a code naming the price/crossing = the quote rule; anything else: read the message)"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
