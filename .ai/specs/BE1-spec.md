# BE1 — Market Test bench edge on main, behind BAZAAR_BENCH_POLICY (port of Marius's #84)

Source: the coordinator's brief, Sat 2026-10-03 ~tick 766 (local backlog, no external tracker). Takes over the closed
PR #84 (Marius, `night/w1b-broker-edge`); #77's realistic bench (`bazaar_sim.bench`) is already on main.

## Goal
Give the maker's venue broker a Market Test policy that can beat the free stall (RULES.md: "Matching as well as the
free auto stall earns half the bench points; the full points go to the mean of the top three") without ever doing
worse than it on average in any modelled regime. Ship it OFF: the coordinator and Omar decide the switch.

## Design
- `agents/bench_model.py` and `agents/bench_edge.py`: #84's per-trader limit bands from quotes and its edge
  (maximum *estimated true* surplus among crossing pairs), ported verbatim. Probes (`cross = "limit"`), extra
  reads and holds are not wired: the broker only ever sends pairs whose quotes cross, as today.
- Safety guard (new, `bench_edge.edge_plan`): the edge's pairs go out only when their estimated true surplus beats
  the exact plan's by `EdgeConfig.guard_margin` (10 P) with at least as many pairs; otherwise the exact plan
  (stall-equal) goes out.
- `BrokerConfig.bench_policy` (`exact` default | `edge`), from `BAZAAR_BENCH_POLICY` in the venue keeper
  (case-insensitive; an unknown value is ignored with a loud line). Declared `preserve()` on `bazaar-maker` only.
- `scripts/bench_edge_proof.py`: stall vs exact vs edge (guarded) vs unguarded on `bazaar_sim.bench`, seeded.

## Acceptance criteria
1. Unset or `exact`: the broker's plan is today's (`plan_matches`), byte for byte in the decision rows.
2. `edge`: the bench pairs come from the guarded edge; decision rows say which policy chose them.
3. The guard: the exact plan unless the edge beats it by the margin with no fewer pairs (tests).
4. Proof: ≥ 1000 seeded books per preset (normal, hard) and regime: the guarded edge's mean efficiency ≥ the stall's
   in every regime; share of books below the stall and the worst book reported; 0 refused matches.
5. `BAZAAR_BENCH_POLICY` is `preserve()` on the maker only (IaC test); no value anywhere in the repo.
6. Full gate and the sim smoke pass.
