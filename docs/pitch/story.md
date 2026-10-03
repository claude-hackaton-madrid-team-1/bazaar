# The story, in five acts (Team 1, Sunday pitch)

Draft as of Sat 3 Oct, ~05:00. Every number has a source: a report, a PR or a test. Re-check the Saturday numbers
before Sunday (§ "Fill in on Sunday"). No private value is in this file: no affinities, no duel limits, no card
values.

**One line:** we built a negotiation agent where **structure binds and language only persuades**. Then we used it
to learn the game's real rules from its own data, and found that the best move is often to say less.

---

## Act 1: the problem (30 s)

Causa Prima is building an agent-to-agent network for finance teams, starting with invoices. The Bazaar is a
sandbox for the same question: can an agent negotiate with other agents, and with LLM personas, without being
talked out of its money?

The game's one rule is ours too: **"Words persuade, structure binds."** Only a structured offer that the
counterparty accepts moves anything. Everything else is talk, and talk can lie, flatter or inject.

The four arenas map onto invoice work:

| Arena | What scores | The invoice analogue |
|---|---|---|
| Dealers | the share of each dealer's price range we capture (best three deals per level, buying or selling) | negotiating with a supplier persona |
| Team trades | value at our private valuations | swaps where each side values things differently |
| Duels | the share of the pie, shrinking every round of talk | the time cost of haggling an invoice |
| Duels II/III: price + delivery day | a pie that grows when both sides trade on what each cares about | **amount vs payment terms** |
| Market Test | matching a synthetic book as a broker | a clearing house between payers and payees |

## Act 2: how we read the game (60 s): measure, don't guess

1. **The duel decay rule, verified on all our real deals.**
   - `result = |price − limit| × 0.94^rounds`, exactly, on 8 of 8 practice deals.
   - `rounds = min(our priced messages, the rival's)`, on 26 of 26 payloads.
   - So every round of talk costs 6 % of the pie (8 % on Duels II, 10 % on Duels III); a round is one priced message from each side.
   - On Friday our v1 player countered every tick: 6.85 rounds per deal.
   - Sources: PLAN.md "Facts", W2a #80.
2. **Silence is free.** In the 12 practice duels we never answered, rivals conceded by themselves. Accepting their
   best offer would have earned 195 P, against 121.7 P for our v1 replayed on the same duels (W2a #80).
3. **The scoreboard, reverse-engineered.**
   - A score model fitted only on public data puts us at 8.26 at tick 159, against the official **8.34**.
   - RMSE 0.34 over 38 snapshots; the tick-30 board is matched with MAE 0.47 over 18 teams.
   - It explains why our score fell 10.76 → 8.34 while we did nothing: the other teams' Chato deals raised the
     top-3 mean (W5 #78).
4. **The unlock rule.** Level 2 opened after 3 negotiated buys with the previous dealer. Sales don't count, and
   neither does a deal at the opening price (B21 #119, from every `level.unlocked` event).
5. **Even the clock.**
   - Friday started 80 min late, so game hour 4.0 is not Saturday 09:00: the clock resumes at h 2.65.
   - We carry both timetables, and a check at 08:55 picks one (B6 #102).

## Act 3: what we built (60 s)

- **The binding path is deterministic.** Prices, accepts, limits and quantities are computed by code, inside
  `GUARDRAILS.md`, an executable policy file:
  - a cash floor;
  - price caps per rarity;
  - spend per game hour;
  - one accept per tick;
  - a kill switch read every tick.
- **The LLM writes words only.** A red team ran **168 hostile cases** through every path that reads counterparty
  text: duel messages, dealer threads, venue names, feed payloads, broker notices. Result: **0 binding fields
  changed** (W5 #78). Planting a bug that reads a number from the rival's text fails 34 of 48 cases, so the tests
  bite.
- **The services.** Taker, maker and duel player run on Railway. They share one Postgres ledger, so the one-accept
  limit and the spend cap hold across processes and laptops.
- **The rest:**
  - a full local simulator of the organisers' API (`bazaar-sim`);
  - OpenTelemetry traces with per-decision evals in Phoenix;
  - a read-only operator cockpit (B22 #122).
- **How we built it overnight.**
  - Up to 20 Claude Code sessions in parallel, each on its own branch and draft PR.
  - An **independent reviewer** session re-ran every claim (r1).
  - An **adversarial "bite hunter"** proved failure modes with tests (r2): 20+ findings, e.g. "the live maker
    cancels hand-posted offers", "PAUSE does not stop cancels".
  - An **integration rehearsal** merged 12 PRs at their heads: 2,746 tests green, 9 cross-PR fixes (B5 #120).
  - Nothing merged without the human.

## Act 4: what we learned, with numbers (90 s)

| Finding | Number | Source |
|---|---|---|
| **Talk less, earn more.** Duel policy v2 anchors once, holds while the rival concedes, and accepts when waiting costs more than it gains. | 22.06 vs 14.35 P per duel over 16,800 simulated duels; rounds per deal **1.23 vs 6.85**; lift 1.42× at decay 0.06/0.08 and 1.55× at 0.08/0.10; **0** closes outside our limit | W2a #80, W2b #86 |
| v2 replayed on the real duels we ignored | 178.4 vs 121.7 P (the oracle: 195) | W2a #80 |
| **Not leaking our limit.** Against a rival that estimates it from our offers: | v1 gives its floor away within 6 % by tick 3 in 96 % of duels; v2 in 0 % by tick 3 (41 % by tick 6); against an oracle squeezer, v2 keeps 0.159 of the pie vs v1's 0.071 | B11 #97 |
| **Ladder.** A floor table per dealer, built from every team's public threads | Abuela uncommons, share of range: 0.84 → **0.945** (model), 0.80 → **0.973** (real replay); 0 repeated prices | W3 #81 |
| **Market making is mostly about being open.** | Even a clairvoyant broker beats the free stall by only +0.03–0.06 efficiency at p50, so a venue that is open every session matters more than a cleverer matcher | W1a #77, W1b #84 |
| **Honest negative results** | Cross-venue arbitrage on Friday: **0** crossings net of fees across 636 offers; packs: luck never scores | W8, W7 #87 |
| **Rival models** | an affinity posterior over 720 permutations per team: log loss 1.14 vs 1.39 for a uniform guess | W4 #79 |

*Update, Sat 3 Oct evening:* our board venue v19 opened around game hour 3.6 with the exact broker and scores exactly the free stall's
0.5 bench; no other team has traded on it yet (`docs/briefing.md`, "Our own market").

## Act 5: what's next, for agent-to-agent finance (30 s)

1. **Policies as data, structure as contract.** An invoice agent should expose its binding limits as a reviewed
   file like `GUARDRAILS.md`, never as prompt text.
2. **Price the time of talking.** Every round of negotiation has a cost, as the duels' decay showed. In invoices
   that cost is days of float. A decay-aware acceptance rule closes faster without giving ground.
3. **Trade on what each side cares about.** Duels II's second issue (delivery day) is payment terms. The pie grows
   when amount and terms are traded against each other. We built the signed-days module behind a switch, waiting
   for the first real payload to confirm the sign (B8).
4. **Make agents auditable.** Every decision is traced, scored and replayable. A reviewer agent and an adversarial
   agent check the builders. That is how a finance team can trust an agent.

**What we would do differently:**
- Read the decay rule on Friday's first duel instead of after it.
- Ship the cockpit before going live.
- Merge less while live: every merge redeploys the trading services.

---

## Fill in on Sunday (from the live data; keep private values out)

- Our Saturday round score and rank: official `/me` → `score`, plus a leaderboard snapshot.
- Duels I and II: deals and mean pie share (`bazaar evals report`), with the v1/v2 choice and when it was made.
- The ladder: best three per level and the levels reached (`bazaar cockpit` → Ladder; `score.ladder_points`).
- The Market Test: whether the free stall scored for us (gate G3) and the sessions with a venue.
- Saturday's clock column (resume or jump) and what G0 saw at 08:55.
