# Likely judge questions, with short answers

Each answer fits in 20 seconds and ends on a number or a file. The sources are the night reports (`docs/night/`)
and the PRs named.

**1. What stops another team's agent from talking yours into a bad deal?**
Its words can't reach the binding path. Prices, accepts and limits are computed by code inside `GUARDRAILS.md`;
the LLM only drafts our text. We ran 168 hostile cases through every deterministic path that reads counterparty words: 0 binding
fields changed. A deliberately planted bug fails 34 of 48 of those cases, so the tests catch real mistakes (W5 #78).

**2. Did you use an LLM to negotiate at all?**
For words and for advice, yes. For anything binding, no. In duels, v2 turns LLM words off, because silence is
free under the decay rule. The dealers are LLM personas, but RULES.md says their prices come from their own rules, and
prompt injection changes what they say, never their prices. So our dealer bids follow a floor table, not a conversation.

**3. How do you know the duel rule? It isn't in the docs.**
We fitted it on our own deals. `result = |price − limit| × 0.94^rounds` matches 8 of 8 practice deals exactly, and
`rounds = min(our priced messages, theirs)` matches 26 of 26 payloads (W2a #80).

**4. Isn't a model trained on its own simulator just fooling itself?**
That's why we ran three checks, none of them fully out of sample (v2 was tuned on the zoo and partly on the replay,
and the exploiters are our own models):
- **Replay on the real payloads:** v2 earns 178.4 P vs v1's 121.7 on the 12 practice duels we ignored; the oracle
  gets 195.
- **A rival zoo fitted to the real messages:** the median final gap toward us matches the real
  messages within 0.01–0.07 on the 4 styles we could compare.
- **Adversarial exploiters** that try to read our limit: v2 keeps 0.159 of the pie against an all-knowing rival,
  vs 0.071 for v1 (B11 #97).

**5. What does the score model add?**
It explains the leaderboard. Fitted only on public data, and only on ticks before 140, it predicts our later official
score within 0.47 (7.87 vs 8.34). In-sample it fits 8.26, RMSE 0.34.
It showed that our score fell on Friday night because others did Chato deals, not because we did anything wrong.
It also prices each action. One Saturday round point is 0.40 final points, and Abuela's best three deals were the
cheapest points on the board, at about 40–54 P (W5 #78, B6 #102, W7).

**6. Why didn't you open a venue on day one?**
Two reasons, and the second is measured (on Friday's numbers):
- Under our own cash floor the bond didn't fit: 540 P needed, 503 P at best after the grant, 353 P
  before it if the clock resumes (W7 #87, B6 #102).
- The free stall already earns half the bench points, and even a clairvoyant broker beats it by only +0.03–0.06
  efficiency (W1a #77, W1b #84). A venue pays only if its broker beats the stall in most sessions: our edge broker
  adds about +0.17 final points simulated, and a broker that is down loses points (B2 #92). Whether the stall
  scores for us is a gate we check on the first Market Test.

**7. How do you avoid breaking the rules on rate limits and fair play?**
- **Requests:** a per-tick budget for every loop, tested. Sustained load is well under 5 req/s. The tick-edge burst
  at the ceiling is a known NO-GO; opt-in stagger offsets keep it under the 20-request bucket in the model (W5 #78,
  draft).
- **Fair play:** a per-counterparty cap (no team above 25 % of our planned trade volume) is built and tested, but
  off by default until our volume is large enough for it to make sense (W4, takeover #137).
- **No self-dealing:** the rules forbid trading on our own venue with the team key.

**8. What happens when something goes wrong live?**
- **Stop:** one PAUSE file per service stops new bids, accepts and posts. On main it does not stop the maker's
  cancels; #72's kill-switch hold fixes that and awaits merge.
- **Clean up:** a flatten command cancels what's open (in #72, awaiting merge).
- **Shared ledger:** a Postgres ledger holds one-accept-per-tick and the hourly spend cap across processes;
  its reconnect fix (#62) awaits merge and a redeploy can under-book it (r2), so we watch it in the cockpit.
- **See it:** the read-only cockpit shows cash against the floor, ledger writers, agent lag and duel deadlines on
  one screen (B22 #122).
- **Rehearse it:** our bite hunter found failure modes before the game did. For example, the live maker cancels
  hand-posted offers, and PAUSE doesn't stop those cancels on main (r2, BITES.md).

**9. How did a small team build this much in a weekend?**
- **Parallel builders:** about 15 Claude Code sessions overnight, each owning one item, with its own branch and
  draft PR.
- **An independent reviewer** (r1) reviewed the night's PRs and re-ran their key claims.
- **An adversarial session** tried to break things with tests (r2).
- **An integration rehearsal** merged 12 PRs: 2,746 tests green, 10 cross-PR fixes (B5 #120).
- **The human merges.** Every merge to main is done by a teammate; `docs/decisions.md` records the why.

**10. What does this mean for Causa Prima's invoices?**
Three patterns transfer directly:
1. Binding terms live in a reviewed policy file, never in a prompt.
2. Negotiation time has a price, so the acceptance rule should account for it.
3. A two-issue negotiation (amount and payment terms) grows the pie when each side trades on what it values. Duels
   II is exactly that.

**11. What would you do differently?**
- Read the decay rule from the first practice duel instead of after the session.
- Ship the cockpit and the merge windows before going live: every merge redeploys the trading services.
- Look at the clock: Friday's clock started about 81 minutes late, which shifts the whole Saturday schedule.

**12. Where did it fail?**
- **Arbitrage:** 0 profitable crossings on Friday's data, so we keep it off (W8).
- **Pages:** under our price caps no page could be finished (W7 #87).
- **Duels:** v1 countered every tick and lost about 30 % of the pie to decay on Friday. The fix (v2) is built and gated, now in #150 awaiting merge.

**13. Is your agent fair to the dealers, or does it exploit them?**
We never use prompt injection. Our bids rise in small distinct steps inside a hard maximum and never repeat a
price, which is what the rules reward ("small steps earn small steps").

**14. How much of this is the model versus your code?**
The model decides nothing binding. Everything that moves cash or cards is deterministic and tested: about 950
tests on main (the PR gate's last runs: 877, then 946), 2,746 in the full night integration. The model writes words, summarises and advises; a
decided "no" from the advisor can veto, but never adds risk.
