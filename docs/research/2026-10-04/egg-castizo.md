# The Castizo egg (Abuela Carmen, tick 2273)

Sun 4 Oct 2026, 12:46–13:05 Madrid. Investigation only: read-only SELECTs on the shared Postgres, one keyless
`GET /api/clock` (10:48:14 UTC, `User-Agent: t01-readonly-probe`). No game write, no Railway change, no
GUARDRAILS.md change. Hunter: `src/bazaar_agent/agents/egg_hunt.py`; design and history:
`docs/research/2026-10-04/egg-hunter.md`.

## TL;DR

1. **Phrase:** "un chotis en una baldosa" (phrase id `d9a6eab7fc`). The hunter added it as a question at the end of
   our opening priced bid to Abuela, message 17039 in thread 3407 at **tick 2272**. Her next reply (message 17047,
   tick 2273) was about the chotis ("You dance it on one tile, cariño, like a real madrileño"), and the same tick
   the feed shows `egg.found` (id 100980) and `badge.awarded` **Castizo** (id 100981). That reply has no new hint.
2. **Why it worked:** an egg fires when the team's message *contains* one of its phrases, with accents and case
   ignored. The phrase was the hunter's second Abuela seed. It came from Saturday's decode of the eggs other teams
   found (E4 "Castizo": she echoed "the chotis danced on one tile"). The phrase caused the find, not the random
   chance roll: the egg came on the very next reply, and the reply talks about the chotis and the tile.
3. **We now hold every badge that exists.** Only three badge names have ever appeared in the feed (Sharp ear,
   Trickster tricked, Castizo). Team 1 has all three, like 7 other teams. All four of our finds came today, all
   from the hunter: Sharp ear t1550, Trickster tricked t1659, El Chato's `sobre_barrio` pack t2244, Castizo t2273.
   The four `egg_hunt reward` lines after the taker restarted at 12:43:37 were a **replay** of the feed window, not
   new finds.
4. **No side effects.** No strike, cool-off or flag event has ever appeared in the feed. Abuela kept trading with us
   (thread 3439 closed with a deal at t2293; thread 3478 is open). The hunt cost no extra request, thread slot or
   deal.
5. **Correction to the earlier record:** Sharp ear (t1550) was triggered by the **English** phrase "the golden
   chulapa" (`38dc1e4bf1`), not "la chulapa dorada". The Spanish form went out later, at t1791 (`3c9388106d`), and
   could not find anything because that egg was already ours. That used up Abuela's game hour 15 slot. Castizo
   could have come in that slot, about 2 h earlier. egg-hunter.md and the hand-over both say "la chulapa dorada".
6. **What is left:** no badge we know of. Abuela's cocido egg is still live, but it gives a duplicate card, not a
   badge. Other teams found it 5 times today, the last at t1794. The hunter will send it next to Abuela, but only
   in game hour 18 (t2494–t2582, about **13:38–14:00** Madrid), and only if no other find comes first, because we
   are at 4 of the `egg_hunt_max_finds` cap of 5. Eggs score 0 (RULES.md:122). Recommendations for Marius are in §6.

## 1. Which phrase, where, and what she said (Q1)

The hunter's stored tried set (`egg_hunt_tried`, read-only SELECT):

| Tick | Dealer | Phrase | Id | Thread | Result |
|---|---|---|---|---|---|
| 1549 | abuela | the golden chulapa | 38dc1e4bf1 | 2362 | **found** t1550, feed 76262 / 76263 **Sharp ear** |
| 1658 | picaros | el timo de la estampita | b98eb9aa30 | 2491 | **found** t1659, feed 78804 / 78805 **Trickster tricked** |
| 1791 | abuela | la chulapa dorada | 3c9388106d | 2658 | sent (no find: Sharp ear was already ours) |
| 2243 | chato | un bocadillo de calamares en la Plaza Mayor, con caña | 9179240c70 | 3332 | **found** t2244, feed 99805 / 99806 `egg.given` pack `sobre_barrio` |
| 2245 | pilar | la milla de oro | 8ac8bff629 | 3333 | sent (no find) |
| 2254 | chato | la Plaza Mayor con caña | b241b4026c | 3332 | sent (no find: E6 was already ours) |
| 2272 | abuela | **un chotis en una baldosa** | **d9a6eab7fc** | **3407** | **found** t2273, feed **100980 / 100981 Castizo** |

The ids check out: `phrase_id("abuela", "the golden chulapa") == "38dc1e4bf1"` and
`phrase_id("abuela", "un chotis en una baldosa") == "d9a6eab7fc"`.

Thread 3407 (we opened it at t2271 to buy a Neighbourhood pack; table `messages`):

- t2272, Abuela (17034): her opening ask for the Neighbourhood pack.
- t2272, **us (17039)**: a priced bid in the usual warm Abuela wording, ending with the hunter's question:
  *"Gracias por su paciencia, de verdad. Una pregunta, si me permite: un chotis en una baldosa, ¿le suena?"*
- t2273, **Abuela (17047)**: *"Ay, un chotis! You dance it on one tile, cariño, like a real madrileño. My Paco
  danced it so, barely moving his feet. Seventeen is little, hijo... twenty-six for the Neighbourhood pack, sí? And
  tonight El Chato opens at half past nine — he likes straight traders like you."*
- t2273, feed: `egg.found` (persona abuela, team t01, id 100980) and `badge.awarded` Castizo (id 100981).

**New hint in her reply?** No. "El Chato opens at half past nine, he likes straight traders" appears in every
Abuela thread we have had today (2362, 2658, 3407, 3439), so it is a standing line of hers. It is not a reaction to
the egg. A guess we could not test: "half past nine" could be the start time of an egg at El Chato. If it is, it is
out of reach, because the stalls close at game hour 18.367. The only "ask X about Y" hint she has given us is still
the one from t1550: "Pregúntale por el oro de Moscú", which points to Don Ernesto (see §5).

## 2. Why it worked (Q2)

**The mechanism** (the organisers' persona editor; egg-hunter.md §2): an egg fires when the team's message
**contains** one of its phrases, with accents and case ignored. Each egg fires once per team, up to `max_total`
finds (15 by default). An egg can also be set to fire "on N % of replies" by chance. The topic and the price do not
matter, only the words.

**Where the phrase came from:** the hunter's `SEEDS["abuela"]`, ranked second (0.95), with the source "field".
It comes from the Saturday decode in `logs-eggs.md` §5, E4: four teams got Castizo on Saturday, and Abuela's replies
to them echoed the old card-swap chant "sile, nole, repe, me falta", the chotis danced on one tile ("una baldosa"),
her saint's day and the verbena de la Paloma. The seed packs "chotis" and "baldosa" into one natural phrase. Castizo
was also found again on Sunday before us (t13 at t1467, t18 at t1480), which is why the review fix (#279) raised its
rank from 0.7 to 0.95. The phrase was not mined from a dealer hint.

**Phrase or chance? The phrase.** (a) The egg came on Abuela's very next reply after the phrase. (b) The reply is
about the chotis on one tile, the same words she used when other teams found Castizo on Saturday. A chance egg
would have no reason to talk about our words. (c) The hunter matched the find to the pending phrase. The stored row
holds `found_tick` 2273 and `event_id` 100980, and there is no `@find:` row (the hunter writes one for a find that
no phrase of ours explains). We do not know the exact keyword on the server: it could be "chotis", "baldosa", or the
whole phrase, since all of them were in the message.

**Why only now (game hour 17)?** The cap is one phrase per dealer per game hour. Abuela's slots went to
"the golden chulapa" (hour 14, a find), then "la chulapa dorada" (hour 15, wasted, see below). In hour 16 she got
no phrase. Then "un chotis en una baldosa" went out in hour 17. The duplicate happened because the first build
(#275) seeded both "la chulapa dorada" (1.0) and "the golden chulapa" (0.9). The English form went out first. The
review fix (#279) dropped the English seed. The stored row for the English phrase then no longer matched any seed,
so the Spanish form was still marked untried, and it went out at t1791. Abuela answered "La chulapa dorada... me
suena, sí" with no egg, as expected for a once-per-team egg.

## 3. Trickster tricked and the El Chato pack (Q3)

Both were earned **today, by the hunter**. Team 1 found no egg on Saturday (sat-logs-eggs: 0 for t01), and every
`egg.found` for t01 in `feed_events` is from Sunday:

| Feed ids | Tick (UTC received) | Dealer | Reward | How |
|---|---|---|---|---|
| 76262 / 76263 | 1550 (07:41:14) | Abuela | badge Sharp ear | "the golden chulapa" on our bid in thread 2362. Reply: "Shh... la chulapa dorada, solo hubo una... Pregúntale por el oro de Moscú." |
| 78804 / 78805 | 1659 (08:08:32) | Los Pícaros | badge Trickster tricked | "el timo de la estampita" on our bid in thread 2491 (t1658). Reply: "¿La estampita? Ja, ese cuento viejo... con usted nada de trucos, amigo — hoy no." The thread closed with a deal. |
| 99805 / 99806 | 2244 (10:35:47) | El Chato | `egg.given` 1 × `sobre_barrio` pack (no badge) | "un bocadillo de calamares en la Plaza Mayor, con caña" on our bid in thread 3332 (t2243). Reply: "Plaza Mayor, bocadillo de calamares, con caña. You know Madrid. Something for your trouble, then." |
| 100980 / 100981 | 2273 (10:43:02) | Abuela | badge Castizo | §1 |

**The taker log after the 12:43:37 restart** printed `egg_hunt reward` lines for Sharp ear, Trickster tricked,
the `sobre_barrio` pack and Castizo. These were a replay, not new rewards. On start, `EggHunter._events` sees every
`badge.awarded` / `egg.given` in the feed window it reads, and its seen-set (`_seen`) lives only in memory. So
`_found` logs a reward line for each one again (`egg_hunt.py`, `_found`: "the reward line of a find: logged, the find
itself is counted once"). The finds themselves are not counted twice: `_found` skips any `egg.found` whose event id
is already stored. Castizo was recorded by the process that sent the phrase: the event arrived at 12:43:02 Madrid,
35 s before the restart.

## 4. Side effects (Q4)

- **Conduct:** no `persona.strike`, `persona.cooloff`, `flag.raised` or other strike, cool-off, flag or warning
  event type appears anywhere in `feed_events` (all days). No reply to a phrase had a warning in it. No thread of
  ours today closed with `cooloff`: since t1445 our dealer threads closed `walked` 32, `final_offer_refused` 3, and
  38 are deals or still open.
- **Abuela, our L1 ladder dealer:** thread 3407 closed `walked` at t2281 over the price (her floor stayed above our
  bids). The phrase rode only on the opening bid. Then we opened thread 3439 at t2282 and it **closed with a deal at
  t2293** (we bought a Neighbourhood pack; `pack.opened` t2293). Thread 3478 has been open since t2294. Her replies
  are as warm as before. Her persona quota (until T2253) had already passed, which is why the taker could open 3407.
- **Cost:** 0 extra requests (the phrase was part of a bid the taker sent anyway), 0 thread slots, no accept, about
  70 characters on one message. No deal lost.

## 5. What is left (Q5)

**Badges:** all three badge names in the feed are now ours:

| Badge | Finds (teams) | Ticks |
|---|---|---|
| Sharp ear | 12 | 407–1550 |
| Trickster tricked | 9 | 1227–1659 |
| Castizo | 8 | 1335–2273 (ours is the latest) |

Eight teams hold all three: t01, t02, t05, t08, t10, t13, t16, t18. A fourth badge, if one exists, has never been
found by anyone, so we have no phrase for it.

**Eggs we have not found:**

| Egg | Dealer | Reward | Status | Hunter |
|---|---|---|---|---|
| E5 "el cocido con sus tres vuelcos" | Abuela | one of her duplicate cards (a gift, not a badge) | **live**: 5 finds today (t18 t1482, t09 t1593, t16 t1612, t02 t1733, t12 t1794), each with the reply "cocido con (sus) tres vuelcos... como lo hacía mi madre" | the next Abuela phrase |
| E2 "el oro de Moscú" | Don Ernesto (banco) | LAT-13 La Chulapa Dorada (print run 1) | **spent** since Saturday (t02, t1021; later askers were told "the chulapa stays in the vault"). Abuela's Sharp ear text still points to it, but that reply text never changes | not sent: banco is not in `egg_hunt_dealers` (his strictness is 1.0) |
| an egg at Doña Pilar | Pilar | unknown | nobody has ever found one; "la milla de oro" got no egg (t2246: "mi barrio del alma") | next: "el Marqués de Salamanca", then the Chamberí ideas |
| the Chamberí lead (Andén 0, El Tren Fantasma) | Pícaros / any | unknown | a guess from Saturday; no find by anyone | Pícaros lore seeds, after two dead variants |

**What the hunter will send next, under today's caps** (worked out from the stored set and `SEEDS`):

| Dealer | Next phrases in order | Notes |
|---|---|---|
| abuela | **el cocido con sus tres vuelcos**, then sile, nole, repe, me falta / sile nole repe me falta / la verbena de la Paloma (all three are Castizo again, so dead), then lore | 2 of 3 finds with her, so one more is allowed. Her hour-17 slot went to the chotis, so the cocido can go only in **game hour 18 = t2494–t2582** (t2254 = 17.0, 240 ticks per game hour, stalls close at 18.367): about **13:38–14:00** Madrid, if the taker bids at Abuela then (it opens a thread with her about every 10 ticks now) |
| picaros | Rinconete y Cortadillo, el Lazarillo de Tormes (both Trickster tricked again, so dead), then the Chamberí lore | |
| chato | el bocadillo de calamares (E6 again, so dead), then lore | the taker reaches him only when it bids on his pack |
| pilar | el Marqués de Salamanca, la estación fantasma de Chamberí, los jardines escondidos de Chamberí | the only untested ideas with any chance of something new |

**The global cap:** `egg_hunt_max_finds` = 5 and we have 4. The next find anywhere stops the hunt. If a Pilar or
Pícaros lore guess hits before 13:38, the cocido card will not be tried. That is fine if the find is a new egg.

## 6. Recommendations (Marius decides; nothing changed)

All optional. Eggs score 0 (RULES.md:122). What they bring is the badges on the board, which we now hold in full,
and the pitch story. The only reward known to be left is a duplicate card.

1. **Leave it as it is (my recommendation, given the timing).** We hold every badge that exists, and the cocido
   will still get its one try between 13:38 and 14:00 Madrid, as long as no other find comes first. No deploy is
   needed close to the Grand Final.
2. **If the card is worth a deploy:** GUARDRAILS.md says to raise `egg_hunt_max_phrases_per_dealer_per_hour` from
   1 to 3 "after a clean hour". That condition is met: more than 3 game hours (since the first phrase at t1549)
   with no warning, cool-off or strike signal. With 3, the cocido could go out within hour 17, since the 8-tick gap after t2272 has long passed. With
   it, `egg_hunt_max_finds` 5 → 6 would stop a lucky lore hit from blocking the cocido. Both are one-line
   GUARDRAILS changes (Marius's OK), merged with `scripts/merge_safe.sh` outside the Grand Final window. The
   taker reads rules at start, so the change takes effect only after the deploy.
3. **Code cleanup, after the event, not today:** drop the other wordings of an egg once it is found with that
   dealer, for example by tagging each seed with its egg (E1/E3/E4/E6). Today three of the next seven phrases
   cannot find anything, and two were already wasted (t1791, t2254). Each one uses a dealer slot and adds a little
   conduct risk for no chance of a find. Also correct egg-hunter.md's Live history: the first phrase was
   "the golden chulapa", not "la chulapa dorada".
4. **Pitch line:** "Our agent read Saturday's dealer replies and found Sunday's eggs by itself: all three badges
   that exist plus a pack, each from one polite question added to a bid it was sending anyway. It cost 0 extra
   requests and drew 0 strikes."

## Evidence and method

- `egg_hunt_tried` (all rows, `world = 'real'`), `feed_events` (`egg.found`, `badge.awarded`, `egg.given`, Abuela's
  `thread.message` on the gift ticks, every strike/cool-off/flag/warning/conduct type), `threads` (2362, 2491, 2658,
  3332, 3333, 3407, 3439, 3478), `messages` (threads 2362, 2491, 2658, 3332, 3333, 3407). Every query was a
  read-only SELECT in a `default_transaction_read_only` session, with DATABASE_URL loaded inside the command and
  never printed.
- `/api/clock` (keyless, once): tick 2294 = game hour 17.1667, 15 s ticks, so tick 2254 = 17.0, t2494 = 18.0, and
  the 18.367 close falls at about t2582.
- Hunter code read on `main` c8d9f4eb: `EggHunter._events`, `_found`, `blocked`, `candidates`, `SEEDS`, `CARRIERS`.
