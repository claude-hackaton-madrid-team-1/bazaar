"""The per-tick request budget: every loop's calls per game tick against the 5 req/s per key (RULES.md).

RULES.md "Fair play": 5 requests per second per key, bursts of 20; reads without a key, 60 per second per
address. Every process that holds the team key (Railway services, every laptop) draws on ONE bucket, so the
budget is the sum over every loop we run, times every copy of it.

Three buckets, by the header a call carries (`sdk.py`):
- `team`: `team_client()` sends X-Team-Key on EVERY call, its `clock()` included (the taker, maker,
  duels and dealer loops read the clock with it);
- `broker`: X-Broker-Key (PR #71's broker). A key of its own, so assumed to be a bucket of its own;
  `check(..., broker_shares_team_bucket=True)` tests the stricter reading;
- `public`: `public_client()` sends no key (monitor clock, feed, dealers, catalog, venues, boards, evals):
  60 req/s per address, and laptops behind one NAT share that address.

Each `LoopBudget` is a CEILING per tick, derived from the caps in the code (`source` says which).
`tests/test_rate_budget.py` runs every loop on fakes behind a counting proxy and fails when a loop makes
more calls in a tick than its budget declares: raise the budget here, and the table must still pass.

`team_at_boundary` is how many of a loop's team calls go out right after the tick lands, before any
deliberate wait (every loop wakes `AFTER_TICK_S` after the tick, `ticks.py`). `burst()` replays those
calls through a token bucket to count the `429 rate_limited` refusals the boundary would cause.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from typing import Any, Literal

Bucket = Literal["team", "broker", "public"]

RATE_PER_KEY = 5.0  # RULES.md: 5 requests per second per key
BURST_PER_KEY = 20  # ... bursts of 20
PUBLIC_RATE_PER_ADDRESS = 60.0  # reads without a key, per address
SATURDAY_TICK_S = 30.0
SUNDAY_TICK_S = 15.0
DEFAULT_LATENCY_S = 0.15  # one synchronous SDK call (urllib, no pipelining): ~6-7 calls/s per process
VENUES_READ = 19  # boards the taker may read per tick: El Rastro + a stall or venue for each of 18 teams


@dataclass(frozen=True)
class LoopBudget:
    """One loop's ceiling of calls per game tick, by bucket."""

    name: str
    team: int
    team_at_boundary: int
    public: int = 0
    broker: int = 0
    broker_at_boundary: int = 0
    copies: int = 1
    source: str = ""

    def times(self, copies: int) -> LoopBudget:
        return replace(self, copies=copies)


def monitor(stream_retry: bool = True) -> LoopBudget:
    return LoopBudget(
        "monitor",
        team=1 + int(stream_retry),
        team_at_boundary=1 + int(stream_retry),
        public=4,
        source="agents/monitoring.py: public clock, feed, dealers, levels; team /me; "
        "the SSE stream retried once per tick after a 429/503 fallback (stream.py)",
    )


def taker(dealer_threads: int = 3, venues: int = VENUES_READ) -> LoopBudget:
    reads = 1 + 3  # loop clock (team key) + me, my_offers, my_threads
    per_thread = 3  # open_thread, thread read, say/close: one move per conversation per tick
    accept = 1 + 1 + 1  # fresh clock before the accept, the accept, the replaced bid's cancel
    return LoopBudget(
        "taker",
        team=reads + per_thread * dealer_threads + accept,
        team_at_boundary=reads + 2 * dealer_threads,  # opens and thread reads run before the duel grace wait
        public=4 + venues,  # dealers, catalog, venues, feed + one board per tradable venue
        source=f"agents/taker.py: TakerConfig.max_dealer_threads={dealer_threads}, 1 accept per tick",
    )


def maker(max_open_offers: int = 30, listings_per_tick: int = 12) -> LoopBudget:
    reads = 1 + 2  # loop clock (team key) + me, my_offers
    team = reads + max_open_offers + listings_per_tick  # plan_offers: every stale offer cancelled, 12 posts
    return LoopBudget(
        "maker",
        team=team,
        team_at_boundary=team,  # nothing waits: cancels and posts follow the reads back to back
        public=4,
        source="agents/maker.py plan_offers: cancels are uncapped (≤ max_open_offers_per_team 30), "
        "posts ≤ offers_per_team_per_tick 12",
    )


def duels(concurrent: int = 3) -> LoopBudget:
    team = 2 + concurrent + 1  # clock + /api/duels, one move per live duel, ?done=true when one finished
    return LoopBudget(
        "duels",
        team=team,
        team_at_boundary=team,
        source=f"cli.py duel run: max_concurrent {concurrent} (Duels I 3, practice 6)",
    )


def dealer_child() -> LoopBudget:
    return LoopBudget(
        "dealer buy",
        team=5,
        team_at_boundary=5,
        source="agents/dealer.py negotiate: clock, thread, fresh clock, one move; open or close on its edge ticks",
    )


def broker(book_reads: int = 1, max_matches: int = 15) -> LoopBudget:
    return LoopBudget(
        "broker",
        team=1,
        team_at_boundary=1,
        public=1,
        broker=1 + book_reads + max_matches,
        broker_at_boundary=1 + book_reads + max_matches,
        source=f"PR #71 agents/broker.py: broker clock, {book_reads} book read(s), ≤ {max_matches} matches; "
        "team my_offers; public feed",
    )


def evals() -> LoopBudget:
    return LoopBudget("evals", team=0, team_at_boundary=0, public=1, source="evals/cli.py: public clock only")


def operator(req_per_s: float, tick_seconds: float) -> LoopBudget:
    """On-demand callers of the team key, as an average rate: the MCP tools (`status`, `strategy`,
    `threads` read `/me`; every write tool's check reads clock + `/me` + offers, even in a dry run), the
    desk and `bazaar ask`, an operator's `bazaar status`. Not per tick, so not in the plans: add it."""
    calls = math.ceil(req_per_s * tick_seconds)
    return LoopBudget(
        "operator tools",
        team=calls,
        team_at_boundary=0,
        source=f"bazaar-mcp, desk, `bazaar ask`/`status` at {req_per_s:g} req/s on average",
    )


def flatten(open_offers: int = 30) -> LoopBudget:
    """`bazaar flatten` (PR #68): reads, then one cancel per open offer, back to back, once."""
    return LoopBudget("flatten", team=2 + open_offers, team_at_boundary=2 + open_offers, source="PR #68 bazaar flatten")


def saturday_plan(*, dealer_children: int = 0, duel_concurrency: int = 3, book_reads: int = 1) -> list[LoopBudget]:
    """What runs on Saturday, every loop at its CEILING: one of each service. Laptops add copies
    (`with_copies`), and `bazaar dealer buy` children add `dealer_children`."""
    plan = [monitor(), taker(), maker(), duels(duel_concurrency), broker(book_reads), evals()]
    if dealer_children:
        plan.append(dealer_child().times(dealer_children))
    return plan


def steady_plan(*, duel_concurrency: int = 3, book_reads: int = 1) -> list[LoopBudget]:
    """A busy but ordinary tick: one dealer conversation on the taker, the maker cancelling and posting two
    offers, every duel moving (the taker tick measured on the fakes makes exactly these calls)."""
    return [
        monitor(),
        taker(dealer_threads=1),
        maker(max_open_offers=2, listings_per_tick=2),
        duels(duel_concurrency),
        broker(book_reads),
        evals(),
    ]


# The proposed stagger, opt-in per service with BAZAAR_TICK_OFFSET_S (ticks.run_per_tick; unset = 0 =
# today): when each loop starts its calls after the tick lands (seconds). Duels first (a duel's pie
# shrinks every round), then the monitor and broker, the taker after the duel grace it already waits
# before accepting, the maker last. `burst(..., offsets=PROPOSED_STAGGER)` quantifies it.
PROPOSED_STAGGER: dict[str, float] = {
    "duels": 0.0,
    "monitor": 0.5,
    "broker": 0.5,
    "dealer buy": 1.0,
    "taker": 2.0,
    "maker": 4.0,
}


def with_copies(loops: Iterable[LoopBudget], copies: Mapping[str, int]) -> list[LoopBudget]:
    return [loop.times(copies.get(loop.name, loop.copies)) for loop in loops]


# ---------------------------------------------------------------- the table and the checks


@dataclass(frozen=True)
class BudgetRow:
    name: str
    copies: int
    team: int
    broker: int
    public: int
    team_rps: float
    broker_rps: float
    public_rps: float


@dataclass(frozen=True)
class BudgetTable:
    tick_seconds: float
    rows: list[BudgetRow]

    def total(self, bucket: Bucket) -> int:
        return sum(getattr(r, bucket) for r in self.rows)

    def rps(self, bucket: Bucket) -> float:
        return self.total(bucket) / self.tick_seconds


def budget_table(tick_seconds: float, loops: Iterable[LoopBudget]) -> BudgetTable:
    rows = []
    for loop in loops:
        team, brk, pub = loop.team * loop.copies, loop.broker * loop.copies, loop.public * loop.copies
        rows.append(
            BudgetRow(
                loop.name,
                loop.copies,
                team,
                brk,
                pub,
                team / tick_seconds,
                brk / tick_seconds,
                pub / tick_seconds,
            )
        )
    return BudgetTable(tick_seconds, rows)


@dataclass(frozen=True)
class BurstResult:
    calls: int
    refused: int  # calls the token bucket would answer with 429 rate_limited
    seconds: float  # when the last boundary call goes out


def burst(
    loops: Iterable[LoopBudget],
    bucket: Literal["team", "broker"] = "team",
    *,
    latency_s: float = DEFAULT_LATENCY_S,
    rate: float = RATE_PER_KEY,
    capacity: int = BURST_PER_KEY,
    offsets: Mapping[str, float] | None = None,
) -> BurstResult:
    """Every copy of every loop wakes at the same instant (plus its `offsets` entry, 0 by default: today
    every loop wakes `AFTER_TICK_S` after the tick) and sends its boundary calls one after another, one
    per `latency_s` (the SDK is synchronous). A token bucket (full at `capacity`, refilled at `rate`)
    answers them; a refused call is counted once (the SDK's retry is not modelled: it only adds calls)."""
    times: list[float] = []
    for loop in loops:
        n = loop.team_at_boundary if bucket == "team" else loop.broker_at_boundary
        start = (offsets or {}).get(loop.name, 0.0)
        for _ in range(loop.copies):
            times += [start + k * latency_s for k in range(n)]
    times.sort()
    tokens, last, refused = float(capacity), 0.0, 0
    for t in times:
        tokens = min(float(capacity), tokens + (t - last) * rate)
        last = t
        if tokens >= 1.0:
            tokens -= 1.0
        else:
            refused += 1
    return BurstResult(len(times), refused, times[-1] if times else 0.0)


@dataclass(frozen=True)
class Verdict:
    ok: bool
    problems: list[str] = field(default_factory=list)


def check(
    loops: Iterable[LoopBudget],
    tick_seconds: float,
    *,
    latency_s: float = DEFAULT_LATENCY_S,
    broker_shares_team_bucket: bool = False,
    offsets: Mapping[str, float] | None = None,
) -> Verdict:
    """Sustained ≤ 5 req/s per key, no 429 at the tick boundary, keyless ≤ 60 req/s per address."""
    loops = list(loops)
    table = budget_table(tick_seconds, loops)
    problems: list[str] = []
    team_rps = table.rps("team") + (table.rps("broker") if broker_shares_team_bucket else 0.0)
    if team_rps > RATE_PER_KEY:
        problems.append(f"team key sustained {team_rps:.2f} req/s > {RATE_PER_KEY:g} at {tick_seconds:g} s ticks")
    if not broker_shares_team_bucket and table.rps("broker") > RATE_PER_KEY:
        problems.append(f"broker key sustained {table.rps('broker'):.2f} req/s > {RATE_PER_KEY:g}")
    if table.rps("public") > PUBLIC_RATE_PER_ADDRESS:
        problems.append(f"keyless {table.rps('public'):.2f} req/s > {PUBLIC_RATE_PER_ADDRESS:g} per address")
    if broker_shares_team_bucket:
        merged = [replace(b, team_at_boundary=b.team_at_boundary + b.broker_at_boundary) for b in loops]
        buckets: list[tuple[str, BurstResult]] = [
            ("team+broker", burst(merged, "team", latency_s=latency_s, offsets=offsets))
        ]
    else:
        buckets = [
            ("team", burst(loops, "team", latency_s=latency_s, offsets=offsets)),
            ("broker", burst(loops, "broker", latency_s=latency_s, offsets=offsets)),
        ]
    for name, b in buckets:
        if b.refused:
            problems.append(
                f"{name} key: {b.calls} calls at the tick boundary, {b.refused} refused 429 "
                f"(bucket {BURST_PER_KEY} + {RATE_PER_KEY:g}/s, {latency_s:g} s per call)"
            )
    return Verdict(not problems, problems)


# ---------------------------------------------------------------- measuring: a counting proxy


class CallTally:
    """Counts every method call made through `wrap()`ed clients, per bucket and per method name."""

    def __init__(self) -> None:
        self.calls: Counter[tuple[str, str]] = Counter()

    def wrap(self, client: Any, bucket: Bucket) -> Any:
        return _Counted(client, bucket, self.calls)

    def total(self, bucket: Bucket) -> int:
        return sum(n for (b, _), n in self.calls.items() if b == bucket)

    def clear(self) -> None:
        self.calls.clear()


class _Counted:
    def __init__(self, inner: Any, bucket: str, calls: Counter[tuple[str, str]]) -> None:
        self._inner, self._bucket, self._calls = inner, bucket, calls

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._inner, name)
        if not callable(attr) or name.startswith("_"):
            return attr

        def call(*args: Any, **kwargs: Any) -> Any:
            self._calls[(self._bucket, name)] += 1
            return attr(*args, **kwargs)

        return call

    def __setattr__(self, name: str, value: Any) -> None:
        if name.startswith("_"):
            object.__setattr__(self, name, value)
        else:
            setattr(self._inner, name, value)


def describe(table: BudgetTable) -> list[tuple[str, ...]]:
    """Rows for a printed table: loop, copies, calls per tick and req/s per bucket."""
    out: list[tuple[str, ...]] = [
        (
            r.name,
            str(r.copies),
            str(r.team),
            f"{r.team_rps:.2f}",
            str(r.broker),
            f"{r.broker_rps:.2f}",
            str(r.public),
            f"{r.public_rps:.2f}",
        )
        for r in table.rows
    ]
    out.append(
        (
            "total",
            "",
            str(table.total("team")),
            f"{table.rps('team'):.2f}",
            str(table.total("broker")),
            f"{table.rps('broker'):.2f}",
            str(table.total("public")),
            f"{table.rps('public'):.2f}",
        )
    )
    return out
