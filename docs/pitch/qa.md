# Likely judge questions, with short answers

Each answer fits in 20 seconds and ends on a number or a file. The sources are the night reports (`docs/night/`)
and the PRs named.

**1. What stops another team's agent from talking yours into a bad deal?**
Its words can't reach the binding path. Prices, accepts and limits are computed by code inside `GUARDRAILS.md`;
the LLM only drafts our text. We ran 168 hostile cases through every path that reads counterparty words: 0 binding
fields changed. A deliberately planted bug fails 34 of 48 of those cases, so the tests catch real mistakes (W5 #78).

**2. Did you use an LLM to negotiate at all?**
For words and for advice, yes. For anything binding, no. In duels, v2 turns LLM words off, because silence is
free under the decay rule. The dealers are LLM personas, but RULES.md says their prices come from their own rules, and
prompt injection changes what they say, never their prices. So our dealer bids follow a floor table, not a conversation.

**3. How do you know the duel rule? It isn't in the docs.**
We fitted it on our own deals. `result = |price − limit| × 0.94^rounds` matches 8 of 8 practice deals exactly, and
`rounds = min(our priced messages, theirs)` matches 26 of 26 payloads (W2a #80).

**4. Isn't a model trained on its own simulator just fooling itself?**
That's why we have three independent checks:
- **Replay on the real payloads:** v2 earns 178.4 P vs v1's 121.7 on the 12 practice duels we ignored; the oracle
  gets 195.
- **A rival zoo fitted to the real messages:** the median concession matches within 0.01–0.07 per style.
- **Adversarial exploiters** that try to read our limit: v2 keeps 0.159 of the pie against an all-knowing rival,
  vs 0.071 for v1 (B11 #97).

**5. What does the score model add?**
It explains the leaderboard. Fitted only on public data, it reproduces our official 8.34 (model 8.26, RMSE 0.34).
It showed that our score fell on Friday night because others did Chato deals, not because we did anything wrong.
It also prices each action. One Saturday round point is 0.40 final points, and Abuela's best three deals were the
cheapest points on the board, at about 54 P (W5 #78, B6 #102).

**6. Why didn't you open a venue on day one?**
Two reasons, and the second is measured:
- Under our own cash floor the bond didn't fit: 540 P needed, 503 P held (W7 #87).
- A clever broker beats the free auto stall by only +0.03–0.06 efficiency (W1a #77, W1b #84). Being open every
  session matters more than matching well, and the stall question (does it score for us?) is a gate we check on
  the first Market Test.

**7. How do you avoid breaking the rules on rate limits and fair play?**
- **Requests:** a per-tick budget for every loop, tested, with stagger offsets that keep the tick-edge burst under
  the 20-request bucket (W5 #78).
- **Fair play:** a per-counterparty cap, so no single team gets more than 25 % of our planned trade volume (W4 #79).
- **No self-dealing:** we never trade on our own venue.

**8. What happens when something goes wrong live?**
- **Stop:** one PAUSE file per service stops every write.
- **Clean up:** a flatten command cancels what's open.
- **Safe ledger:** the shared Postgres ledger keeps one-accept-per-tick and the hourly spend cap true across
  processes.
- **See it:** the read-only cockpit shows cash against the floor, ledger writers, agent lag and duel deadlines on
  one screen (B22 #122).
- **Rehearse it:** our bite hunter found failure modes before the game did. For example, the live maker cancels
  hand-posted offers, and PAUSE doesn't stop those cancels on main (r2, BITES.md).

**9. How did a small team build this much in a weekend?**
- **Parallel builders:** up to 20 Claude Code sessions overnight, each owning one item, with its own branch and
  draft PR.
- **An independent reviewer** re-ran every claim (r1).
- **An adversarial session** tried to break things with tests (r2).
- **An integration rehearsal** merged 12 PRs: 2,746 tests green, 9 cross-PR fixes (B5 #120).
- **The human merges.** Nothing went live without a decision in `docs/decisions.md`.

**10. What does this mean for Causa Prima's invoices?**
Three patterns transfer directly:
1. Binding terms live in a reviewed policy file, never in a prompt.
2. Negotiation time has a price, so the acceptance rule should account for it.
3. A two-issue negotiation (amount and payment terms) grows the pie when each side trades on what it values. Duels
   II is exactly that.

**11. What would you do differently?**
- Read the decay rule from the first practice duel instead of after the session.
- Ship the cockpit and the merge windows before going live: every merge redeploys the trading services.
- Look at the clock: Friday started 80 minutes late, which shifts the whole Saturday schedule.

**12. Where did it fail?**
- **Arbitrage:** 0 profitable crossings on Friday's data, so we keep it off (W8).
- **Pages:** under our price caps no page could be finished (W7 #87).
- **Duels:** v1 countered every tick and lost about 30 % of the pie to decay on Friday. That one we fixed.

**13. Is your agent fair to the dealers, or does it exploit them?**
We never use prompt injection. Our bids rise in small distinct steps inside a hard maximum and never repeat a
price, which is what the rules reward ("small steps earn small steps").

**14. How much of this is the model versus your code?**
The model decides nothing binding. Everything that moves cash or cards is deterministic and tested: about 850
tests on main's branch line, 2,746 in the full integration. The model writes words, summarises and advises; a
decided "no" from the advisor can veto, but never adds risk.
