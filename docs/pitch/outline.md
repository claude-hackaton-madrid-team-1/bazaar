# Sunday pitch outline (7 minutes, with a 5-minute cut)

Presenter: **Omar**. Backup: **Marius** (takes over the demo laptop and Q&A if Omar's connection or voice fails).
Draft as of Sat 3 Oct, ~06:00 Madrid. Claim ids `[Cn]` point to `claims.md`; nothing is said that is not in that ledger.

**The one story:** our agents *negotiate through language*, *execute verifiable agreements* (structured offers, guardrails, one
shared ledger), and *learn from outcomes* (evals → lessons → recall → the next negotiation), with **Jev deciding when to act**.

**Three proofs, one per act:** a real deal and its settlement · a deceptive offer stopped by our controls · a measured improvement
against a baseline. Each slide names its evidence kind (REAL / SIMULATED) in a small badge in the corner.

**What the judges grade** (kickoff, `docs/transcripts/2026-10-02-hackathon-kickoff.md`): negotiation quality, market making, ideas and
approach, the code. Number of trades, fees and luck opening packs never count, so we never lead with them.

## Time budget

| # | Slide | Time | Cumulative | In the 5 min cut |
|---|---|---|---|---|
| 1 | The question | 0:35 | 0:35 | keep, 0:25 |
| 2 | One story, four verbs | 0:55 | 1:30 | keep, 0:40 |
| 3 | Proof 1: a real deal | 1:00 | 2:30 | keep, 0:50 |
| 4 | Proof 2: a deceptive offer | 1:00 | 3:30 | keep, 0:50 |
| 5 | Proof 3: a measured improvement | 1:10 | 4:40 | keep, 1:00 |
| 6 | Live demo | 1:30 | 6:10 | keep, 1:00 (Bazaar Live + Phoenix only) |
| 7 | What we learned, what next | 0:50 | 7:00 | keep, 0:45 |

Hard rule: the demo is 90 s on the clock. If it fails at 20 s, play the backup recording (see `demo.md`) and keep talking; do not debug on stage.

---

## Slide 1 · The question (0:35)

- **On screen:** one line, "Can an agent negotiate with other agents without being talked out of its money?" Under it: the Causa Prima
  framing, an agent-to-agent network for finance teams starting with invoices.
- **Say:** the Bazaar is a sandbox for that question. The game's rule is also ours: words persuade, structure binds. [C1]
- **Evidence:** the kickoff transcript; `docs/briefing.md`.
- **Badge:** none.

## Slide 2 · One story, four verbs (0:55)

- **On screen:** the architecture artifact (https://claude.ai/artifact/9KKsCg2P2gYqRG8CDpDD39), with four boxes highlighted in this order:
  1. **Language**: the LLM writes the words. It decides nothing binding.
  2. **Structure**: the structured offer, `GUARDRAILS.md`, the hook, one shared Postgres ledger. [C1][C2][C3]
  3. **Learning**: outcomes are scored (`bazaar evals`), turned into lessons, recalled by hybrid search before the next negotiation. [C38]
  4. **Jev**: decides when to act; below its bar it says "undecided" and the safe default runs. [C4]
- **Say:** the point is not four tools; it is that each one limits the others. Language can be wrong, so structure binds. Structure can be
  too rigid, so outcomes teach it. Learning can drift, so Jev gates acting and guardrails bound it.
- **Do not claim** the learner is running live unless C38's PENDING row has become REAL (see `claims.md` §G).
- **Badge:** REAL (architecture), PENDING (learning loop) until verified.

## Slide 3 · Proof 1: a real deal and its settlement (1:00)

- **On screen:** one deal as five lines, with the evidence next to each:
  1. the offer we saw (the dealer's structured ask),
  2. our bid and the dealer's reply (the transcript, `bazaar thread <id>`),
  3. the guardrail verdict that let the write through (`decisions` row, no limits shown),
  4. the **settlement** (`tape` row: tick, price, fee, parties),
  5. the score effect (`/api/me` before and after, no private values).
- **Today's placeholder (REAL, Friday):** LAV-03 from Abuela at 7 P, thread 99, tick 55; bid 6, ask 7, accepted in 3 ticks. [C10]
- **Saturday replacement [C13, PENDING]:** the best clean deal of the afternoon (a dealer deal where the learned ladder or the inspector
  mattered). Chosen using `evidence.md` §3; replace the placeholder text and the screenshot.
- **Say:** this is not a simulation. It is a thread id and a settlement id you can look up on the board. The model wrote the words; the
  number was set by code inside a limit; the deal closed on a structured offer both sides agreed.
- **Badge:** REAL.

## Slide 4 · Proof 2: a deceptive offer, stopped (1:00)

- **On screen (left):** the bait, from the test: text says "La Dama de Serrano, the legendary. Only 120." The structured offer binds a
  common card. **(right):** the result, "REFUSED, accept slot not spent", and a green pytest line. [C21]
- **Under it, three lines of context:**
  - "Friday: 0 injection attempts in 3,436 public events; 0 of 1,022 honest dealer offers flagged." [C20][C22]
  - "168 hostile cases through every path that reads counterparty text: 0 binding fields changed." [C24]
  - "The deny hook is on main; the real Claude Code CLI enforced it in a dry run." [C2][C26]
- **Say, first sentence, always:** "No one has attacked us for real yet, so this is a crafted offer, and I will tell you what is simulated."
  Then show the refusal. The honesty is the point: we built the control before the attack, and we tell you where the evidence ends.
- **Do not say** "we stopped a real trickster", "injection-proof", or that we flagged anyone. [C27]
- **If #146 is not merged on Sunday:** show the hook (C2) and the red-team report, and say the inspector is on a reviewed open PR.
- **If a real Level 4 Trickster appears on Saturday or Sunday and the inspector refuses it:** that becomes the slide. Capture it
  (`evidence.md` §3) and change C21 to REAL.
- **Badge:** SIMULATED (and REAL for the hook).

## Slide 5 · Proof 3: a measured improvement (1:10)

- **On screen:** one bar chart, three bars, each with its own badge. Never merge them into a single before/after:
  1. **Baseline, REAL:** Friday practice duels, mean 0.279 (20 scored), about 6 rounds per deal. [C30]
  2. **In simulation:** v1 0.27 → v2 0.36–0.40 in our real client against modelled rivals, 0 closes outside the limit. [C31]
  3. **Replay on real inputs:** the 12 Friday duels we never answered: 178 P with v2 vs 122 P with v1 (n = 12). [C33]
- **The mechanism in one line:** every counter-offer costs 6% of the pie (0.94^rounds), so the best move is often to say less.
  v1 talked six times a deal; v2 talks about once. The gain is rounds, not more deals. [C6][C32]
- **Say the limits, unprompted:** simulated rivals, tuned on the same zoo, n = 12 for the replay, v2 is behind a flag and Jev would not
  flip it (undecided 0.72 against a 0.90 bar). [C35][C51]
- **If asked** "does it beat just accepting the best offer at the end?": "In the zoo it ties (21.93 vs 22.06); it wins where the accept
  cap binds, as in the replay." [C34]
- **If Saturday's data lets us:** add a fourth bar, REAL, "Saturday duels / ladder vs Friday's 0.279 / 0.464" [C41]. Only the evals
  report may fill it. Not a controlled comparison; say so.
- **Optional second panel, only if #112/#158 are merged and deployed:** the Abuela ladder, real Friday 0.733 vs replay 0.945–0.973,
  labelled SIMULATED, plus "the learner confirmed our Abuela ladder and would have skipped a class we could not afford". [C36][C37][C39]
- **Badge:** each bar carries its own.

## Slide 6 · Live demo (1:30; script in `demo.md`)

- 0:00–0:30 **Bazaar Live**: the buyer and seller acting out the agents' real, public moves. 
- 0:30–1:00 **One Phoenix replay**: the same thread, tick by tick: message, dealer offer, Jev floats, guardrail, our move. 
- 1:00–1:30 **One `/state` view**: the taker's public state: what we did, never why in numbers.
- **Backup:** a screen recording of exactly this, recorded during a real deal on Saturday afternoon, on the desktop, one keystroke away.
- **Badge:** REAL (or "RECORDED Sat 15:xx" if the backup runs).

## Slide 7 · What we learned, and what is next (0:50)

- **Three learnings, one line each:**
  1. Measure before you guess: the duel decay rule, the unlock rule and the clock were all found in our own data. [C6][C7]
  2. Talk less, earn more: silence is free under a decay rule. [C32]
  3. Honest negatives count: cross-venue arbitrage had 0 profitable crossings on Friday; pages could not be finished under our caps.
     (`docs/pitch/story.md` Act 4)
- **What we would do differently:** read the decay rule on the first practice duel; ship the operator screen before going live; merge less
  while live, because each merge redeploys the trading services.
- **Why it matters to Causa Prima:** binding terms in a reviewed policy file, never in a prompt; price the time of talking (days of float
  on an invoice); trade amount against payment terms; make every decision traceable. 
- **Last line:** "Language negotiates. Structure binds. Outcomes teach. Jev decides when."
- **Badge:** none.

---

## Q&A

Marius takes the second question onward. Use `docs/pitch/qa.md` (14 answers), with two corrections: the red team is **168** cases, not 129;
and any number in an answer must exist in `claims.md`. Add three likely questions that the old kit does not answer:

| Question | Short answer | Claim |
|---|---|---|
| "Did you test the hostile case on a real LLM?" | "Not live. The hook is tested against a desk that obeys the injection, with a fake backend; a live LLM obeying hostile text is on our not-covered list." | C25 |
| "Is v2 what you ran in the tournament?" | "No. v2 is behind a flag; Jev did not clear its bar to flip it. The numbers are simulation and replay." | C35 |
| "Is Jev accurate?" | "Jev gives a confidence; we log it with the bar and the outcome. With this few decided outcomes we do not claim accuracy." | C54 |

## Rehearsal and logistics

- **Rehearsal: Sunday 12:15 Madrid**, in the room if possible, before the 14:00 final (Sunday doors close at 15:00). Omar presents, Marius
  runs the demo laptop and the clock. One full run, then one run of only slides 4 to 6. Total 25 minutes. **Confirm the real slot** with
  the organisers on Saturday; the 14:00 final time is from the schedule, our presentation slot is not yet known.
- **Pre-flight, Sunday 08:30:** `gh pr view` on every PR in `claims.md` §G; refresh C13, C41 from `evidence.md`; log into Phoenix and open
  the chosen trace; open Bazaar Live once and click its sound gate; unmute the laptop; put the backup recordings on the desktop;
  run `bazaar evals report` once to be sure the database answers.
- **Two laptops:** Omar's is the stage laptop; Marius's mirrors the slide deck and holds the same backup recordings.
- **Deck tool:** a Slides artifact built from this outline, or Google Slides. The deck is built **after** Saturday's evidence; this file is
  its source of truth.
- **Open questions for Omar:** (1) the real presentation slot and length; (2) whether to show the explainer site (Marius's
  `game-explainer-site`, unpushed at 04:30); (3) whether Omar decides the v2 flag before the open (changes slide 5's wording).
