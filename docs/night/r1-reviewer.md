# Night review (r1): every open PR, reviewed and re-checked

Session `night-r1-reviewer`, 03:14 to 08:00 on Saturday 3 October. The full findings, with file:line references, failure scenarios and fixes, are in
`_night/REVIEWS.md` (outside the repo). This page is the summary for Marius. Proof tests are in `docs/night/r1-proofs/`.

## Method
- One reviewer subagent per PR, each in its own scratch worktree, at high effort. Every review re-ran the gates and the
  PR's headline numbers (simulator and fixtures only, never the live game).
- Each review also checked: guardrail and kill-switch bypasses, defaults that change today's behaviour, private
  values, units, races, and cross-PR breaks.
- Findings went to the owning session. Every fix was then re-checked on the new head, so a "fixed" below means
  re-verified, not taken from the owner's report.
- Differential tests carry the "defaults unchanged" claims. For example: 100k random duels for #86's v1, 400 taker
  runs for #101, and 3,000 duels across missed ticks for #103.

## Merge-relevant verdicts (status at the time of writing)
| PR | Owner | Verdict | Why |
|---|---|---|---|
| #71 live venue | ogarciarevett | **DO NOT MERGE AS IS** | Merging opens a venue at h6.5 and moves the floor from 270 to 370 to 100 with no human step, against the 02:30 decision. HIGH: one Postgres blip locks the key vault out for the life of the process, which means no venue that day. HIGH: an open whose answer is lost leaves no broker key. Medium: such an open is reopened automatically (a second bond). The `venue_keys` schema has no migration. |
| #105 holdings in DB | ogarciarevett | **Block until the default is off** | `holdings_from_db = true` changes the live decision inputs. It also strips `starter_broker_key`, so #71 then sees the starter stall as our venue. A frozen Postgres blocked a send for more than 40 s. |
| #89 / #96 learning taker | ogarciarevett | **Default must be off** | `BAZAAR_LEARN` defaults to on. Railway only `preserve()`s it, so the live taker starts skipping dealers. #96 adds 152 MB of models and about 350 MB of RAM on the live taker, and breaks against #91's `dealer_events`. |
| #91 evals in the tick loop | ogarciarevett | Default should be off | `EVERY_TICKS = 6` turns scoring and Phoenix on in all three live services. Everything else is fixed. |
| #72 (contains #61 + #68) | Marius / ogarciarevett | OK with one medium | Merge #72 with a merge commit, never #61 alone, and close #68 by hand. Refunds are still dated at 60 s per tick, which counts a cancelled bid as phantom spend for 21 to 31 minutes. |
| #62 shared ledger | Marius | OK, but **fix at merge** | Merged with #79, the maker raises AttributeError every tick (`hands_off_ids`). The exact patch is in REVIEWS.md. |
| #60, #86, #103 duels | Marius / w2b | OK | v1 is unchanged on real-shaped payloads. v2 has 0 outside-limit closes in more than 150k fuzzed moves. B11 holds on both harnesses. |
| #106 + #110 wake / fee | ops-small | GO before 09:00 | These are pure fixes. A merge redeploys every agent. |
| #79, #98, #101 trade desk / scanner / arbitrage | w4 / w8 | OK, **squash-merge** | Earlier commits hold private numbers. All highs are fixed, and arbitrage defaults are byte-identical to today. |
| #77, #78, #80, #81, #84, #87, #92, #93, #94, #97, #100, #102, #109 | night | OK, documentation only | All blockers, highs and mediums are fixed and re-verified. The remaining lows are in REVIEWS.md. |

## Counts
To be refreshed at the end of the night.
