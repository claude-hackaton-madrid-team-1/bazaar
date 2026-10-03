> Sanitised copy of `_night/PLAN.md` (night shift of Fri 2 → Sat 3 Oct 2026, file state at ~07:15 Madrid).
> `[private]` marks redacted private game values (cash, card values, affinities, our price caps and bid
> ladders, album state). Numbers inside test descriptions (`tests/bites/*`, chaos runs, proof tests) are
> fixture values, not our real ones. See [../SUMMARY.md](../SUMMARY.md) and [../INDEX.md](../INDEX.md).

# Overnight plan (Fable strategist, 01:45 Sat; verified and adjusted by the orchestrator session)

## Decisions by Marius (02:30)
- Open PRs stay unmerged; night work stacks on their branches. No merges tonight.
- Each workstream runs in its own Orca session and ends as a DRAFT PR + report. Nothing goes live.
- No venue opening and no `cash_floor` change tonight ("we'll see tomorrow").
- Duel policy v2: build it and prove it, but ship it as a PR only, behind a parameter whose default is today's policy.

## Facts the plan rests on
1. **Duel decay is charged per exchange, not per tick.** VERIFIED on all 8 real practice deals (shared DB `duels`):
   `result = |price − limit| × 0.94^rounds` exactly (18.8, 16.5, 20.1, 13.2, 18.8, 21.3, 39.0, 29.2), and
   `rounds = min(our priced messages, rival priced messages)`. Every priced counter we send costs 6 % of the pie
   (8 % on Duels II, 10 % on Sunday). Our v1 counters every tick: 7–8 rounds per deal, ~30 % of surplus lost Friday.
   In the 12 practice duels we never answered, rivals conceded unilaterally into our zone: accepting their best offer
   would have realised ~195 P (16.3 P/duel) vs 176.9 P on the 14 duels we played (12.6 P/duel). Unverified: whether an
   accept itself adds a round.
2. **Market-making (30 pts) is 0 for us.** Rival venues: t06 board 50 bps, t12 board 0 bps, t13 board 100 bps,
   t02 auto 0 bps; likely running the kit's starter broker, which by its own docstring earns half the bench points.
   Market Test every 2 h (Sat h5, 7, 9, 11, 13, 15, 16-hard, 17; Sun h19, 21); each session counts our best open
   venue; 0.5 = stall-level, 1.0 = top-3 mean. Our venue + exact max-weight matcher + broker loop: PR #71 (build-only).
3. **Days in two-issue duels.** `days_meaning` text "primas you gain (+) or lose (−) per delivery day, 0–10" comes
   from the SIMULATOR (#55), NOT the real game: real practice payloads have `days_meaning: null`; the official openapi
   only says "Primas per delivery day". So PR #60's worst-case `abs(weight)` is the correct stance until a real
   Duels II payload confirms the sign. Build the signed version behind a switch, default off.

## Workstreams
### W1 — Venue + bench broker that beats the stall (Market 0 → 10–20 pts). Highest-leverage bet.
- W1a (sim realism, base: #55 branch): extend `src/bazaar_sim/broker.py` bench with the #12 parameters: traders who
  leave after 1–2 ticks (25 % normal / 35 % hard), quotes relaxing toward hidden limits per tick, firm share 0.2 /
  0.35, 10 vs 12 traders, and a stall (auto) replica scored in the same run so efficiency is relative.
- W1b (broker edge, base: #71 branch): `TraderModel` per bench trader id (quote history → limit estimate, leave
  probability), match impatient traders the tick they appear, hold patient pairs for better crosses, read the book
  2–3× per tick during a bench run within the rate budget, exact max-weight pairing from `matcher.py`. Tournament:
  1,000 random books × {normal, hard} × {stall, greedy, ours}. Pull W1a's branch when it lands.
- Go/no-go: p50 efficiency ≥ 0.85 of the true-limit optimum and ≥ stall + 0.15 in both presets; 0 infeasible matches;
  within the rate budget. Morning plan: a deliberate non-crossing probe to learn whether the real
  `POST /api/broker/matches` honours hidden limits.

### W2 — Duel policy v2: "silence is free" + decay-aware acceptance (+5–8 pts)
- W2a (rival zoo, base: #55 branch): 6 rival styles fitted to the 26 real payloads (`tests/fixtures/evals/duels_done.json`):
  linear conceder, convex conceder, one-shot-then-silent, tit-for-tat, no-show, the sim's 0.9→0.35 bot; signed
  `rival_days_weight`; decays 0.06/0.08/0.10; 12 and 16 tick duels; a replay harness that scores any policy on the
  real payloads where the rival's moves were unilateral.
- W2b (policy v2, base: #60 branch): anchor once, then hold while the rival concedes; at most
  `duel_max_own_offers` priced messages (default 2 in v2); stall-counter after k unchanged ticks; accept when
  `surplus_now ≥ E[surplus_next] × (1 − d)` or in the endgame; strictly inside the limit always (keep #60's guard);
  signed days behind a switch (default worst case). Everything as GUARDRAILS.md parameters with `duel_policy = v1`
  as the default. `llm_words` off for duels in v2. Pull W2a's zoo when it lands.
- Go/no-go: 6 styles × 200 scenarios × 2 roles × 2 decays: mean result ≥ 1.4 × v1; deal rate ≥ v1 vs conceders and
  ≥ 0.9 × v1 vs one-shot rivals; 0 outside-limit closes in 10k duels; replay beats v1 on the 12 unanswered duels.

### W3 — Ladder maximiser (+3–5 pts, early L3) (base: #61 branch)
Floor table per dealer × rarity from every team's threads (`dealer_curves`, fixture + DB read-only); `BidPlan`
generator (start, step and max set relative to the dealer floor: [private]); validate on the sim dealers; `ladder_plan.json` + a 09:00–10:30
schedule within 8 deals/dealer/hour and 3 packs/hour, Chato first. Go: backtest mean share ≥ 0.8, ≥ 90 % fill within
8 ticks, 0 repeated prices. Note: `cash_floor` 270 and `max_price_rare` [private] vs Chato's rare opening 97 limit this:
report the constraint, don't change the values.

### W4 — Trade desk (+2–4 pts) (base: #72 branch)
Rival affinity map from the public feed (which set each of 17 teams chases; every team has the same six affinities
shuffled) with confidence; the per-counterparty cap from #14 in maker/taker; 12 listings + 3 direct-thread proposals
for 09:00 and the [private] page buy list, as a dry-run plan. Fair play: every trade must have surplus for us; no
counterparty above 25 % of planned volume (never "feed another team").

### W5+W6 — Score simulator, red team, rate budget, MORNING.md (base: origin/main)
Score simulator (components normalised to the top-3 mean, round weights) reproducing 8.34 ± 0.5 at tick 159;
red-team tests feeding hostile text through every path that reads counterparty words (duel text, threads, venue
names, broker announcements) asserting no binding field changes (#24); a per-tick request budget for 30 s / 15 s
ticks across taker + maker + duels + monitor + broker ≤ 5 req/s; and at ~07:00 a MORNING.md that summarises every
night PR (read STATUS.md and `gh pr list --draft`), the go/no-go of each, and an ordered checklist for 08:30–11:30.

## Killed
Dealer prompt injection (words never move prices), mirror-duel learning (aliases unstable), eggs, fee tuning,
LLM-words polish (none score). Flags stay off.

### W7 — Page economics: what completing collections is worth, what it costs, and the cash plan (base: origin/main)
Added 02:20 at Marius's request. Key fact: completing pages does NOT score by itself (RULES.md scoring table has no
album term). A complete page only raises the private value of its cards (+25 % page bonus, a little more for master),
which scores only as surplus in team-to-team trades at private values (capped per trade and per counterparty).
Dealer purchases score through the ladder (share of the dealer's range), not through card value.
Deliver: (1) per missing card: value to us with and without the page bonus, expected price by source (rival teams via
tape/listings; dealers via dealer_curves), scarcity (minted, likely holders), surplus per primas; (2) which pages are
worth finishing and which aren't; (3) a cash allocation across competing uses — venue 270 P (Market 30 pts), ladder
deals, best-surplus purchases — for our cash ([private] at tick 159) plus any organiser grant (VERIFY from
tests/fixtures/api/get_api_schedule.anon.json / docs, don't assume), as scenarios (venue Saturday morning / later /
never); (4) the steps: what to buy, in what order, from whom, at what max price, within per-tick/hour limits;
(5) a small script (e.g. `bazaar plan pages`, dry run, read-only) that recomputes this from live data later.
Never commit our private affinities or values into the repo beyond what it already holds; the report may show them
only if the repo already does (strategy.py reads them live from /api/me — use example placeholders in committed docs
and keep real numbers in the PR body or the _night folder).

### W8 — Cross-venue arbitrage (base: fix/cash-spend-accounting, PR #72)
Added ~02:30 at Marius's request: `block_buying_held_cards = true` blocks arbitrage. Keep the rule as the default,
add a narrow, guarded exception.
Scoring mechanics (cash itself never scores): a buy scores `your_value(one more copy) − price` (a duplicate is
0.25× or 0.1× book × affinity, so the buy leg is usually NEGATIVE), the resale scores `price − your_value(that copy)`;
net = the spread minus fees, IF the per-trade / per-counterparty caps clip both legs symmetrically. RULES.md says
trade value is "capped per trade and per counterparty" without saying whether negatives are capped: analyse it, and
design so that each leg on its own stays within the caps (or the edge is robust to asymmetric clipping). The ring
guard re-scores one-sided pairs 50/50: avoid repeated round trips with the same counterparty.
Deliver:
1. An offline study on the captured feed (stream.jsonl, tape, offer.listed/cancelled across El Rastro 5 % + 1 P/card
   and the team venues t06 50 bps, t12 0 bps, t13 100 bps, t02 auto 0 bps): how many cross-venue or ask-vs-bid
   opportunities existed on Friday (ask on venue A + all fees < standing bid on venue B, or < a known rival's need
   price), their size, how long they lasted in ticks, and how many we could have executed under 1 accept/tick.
2. A scanner (`bazaar arb scan`, read-only) that lists live opportunities with net spread after every fee.
3. A guarded exception in the taker: buying a held card is allowed ONLY when a matching exit is executable now
   (a fresh standing bid on another venue we can hit next tick, re-read just before the accept), net spread after all
   fees ≥ `arb_min_net_spread`, total arbitrage inventory ≤ `arb_max_inventory_p`, and both legs pass every other
   guardrail (cash floor, caps, kill switch, accept slot). New GUARDRAILS.md params with defaults that keep today's
   behaviour (`arb_enabled = false`). The exit is executed with priority on the next tick; if it vanished, the card
   goes to the maker's normal sell-to-need flow, never dumped below `sell_min_value_ratio`.
4. Tests for each guard and for the fee arithmetic per venue. Report with the Friday numbers and a go/no-go.

#### W8 addendum (03:35, scope added by Marius via the orchestrator): guarded duplicate buys
Besides arbitrage, a held card may be worth buying AGAIN on its own surplus: `your_value(one more copy)` (book ×
affinity × marginal 0.25 / 0.1) − all-in price (ask + every fee) ≥ `dup_min_surplus`. Second guarded path next to
arbitrage in the held-card rule: `dup_buy_enabled = false` (default = today), `dup_min_surplus`, `dup_max_spend_per_hour`
(per-hour cap on duplicate spend); every other guardrail unchanged. The Friday study counts how many such buys were on
offer by affinity tier (tiers only in committed files; real numbers in the PR body / _night). Tests for the threshold
and the cap.
