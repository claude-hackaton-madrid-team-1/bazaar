# Night shift reports (Fri 2 → Sat 3 Oct 2026)

Marius's night shift opened one draft PR per backlog item. The fixes that help us win were taken over and
land with their own reports here (B13 #106, B17 #140, B18 #141, B14 #142, B16 #143, B19 #144). The analysis and
planning PRs were closed on 2026-10-03 (coordinator triage); their code was **not** merged and stays on its
branch, and their reports are kept below so the findings and decisions are not lost.

## Findings and decisions

| Report | From | Finding | Decision (2026-10-03) |
|---|---|---|---|
| [w7-page-economics.md](w7-page-economics.md) | #87 W7 (`night/w7-page-economics`) | Finishing a page is not worth it under today's caps: all 7 missing rares fail at `max_price_rare` 80 (Chato fills at 82–93) | No cap change |
| [b9-packs-ev.md](b9-packs-ev.md) | #109 B9 (`night/b9-packs-ev`) | **Buy no Abuela packs Saturday**: her pack threads fill at a median 22 P (> `max_price_pack` 20), a pack thread takes the Abuela conversation the best-three ladder deals need, and she sells 3 an hour against the same spend cap | Adopted as a finding (sent to N14b) |
| [w3-ladder.md](w3-ladder.md), [ladder_plan.json](ladder_plan.json) | #81 W3 (`night/w3-ladder`) | Friday floor table from 273 dealer threads: Abuela common p50 10, uncommon p50 23, pack p50 22; Chato uncommon p50 29, rare p50 91. W3's Abuela uncommon plan 21→25 replays at share 0.973 vs today's 17→26 at 0.800 | Prior for the auto-evolving ladder (#112, N3) |
| [b21-levels.md](b21-levels.md) | #119 B21 (`night/b21-levels`) | Levels are activated by an admin; the unlock rule and open-to-all times cannot be verified offline: plan for an early unlock | Reference |
| [b25-verify.md](b25-verify.md) | #132 B25 (`night/b25-verify`) | 112 assumptions of the night's plans, each with the read-only check that settles it after 09:00; `bazaar verify` runs from that branch | Run from the branch if needed |
| [b28-taker-counterfactual.md](b28-taker-counterfactual.md) | #134 B28 (`night/b28-taker-counterfactual`) | Friday replayed through the current taker: `min_buy_surplus` 4–6 doubles its copy-value surplus (+28 → +54 P) with fewer buys | **`min_buy_surplus` stays at today's value**: Jev leaned to 4 at 0.60, under the 0.75 bar |
| [b6-saturday-playbook.md](b6-saturday-playbook.md), [saturday-playbook.md](saturday-playbook.md) | #102 B6 (`night/b6-saturday-playbook`) | **Saturday 09:00 is probably game hour 2.65, not 4.0** (the clock froze at tick 159 = h 2.65 Friday): every "game hour" timing shifts unless the organisers jump the clock. Hour-by-hour plays in `saturday-plays.json` / `saturday-schedule.json` | Reference for the operator |
| [b20-venue-path.md](b20-venue-path.md) | #118 B20 (`night/b20-venue-path`) | Reconciles #71's venue keeper with B2: open at 09:00 as insurance (auto, or board + #84's edge); the free stall may already score half the bench points | **Not adopted**: venue timing stays as #71 (game hour 6.5); Jev undecided → today's default |
| [b2-venue-runbook.md](b2-venue-runbook.md) | #92 B2 (`night/b2-venue-runbook`) | Venue go-live runbook and Saturday bench simulation; superseded by B20's reconciliation | Reference |
| [b1-organic-winrate.md](b1-organic-winrate.md) | #94 B1 (`night/b1-organic-winrate`) | Organic market: 0 of Friday's 739 public offers went to a team venue, so it is no points source today; one genuine rival pair would score the whole component. The win-rate bench policy is not wired into the broker | Reference |

## Pitch kit (#135 B29, partial)

[`docs/pitch/`](../pitch/) holds the Sunday presentation drafts: `story.md` (five acts, sourced numbers), `qa.md`
(14 judge questions), `demo.md` (timed 5-minute demo) and `charts/` (chart specs and extracted data). Report:
[b29-pitch-kit.md](b29-pitch-kit.md). **Drafts, not reviewed**: the red team now has 168 cases (story and Q&A
say 129), chart 8 and the playbook disagree on Saturday's Market Test count, and every Saturday number must be
re-checked on Sunday. [`docs/decisions.md`](../decisions.md) (43 decisions + 13 open ones) describes duel v2's
shape and the endgame: keep this repository private until after the Final.

## Not salvaged here

- #84 W1b broker edge + #77 bench simulator: taken over as one PR after #71 merges (afternoon window).
- #78 W5/W6 + #128 B10: taken over as one PR on `main` (afternoon window).
- #129 B26 (`dealer_mints_unminted`): closed into #145 (N14b).
- #127 B24 team desk: closed; N17 (#123) covers team threads.
- #100 B12 jitter, #122 B22 cockpit, #125 B23 alerts, #120 rehearsal: closed (see each PR's closing comment).
