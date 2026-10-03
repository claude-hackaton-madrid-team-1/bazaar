"""B11 endgame squeeze: the trade-off curve of `duel_endgame_min_share` on our arena, as Markdown on stdout.

    uv run python scripts/duel_b11.py [--quick]

Pie share against the exploiters (`oracle_squeezer` knows our limit, `curve_inferrer` inverts our concession
curve from our offers), and mean result and deal rate against the honest zoo relative to today's v2, both
within-tick orders. Offline only. W2a's
exploiters (PR #97) are the external check; docs/night/b11-endgame.md has both tables.
"""

from __future__ import annotations

import argparse

from bazaar_agent import duel_arena as arena
from bazaar_agent.agents.duel_v2 import V2Params

DECAYS = (0.08, 0.10)
TICKS = (12, 16)
SETTINGS = [("today", V2Params())] + [
    (f"duel_endgame_ticks 1, min share {ms}", V2Params(endgame_ticks=1, min_share=ms))
    for ms in (0.2, 0.3, 0.4, 0.5, 0.6)
]


def honest_run(params: V2Params, n: int, team_first: bool) -> arena.Summary:
    pol = {"v2": arena.v2_policy(params)}
    return arena.summarize(arena.tournament(pol, scenarios=n, decays=DECAYS, ticks=TICKS, team_first=team_first)["v2"])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--quick", action="store_true", help="40 scenarios per cell instead of 200")
    n = 40 if parser.parse_args().quick else 200
    honest = {tf: honest_run(V2Params(), n, tf) for tf in (False, True)}
    head = "| setting | exploiters' pie share to us | oracle squeezer | curve inferrer | honest P / deals (rival first)"
    print(head + " | (we move first) | outside |")
    print("|---|---|---|---|---|---|---|")
    for name, params in SETTINGS:
        pol = {"v2": arena.v2_policy(params)}
        ex = arena.tournament(pol, styles=arena.EXPLOITERS, scenarios=n, decays=DECAYS, ticks=TICKS)["v2"]
        e = arena.summarize(ex)
        by = {s: arena.summarize([o for o in ex if o.style == s]).mean_share for s in arena.EXPLOITERS}
        cells, outside = [], e.outside
        for tf in (False, True):
            h = honest_run(params, n, tf)
            cells.append(f"{h.mean_result / honest[tf].mean_result:.3f} / {h.deal_rate / honest[tf].deal_rate:.3f}")
            outside += h.outside
        shares = f"{e.mean_share:.3f} | {by['oracle_squeezer']:.3f} | {by['curve_inferrer']:.3f}"
        print(f"| {name} | {shares} | {cells[0]} | {cells[1]} | {outside} |")
    ours = arena.tournament({"v2": arena.v2_policy()}, scenarios=n, decays=DECAYS)["v2"]
    share = arena.leakage(ours).get("inverted_within_2pct", 0)
    print(f"\nv2's offers through today's curve put our limit within 2 % in {share:.0%} of duels where it priced.")


if __name__ == "__main__":
    main()
