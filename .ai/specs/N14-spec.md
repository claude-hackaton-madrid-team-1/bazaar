# N14 — RAG-driven strategies per mechanic (hard dealers, packs, supply, custom markets, duels, new pages)  (per-task spec)

- Task id: N14 (migrated from GitHub issue(s) #22, #9, #8)
- Priority: P1
- Status: ⬜ starts when the learner (#96) merges in the 09:30 window; the ladder pieces (#8, #9) live in N3's auto-evolve (#112).
- Backlog source: local (`.ai/specs`). GitHub issues are not used any more (migrated and closed 2026-10-03).
- Traces up to: [`01-spec.md`](./01-spec.md)  ·  Indexed in: [`02-plan.md`](./02-plan.md)

## Goal
Every mechanic reads the learner's lessons through the hybrid recall and writes its outcome back: hard dealers (learned curves, blockers, when to walk), packs (EV with supply, 3/hour), supply and scarcity (the 270-card map), custom markets, duels, new pages.

## Acceptance criteria (each MUST be testable)
- [ ] 1. Hard dealers first: the taker's dealer plan reads learned curves and blockers (recall) and logs which lesson changed a bid.
- [ ] 2. Packs: buy only when EV (with supply and the page bonus) beats the price; at most 3/hour.
- [ ] 3. Supply map: scarcity per card from the 270 assets + opened packs feeds valuation.

## Slice N14a: hard dealers (2026-10-03, worker ctx_1d501316eaea, stacked on N3 PR B #112)
Covers criterion 1. This slice sets prices only: the words belong to N16 and the accept inspector to S1 (`agents/inspector.py`).
- [ ] a. The taker builds a per-dealer plan from recall: the learned ladder policy (a `learnings` row,
  kind `policy`), the dealer's curve (patience, opening ask, a bid it ignored) and the blockers. Every
  `dealer_open` and `dealer_bid` row records which learning changed the bid (`changed_by`) and the lessons
  recalled for that dealer (`recalled`). Neither key is on the public `/state` allow-list.
- [ ] b. A dealer's final offer is its limit, and the dealer walks if we refuse it. `dealer_final_lift`
  (GUARDRAILS, default 0 = today's behaviour) lets us accept a final, or bid exactly at it, up to the
  rarity cap × (1 + lift). The accepted price never exceeds our value minus `min_buy_surplus`, the lift
  applies to cards only (never packs), and our own bids still never pass the cap. With the lift on, the
  patience play starts low enough that the dealer names its final before our bids run out: step 1, about
  patience + 3 distinct bids (at least 9), and never below a bid the dealer ignored or 40 % of its opening ask.
  Evidence: Chato's uncommon finals were 28, 29 and 29 for t03 (start 13, step 1).
- [ ] c. L3-L5 readiness: every active dealer we have unlocked is planned the same way, with no hard-coded
  dealer. An unknown dealer gets neutral words and the default patience. Trickster bait in a dealer thread
  is never accepted and never bid into: a different card, a lower rarity, our assets wanted, cash given on
  a buy, or a `final` bait on the wrong item (tests on the desk). Epics and legendaries cannot be bought
  until they have a cap.
- [ ] d. Simulator proof per dealer (Abuela, Chato) on our own port, with `scripts/sim_smoke.py` green.
- Decision (coordinator, 05:40, Jev undecided at 0.31): ship `dealer_final_lift` = 0. Omar decides the value
  around 08:00, using the replay numbers for 0.15 and 0.25 in the PR.

## Source (the original issue text, verbatim)

### #22 — [intel] Supply map: scan of the 270 cards and scarcity (no epics or legendaries)

Migrated from https://github.com/claude-hackaton-madrid-team-1/bazaar/issues/22 on 2026-10-03 (closed there).

## Finding (real API, read-only scan of `/api/cards/{id}`)
- `GET /api/cards/{id}` works with our team key for **any** asset. It returns card, serial and history, but another team's ownership is anonymised (`"owner":"a team"`).
- There are **270 assets**, ids 1–270: 18 starting hands × 15 cards, in **consecutive blocks of 15**. Block 1 (ids 1–15) is ours, t01. 271 returns `unknown_asset`, so new ids (opened packs and mints) can be tracked as they appear.
- Each hand: 11 commons, 3 uncommons and 1 rare, spread across the 4 published sets.

## Scarcity in circulation (the important part)
- **Epics and legendaries: 0 in circulation** (LAV/MAL/LAT/SAL-11 and -12). They only come from silver or gold packs, which Abuela doesn't sell. The neighbourhood pack only gives commons and uncommons.
- **Rares:**
  | Card | Total copies |
  |---|---|
  | LAV-09 | 1 |
  | MAL-09 | 1 |
  | LAV-10 | 2 |
  | MAL-10 | 2 |
  | LAT-10 | 2 |
  | LAT-09 | 3 (one is ours) |
  | SAL-09 | 3 |
  | SAL-10 | 4 |
- **Scarce uncommons:** MAL-07 has a single copy; LAV-06, LAT-06 and SAL-08 have 2 each.
- **Most abundant commons:** MAL-02 (15), LAV-01 (13), LAT-01 (13), MAL-01 (12), LAV-03 (12).
- **A page needs both of its rares.** With what exists now, only 1 complete LAV page and 1 complete MAL page fit in the whole game, until packs mint more rares (welcome pack: 40% rare in slot 3; silver: rare almost guaranteed). **Whoever holds LAV-09 or MAL-09 holds that page's bottleneck.**

## What to do
- [ ] Save the scan (`cards.jsonl`) as a snapshot and repeat it every N ticks at ≤4 req/s (the limit is 5 req/s per key). Detect new assets (ids > 270) and changes of hands (`history`).
- [ ] "Who has what" map per anonymous block. If the feed settlements reveal teams (#21), cross-reference them to de-anonymise.
- [ ] Feed #14: prioritise the scarce cards that complete a page for us, and know how many potential sellers there are for each card.

## Acceptance criteria
- [ ] Reproducible snapshot and diff between snapshots (additions and movements).
- [ ] Per-card scarcity table, kept up to date.

**Comment by serban-marius:**

**Audit 2026-10-03 (main @ 1a89f86 + shared DB + monitor captures)** — status: still open, scarcity is computed from the catalog, but the card scanner and the snapshots don't exist.

- 🟡 Partial:
  - Scarcity from the catalog's minted counts (`src/bazaar_agent/strategy.py:247-265`, `bazaar strategy`).
  - Likely holders of each card inferred from the public feed (`strategy.py:153`).
- ❌ Missing:
  - A scanner on `/api/cards/{id}`. The SDK has `card()` (`vendor/bazaar-kit/bazaar_sdk.py:168`), but nothing in `src/` calls it.
  - The `cards.jsonl` snapshot, and a diff between snapshots (new ids above 270, and changes of hands from `history`).
  - A map of who holds what per anonymous block. The shared DB `cards` table is **empty**.

### #9 — [personas] Per-round ladder maximizer + unlock/egg watcher (reach L2)

Migrated from https://github.com/claude-hackaton-madrid-team-1/bazaar/issues/9 on 2026-10-03 (closed there).

## Context
- Ladder: *"the best deal shares per persona level weighted by level (0–1)"*. Only **the best deal per level and per round** counts, so doing many deals with Abuela doesn't raise the ladder.
- Higher levels weigh more: L1 Friendly, L2 Sharp, L3 Collector, L4 Tricksters, L5 Banker.
- Unlocks: *"open from the start, early for teams that proved themselves elsewhere, and for everyone at a published time"*. The team's level is the highest persona unlocked.
- **Opening our own venue requires level 2** (see #11).
- Eggs: triggered by a keyword in the message or by chance. They are once per team and there is a global cap of ~15 across the 18 teams (**it's a race**). They can yield hidden cards.

## What to do
- **Per-round maximizer:** track the best share per level in each round, have ≥5 chats per persona per round, and stop at a level once the share exceeds 0.95. Reset at the start of round 2 (h4) and round 3 (h18).
- **Watcher:** poll `/api/levels` and `/api/dealers` and listen for `level.announced`, `level.activated`, `persona.open_to_all`, `level.unlocked` and `egg.found`. Alert immediately.
- **Find out the actual rule for reaching L2.** The "3 deals" is Abuela's `early_min_deals`; it is not verified that it is the L2 rule, and `/api/levels` is empty.
- Store and compare every persona response (the hints point to eggs). Try 1–2 candidate keywords taken from the bio in each message.

## Acceptance criteria
- [ ] Alert in <1 tick on any unlock or egg event.
- [ ] Table of the best share per level and round.
- [ ] L2 rule documented.

**Comment by serban-marius:**

**Important correction (RULES.md):**
- **Ladder:** **the 3 best deals per level** count (*"your best three deals per level count, a missing one as zero, higher levels weigh more"*), not just the best one. We need ≥3 good deals per level and round.
- **Unlock:** a level opens to whoever has made *"a few good deals with the one before"*. **A deal at the opening price doesn't count**; a negotiated one does. It then opens to everyone after a head start.
- **Eggs:** *"What never counts: … easter eggs"*. Lower their priority.

**Comment by serban-marius:**

**Audit 2026-10-03 (main @ 1a89f86 + shared DB + monitor captures)** — status: still open, level alerts and polling work, but egg alerts, alerts on our own unlock and the ladder table do not.

- ✅ Done:
  - `level.*` alerts (`src/bazaar_agent/monitor.py:30`).
  - `/api/levels` and `/api/dealers` are polled (`agents/monitoring.py:174`).
- ❌ Missing:
  - `egg.found` is not in `ALERT_EVENT_TYPES` (`monitor.py:30`).
  - Our own unlock never raises an alert, because the `not is_ours(...)` filter drops it (`monitor.py:133`).
  - A table of the best three shares per level and round.
  - The L2 rule written down. The tick-98 unlock text ("4 deals with abuela") is the evidence to write it from.
  - Egg and keyword probing (low priority: eggs don't score).
- Data: we reached L2 at tick 98, and El Chato opened to everyone at tick 158. At tick 149 we were level 2 with `ladder_points` 0.058.
- Notes:
  - Make the alert fixes after PR #43, because it rewrites `monitor.py`.
  - #58 (open) adds an `eval_ladder` report (best 3 per level, a missing deal counts as 0): L1 Abuela 0.733, L2 Chato 0. Once merged, that could cover the table criterion.

**Comment by ogarciarevett:**

Status update: #58 (merged) added the table criterion. `uv run bazaar evals report` prints `eval_ladder` (best three shares per level, a missing deal counts as 0). Still open here: `egg.found` is not in `ALERT_EVENT_TYPES` (`monitor.py`), our own unlock raises no alert, and the L2 rule is not written down (evidence: the tick-98 unlock text).

### #8 — [personas] Negotiator that probes the concession curve (no strikes)

Migrated from https://github.com/claude-hackaton-madrid-team-1/bazaar/issues/8 on 2026-10-03 (closed there).

## Context
Persona prices are decided by **code, not the LLM**:
- *"Traits colour the words, not the price"*
- *"The persona's negotiating brain is code"*

The curve is `target(r) = open + (limit − open)·min(1, r/rounds_to_limit)^(1/β)`; Abuela is concessive (β > 1). Also, *"never moves faster than the team's last step (at least 2% of book)"*: if you don't move, neither does she. When her patience runs out, she only accepts offers at her limit or walks away. *"The words are free; the structured offers bind."*

The ladder score is the **share of the persona's price range captured**, only on negotiated deals (both sides made offers).

## What to do
- Negotiator that probes the curve: start low (~50% of book), raise ≥2% of book per tick, always read the other side's **structured offer** and log every counter.
- Estimate `open`, `limit` and β per persona.
- Accept when the counter stops moving or a "final" arrives.
- **Welcome offer:** the first conversation with each persona opens at a welcome price. Save it for the item with the highest list price.
- **Politeness:** a judge scores each message from 0 to 3 and *"politeness can soften the limit"*. Friendly, varied messages.
- Buying the `sobre_barrio` from Abuela pays off (list 26 vs. a book EV of 33.8) and it must be done at the **start of every game hour**, because the limit rises when stock runs out.

## Anti-strikes (mandatory)
The judge tags `injection, abuse, spam, false_claim`, and enough strikes trigger a cool-off. No instructions to the persona and nothing like "ignore previous instructions" (that is literally its test message), no made-up rival bids, no repeated phrases. Numbers in an LLM line that don't add up are discarded.

## Acceptance criteria
- [ ] Never accepts the first offer without countering at least once.
- [ ] Share recorded per deal and curve estimate per persona.
- [ ] Zero strikes in a test session.

**Comment by serban-marius:**

**Correction (RULES.md):**
- There can only be **one open conversation per dealer at a time**.
- Each conversation has its own secret limit. Repeating the same price earns no concession.
- An offer above our cash is rejected immediately (`insufficient_cash`).
- The closing reason comes in `closed_reason`: `persona_quota`, `sold_out`, `cooloff` with `until_tick`…
- Prompt injection is allowed but **never changes prices**, and some dealers stop talking to you.

**Comment by serban-marius:**

**Real Abuela data** (`/api/dealers/abuela`, unchanged at tick 0):
- Sells `sobre_barrio` with `list_price` 26 and `opening_ask` 30, 3 per team per hour.
- Sells commons at 10 and uncommons at 25. Buys commons and uncommons. Max 8 deals per team per hour.
- Offers in dealer threads **expire after 2 ticks** (`expires_tick = created_tick + 2`).
- Shape of the buy offer: `give:{cash:N}`, `want:{types:["pack:sobre_barrio"]}`.
- What other teams open with: t04 opened at 12 P and t16 at 15 P for the pack (book EV 33.8).

**The public feed shows other teams' negotiations with Abuela** (#21): we can calibrate her curve without spending our own deals.

**Comment by serban-marius:**

**Audit 2026-10-03 (main @ 1a89f86 + shared DB + monitor captures)** — status: still open, the ladder mechanics are in place, but there is no curve estimate, no share per deal and no zero-strike evidence.

- ✅ Done:
  - Bids rise strictly, never repeat, and are read from the structured offer. A `final` means take it or walk.
  - Spanish templates and a guard on the LLM's output.
  - `bazaar curves` (`src/bazaar_agent/cli.py:137`): fill rate, plus opening and final medians.
- ❌ Missing:
  - An `open` / `limit` / β estimate per persona.
  - The share recorded per deal.
  - The welcome-offer logic.
  - Evidence of zero strikes in a test session.
- Findings:
  - We can accept the opening ask without countering once, which breaks criterion 1. Fix on branch `fix/dealer-ladder-counter`.
  - At tick 149, `ladder_points` 0.058 is our only nonzero scoring component.
  - Our Abuela deals closed at 7/7, 9/12, 9/12 and 22/29 (price / her opening ask).
  - El Chato matches our step size ("I match what you move").
- Related: #58 (open) adds a dealer eval of each deal's share of the dealer's learned range, and calls out deals taken at the opening price. Its numbers: dealer mean 0.464; ladder best-3 L1 Abuela 0.733, L2 Chato 0. Once merged, it covers most of criterion 2.
