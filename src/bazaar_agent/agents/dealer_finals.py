"""`bazaar dealer finals`: what `dealer_final_lift` would have bought on the real feed (N14a evidence).

For each lift and each card class a dealer sells, every real conversation of that class is replayed
(`learn.replay`, the same pessimistic model the auto-evolve uses) with the patience play: our bids go from
`patience_ladder` up to the rarity cap in steps of 1, and the dealer's final (its limit) is taken only at or
under the lifted cap. The output is which conversations would have closed, at what price and share, and the
cash they need against `max_spend_per_game_hour`. It reads the captured feed only: no game call, no key.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from statistics import mean
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from bazaar_agent.agents.dealer_plan import patience_ladder
from bazaar_agent.evals.dealers import price_class
from bazaar_agent.guardrails import LIFTED_RARITIES, Guardrails
from bazaar_agent.intel import DealerThread
from bazaar_agent.learn.curves import CurveStats, curve_stats
from bazaar_agent.learn.evolve import Ladder
from bazaar_agent.learn.replay import replay_thread

console = Console()


@dataclass(frozen=True)
class FinalsRow:
    dealer: str
    price_class: str
    lift: float
    cap: int
    final_cap: int
    ladder: Ladder
    threads: int
    deals: tuple[tuple[int, int], ...]  # (thread, price) per replayed deal
    share: float  # mean replayed share over every replayed thread (no deal = 0)
    real_share: float  # what the teams got on the same threads

    @property
    def prices(self) -> list[int]:
        return [p for _, p in self.deals]

    def per_hour(self, max_spend: int) -> int:
        """How many of these deals fit in one game hour's spend cap, at their mean price."""
        return math.floor(max_spend / mean(self.prices)) if self.deals else 0


def finals_rows(
    threads: Sequence[DealerThread], rules: Guardrails, lifts: Iterable[float], dealer: str | None = None
) -> list[FinalsRow]:
    """One row per (lift, dealer, card class) with a curve and at least one replayable conversation."""
    curves = curve_stats(threads)
    out: list[FinalsRow] = []
    for lift in lifts:
        lifted = rules.model_copy(update={"dealer_final_lift": lift})
        for (who, cls), stats in sorted(curves.items()):
            rarity = cls.split(":", 1)[1] if cls.startswith("card:") else ""
            if (dealer and who != dealer) or rarity not in LIFTED_RARITIES or stats.floor is None:
                continue
            row = _row(threads, stats, rarity, lift, lifted)
            if row is not None:
                out.append(row)
    return out


def _row(
    threads: Sequence[DealerThread], stats: CurveStats, rarity: str, lift: float, rules: Guardrails
) -> FinalsRow | None:
    cap, final_cap = rules.max_price_for(rarity), rules.final_cap_for(rarity)
    assert cap is not None and final_cap is not None and stats.floor is not None
    start, top, step = patience_ladder((cap, cap, 1), stats.patience, stats.opening, stats.silent_below)
    ladder = Ladder(start, step, top)
    pool = [
        t for t in threads if t.dealer == stats.dealer and t.side == "buy" and price_class(t.item) == stats.price_class
    ]
    patience = stats.patience or 5.0
    runs = [(t, r) for t in pool if (r := replay_thread(t, ladder, stats.floor, patience, final_cap)) is not None]
    if not runs:
        return None
    real = [_real_share(t, stats.floor) for t, _ in runs]
    return FinalsRow(
        stats.dealer,
        stats.price_class,
        lift,
        cap,
        final_cap,
        ladder,
        len(runs),
        tuple((r.thread, r.price) for _, r in runs if r.price is not None),
        round(mean(r.share for _, r in runs), 3),
        round(mean(real), 3),
    )


def _real_share(t: DealerThread, floor: int) -> float:
    opening = t.opening_ask
    if t.fill_price is None or opening is None or opening <= floor:
        return 0.0
    return round(min(1.0, max(0.0, (opening - t.fill_price) / (opening - floor))), 4)


DEFAULT_LIFTS = (0.0, 0.15, 0.25)
LIFT_HELP = "dealer_final_lift values to replay (repeat; default 0, 0.15 and 0.25)"


def finals(
    lift: Annotated[list[float] | None, typer.Option("--lift", help=LIFT_HELP)] = None,
    dealer: Annotated[str | None, typer.Option(help="Only this dealer (e.g. chato)")] = None,
    show_threads: Annotated[bool, typer.Option("--threads", help="List every replayed deal")] = False,
) -> None:
    """Replay the captured feed's dealer threads under each `dealer_final_lift`: deals, prices, shares, cash."""
    from bazaar_agent.cli import _events
    from bazaar_agent.guardrails import load_guardrails
    from bazaar_agent.intel import dealer_threads

    settings_rules = load_guardrails().rules
    events = _events(False)
    rows = finals_rows(
        dealer_threads(events, None), settings_rules, lift or DEFAULT_LIFTS, dealer
    )  # every team's threads
    table = Table(
        title=f"dealer finals replayed on {len(events)} feed events (a model: pessimistic, same for every lift)"
    )
    for col in (
        "lift",
        "dealer",
        "class",
        "cap → final cap",
        "ladder",
        "threads",
        "deals",
        "mean price",
        "share",
        "teams' share",
        "per hour",
    ):
        table.add_column(col)
    for r in rows:
        prices = r.prices
        table.add_row(
            f"{r.lift:g}",
            r.dealer,
            r.price_class,
            f"{r.cap} → {r.final_cap}",
            str(r.ladder),
            str(r.threads),
            str(len(r.deals)),
            f"{mean(prices):.1f}" if prices else "-",
            f"{r.share:.3f}",
            f"{r.real_share:.3f}",
            str(r.per_hour(settings_rules.max_spend_per_game_hour)),
        )
    console.print(table)
    if show_threads:
        for r in rows:
            if r.deals:
                deals = ", ".join(f"thread {t} at {p}" for t, p in r.deals)
                console.print(f"lift {r.lift:g} {r.dealer} {r.price_class}: {deals}")


def register(app: typer.Typer) -> None:
    app.command("finals")(finals)
