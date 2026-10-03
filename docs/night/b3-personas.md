# B3 · L3–L5 personas prep (night shift, 3 Oct 2026)

Draft PR #93, stacked on #81 (W3) and #61. Nothing went live. The data is Friday's public feed (1,089 dealer messages, 273 threads), the API fixtures, the kit and the explainer site.

## What is known (and what is not)
| level | dealer | known | source |
|---|---|---|---|
| L1 | Abuela Carmen ("Friendly") | Sells packs, commons and uncommons. Buys commons and uncommons. 8 deals per hour. | `/api/dealers`, feed |
| L2 | El Chato ("Sharp") | Sells uncommons at 33 and rares at 97. Buys uncommons and rares. Matches our step. | `level.activated` "how", feed |
| L3 | **Collector** | Only the tier name. Likely buys cards (the name); nothing is verified. | the game's front-end screens (#9, explainer site) |
| L4 | **Tricksters** | "Slip a lesser card into the structured offer while the text names a good one". A correct flag scores, a wrong one costs. | front-end screens, #10, RULES.md ("Some lie; flag…") |
| L5 | **Banker** | Only the tier name. | front-end screens |

How a level arrives (Friday's El Chato):
- `level.announced` came at tick 71 (teaser only).
- `level.activated` came at tick 98 with "how" and `opens_to_all_in_hours: 1.0`.
- Everyone else got in at tick 158.

**Early unlock evidence:**
- At activation, L2 opened to every team with **3 negotiated deals** with Abuela.
- Latecomers opened exactly when they reached 3 (t02 at tick 121, t04 at 122, t08 at 126, t16 at 132).
- t15 had 5 deals, 3 of them at her opening price, and never unlocked.
- So early L3 needs 3 deals with Chato away from his opening price. Under today's caps no Chato buy can fill (#81: `dealer_price_caps`). A sale may count, unverified.
- The server's "N deals" counts match our buy settlements for 10 of the 16 teams that have a count; the rest are unexplained.

## Trickster detection (`agents/inspector.py`)
- **`block`** (never accept): any structural mismatch with the thread's topic, such as another item, our assets in `want`, or cash given on a buy.
- **`flag`**, only on the trickster signature, all three at once:
  - another item, worth less;
  - words that claim the better one: the requested card by name or ref, a dearer card, or a rarity *as a card* ("the legendary.", "un cromo raro", "rare card");
  - words that do **not** name the bound item (naming it discloses the substitution, e.g. "No me queda X, te doy Y").
- **Trusted dealers:** Abuela and Chato are blocked but never flagged.
- **Rarity adjectives are not claims:** "qué raro, hijo" (how odd) and "an epic deal" never count.
- **Precision on Friday:** honest dealers' structure matched the topic in **1,017 of 1,017** offers, and the rule flags **0** of 1,022 offers. The only 5 blocks are threads whose topic our capture missed.
- **That precision is not yet proven against a real lie.** Friday had no structural mismatch with a known topic, so the words rule never ran on real data. The r1 review found three honest near-misses that flagged under the first version; they are fixed and kept as tests. This is why flags stay off until a real L4 thread is seen.
- **Crafted tricksters:** all flag. Cases:
  - the explainer's "La Dama de Serrano, the legendary. Only 120.", binding SAL-02;
  - a common for our LAV-08 while the words name LAV-08;
  - a common for a rare request ("un cromo raro");
  - a card instead of the pack.

  The near-misses all block but never flag: undressed mismatches, dearer cards, extra `want` items, other assets on a sale, unknown topics, and the three review cases.
- **Wiring:** every thread read in `bazaar dealer buy` (new `negotiate(on_thread=…)` hook), the desk and `bazaar dealer sell` runs `flag_step()`.
  - Each certain message is logged once, uncapped (`would flag message N (…allow_flags = false): <structural reason>`).
  - At most `max_flags_per_process` (GUARDRAILS.md, 2) flags are actually sent, and only if `guardrails.check(Action("flag"))` passes.
  - A denied or failed flag is re-checked on the next read, so allowing flags mid-run still sends it.
  - `flag_trusted_dealers` (abuela, chato) are never flagged.
  - The catalog is read lazily inside the guarded hook, so an inspection failure never changes or breaks a negotiation.
  - Unverified: a real thread message's id key. The openapi says `message`; the code falls back to `id`.

## Selling to dealers (`agents/dealer_sell.py`, `bazaar dealer sell`)
Dealers that buy (Abuela, Chato, and most likely the Collector) could not be sold to: `dealer.py` is buy-only. A sale is a buy in mirrored prices (p → 10,000 − p). So `decide_sell` runs #61's own `decide()`, and every rule carries over with no copy:
- never close at the opening bid;
- counter strictly past an unmoved price;
- take it when no whole price is left between us;
- a final is take-it-or-walk.

`bazaar dealer sell <id|ref> --start --min [--dealer] [--live]` is a dry run by default. It refuses a `--min` below `sell_min_value_ratio × your_value`, and runs a guardrail check before it opens a thread (so the kill switch stops it). A settled sale is recorded on the ledger as negative spend, as the maker does. In the simulator over real HTTP, it made 2 of 2 sales to Abuela (`scripts/sim_e2e/test_b3_sell.py`). Every ask and accept passes `guardrails.check` (`sell` / `accept_sell` with the copy's value), and the accept slot is reserved on the shared ledger.

Sale ladders from Friday's sale threads, replayed through the W3 machinery (share = (price − its opening bid) / (its limit − its opening bid)):

| dealer, class | opening bid | limits seen | plan | model share | replay | real teams got |
|---|---|---|---|---|---|---|
| Abuela, uncommon | 12 | 12–16 | ask 16 → 13 | 0.872 (deal 0.99) | 0.875 | 0.75 |
| Abuela, common | 5 | 5–6 | ask 8 → 6 | 0.617 (deal 0.77) | 0.526 | 0.474 |
| Chato, uncommon | 13 | 13–16 | ask 17 → 14 | 0.328 (deal 0.61) | 0.600 (n 5) | 0.20 |

A sale plan never goes down to the dealer's opening bid, since a sale there captures nothing and does not count toward unlocking. When its limit *is* its opening bid we keep the card. These ranges are tiny (one or two primas), so a sale's share is coarse. Its real value is as a ladder deal and an unlock deal at a level where we cannot buy.

## Plan per persona (when each one opens)
- **Any new dealer, at announce:** `uv run bazaar ladder levels` (offline, from the captured feed) shows the teaser. The monitor alerts on `level.*`.
- **At activation:** read "how" and `/api/dealers/<id>` (what it sells and buys, deals per hour). Before 5 of its threads have closed, `plan_for` has no floor, so the desk keeps today's lowest-fill / deepest-discount ladder. After that, `bazaar ladder floors --since-tick <activation>` gives its floors.
- **L3 Collector:**
  - If it buys, sell duplicates with `bazaar dealer sell`, starting about 4 above its first bids seen in the feed and stepping 1, never below our floor.
  - If it sells, use `bazaar dealer buy`.
  - Three deals with it, away from its opening price, would unlock L4 early.
- **L4 Tricksters:**
  - Buy only exact card or pack topics (the desk and `dealer buy` never send rarity requests).
  - The inspector blocks every substitution.
  - Turn `allow_flags` on only after watching the `would flag` log lines on their first threads (decision 1).
  - Honest deals with a trickster still count for the ladder.
- **L5 Banker:** unknown mechanics. Our rule holds: accept only a structure the inspector classifies as the plain buy or sale we asked for, and anything else is blocked. Read "how" first; no plan until then.

## What Marius must decide
1. **`allow_flags`** (GUARDRAILS.md, today `false`). Recommendation: switch it on when L4 opens, after the first `would flag` lines confirm the pattern on their threads.
   - The policy only flags the signature. It fired 0 times on Friday's honest dealers, but no real lie has been seen yet: watch the `would flag` lines first.
   - At most 2 flags per process; the cost of a wrong flag is unknown.
2. **Selling to Chato** for L2 ladder deals and early L3 (`dealer sell --dealer chato`). Each sale gives up a duplicate for about 15–16 P, and it is unverified that sales count toward unlocking. The alternative is #81's `dealer_price_caps`.

## Risks
- Nothing about L3–L5 is verified beyond their names and the Trickster description. The plans are conditional, and the code refuses structures it cannot classify.
- The trickster signature assumes the lie is in the item. A trickster that lies only about price or quantity in words, with an honest structure, costs nothing (the structure binds) and is not flagged.
- If a trickster's substituted card is *dearer* than what we asked, we block it but do not flag it.
- The sale ladder rests on 5–19 closed sale threads per class.
