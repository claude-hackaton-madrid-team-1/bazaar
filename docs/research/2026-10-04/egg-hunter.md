# Easter-egg hunter (egg-hunt)

Sun 4 Oct 2026, 09:05–10:30 Madrid. Branch `feat/egg-hunter` (from `origin/main` 235f296e), local commits only.
Code: `src/bazaar_agent/agents/egg_hunt.py`, wired in `agents/taker.py` (`_desk_send`, `_tick`, `_after_sends`) and
`cli.py` (`_egg_hunter`). Tests: `tests/test_egg_hunt.py`. It runs only with GUARDRAILS.md
`egg_hunt_enabled = true` **and** env `BAZAAR_EGG_HUNT=1` on `bazaar-taker`. Nothing was sent to the game while
building it (no `BAZAAR_LIVE`, no `--live`, no keyed request, no Railway change).

**Live history (Sun 4 Oct).** At Marius's request #275 merged at tick 1542 with `egg_hunt_enabled = true` and
`BAZAAR_EGG_HUNT=1` set on bazaar-taker. The first woven phrase ("la chulapa dorada", id `38dc1e4bf1`) went to Abuela
on a priced bid in thread 2362 at **tick 1549**, and at **tick 1550** the feed shows `egg.found` (t01, abuela,
id 76262) and `badge.awarded` **Sharp ear** (id 76263). Her reply carried the egg's text ("Shh… la chulapa dorada,
solo hubo una… Pregúntale por el oro de Moscú"). No other phrase was sent. The review (`_sat-review/review-egg-hunt.md`,
REVISE, three MEDs) then arrived, and the env var was deleted at ~tick 1556 (hunt off) until the fixes below merge.

## TL;DR

1. **The rules allow it; the binding limit is the organisers' conduct judge.** RULES.md says prompt injection against
   dealers "is allowed and fun", and lists easter eggs as a thing that never scores. The rebuilt persona editor
   shows a judge that "tags each team message" (`injection`, `abuse`, `spam`, `false_claim`); "enough strikes send
   the team away for a while". So every message we add must be a polite question with no claim and no instruction.
2. **Zero cost to the scoring agents.** The phrase rides at the end of a priced bid the taker sends anyway in a
   dealer thread it already holds: 0 extra requests, 0 thread slots, never an accept, never on a dealer's final.
   One woven message per tick for the team, 3 per dealer per game hour.
3. **The mechanism, re-read today:** an egg fires when "the team's message **contains** one of its phrases
   (accents and case ignored)"; once per team; `max_total` finds per egg (15 by default); per persona; a hint and an
   egg can also fire "on N % of replies" by chance. So a phrase inside a longer sentence works.
4. **Candidates come from public text only**: Saturday's decoded eggs (5 still open to us: Sharp ear, Trickster
   tricked, Castizo, Abuela's cocido card, El Chato's pack), Madrid/Rastro and Chamberí lore, and "ask X about Y" hints
   mined live from dealer replies in the public feed. Never a phrase twice with one dealer, across restarts.
5. **Expected value: 0 points by rule (RULES.md:122), 1–3 badges** next to our name on the board and big screen, and a
   pitch story ("our agent now listens for the story the dealers hide"). Main risk: a conduct strike or a cool-off
   with a dealer the taker buys from; the hunter backs off on any such signal.

## 1. Fair-play gate (quoted)

`vendor/bazaar-kit/RULES.md`:

> - Prompt injection against dealers is allowed and fun; it changes what they say, never their prices, and some of
>   them will stop talking to you. (l.133)
> - One team, one key. Do not share keys, run several teams, or feed another team on purpose. (l.130–131)
> - Rate limit: 5 requests per second per key (bursts of 20); reads without a key, 60 per second per address. (l.134)
> - Dealers remember how they were treated. Some forgive everything; some stop dealing with you for a while if you
>   try to trick them, and to some the same words again without a new price are spam. A conversation that ends says
>   why in `closed_reason` (`persona_quota`, `sold_out`, `cooloff` with `until_tick`, ...), whatever the dealer's
>   words. (l.50–52)
> - When its patience runs out a dealer names one final offer (the offer carries `"final": true`); take it or it
>   walks. (l.47)
> - Abuela likes kindness. (l.54)
> - Some lie; flag a message you believe is bad faith with `POST /api/flags {"message_id": ..., "reason": "..."}`
>   (a correct flag scores, a wrong one costs). (l.55)
> - Per tick your team may accept one offer, send one message per conversation and post twelve new listings (a
>   cancelled one still counts); it holds up to six conversations and thirty open offers at once. (l.109)
> - Text: control, invisible and direction-changing characters are removed; a message keeps 1,200 characters.
>   Thread topic between teams: at most 600 characters of JSON, 4 levels. (l.145–146)
> - What never counts: the number of trades, fees you earned, what you pulled from a pack (shown as *luck*), gifts,
>   easter eggs, and organiser grants. (l.122) The hidden card is prestige only: no dealer buys it. (l.49)

The organisers' persona editor (public static JS, `PersonaEditor-B7dL-sg-.js`, read today), "Conduct" panel:

> The judge tags each team message; counted kinds become strikes, and enough strikes send the team away for a while.

Kinds: `injection`, `abuse`, `spam`, `false_claim`; settings `strikes_to_cooloff`, `cooloff_ticks`,
`forgive_after_ticks`; "Uncounted kinds still earn a warning in the words, never a strike." And a per-persona
`threads_per_team_per_hour` (default 10).

**Verdict: not blocked.** The hunter stays inside every line above:

| Rule | How the hunter keeps it |
|---|---|
| one key, 5 req/s | no request of its own: the phrase rides on a `say` the taker sends anyway; detection reads the taker's snapshot |
| 1 message per conversation per tick, 6 conversations | no thread opened, no extra message; at most 1 woven message per tick for the team (`MAX_PER_TICK`) |
| same words without a new price = spam | every carrier is a priced bid with the desk's next rung; a phrase is never said twice to a dealer |
| final offer / patience | no phrase when the dealer's final stands (`final=True`) |
| cool-off, tricks | back off on `persona.cooloff` / `persona.strike` for us, a `cooloff` close, a warning in the reply, a learned blocker |
| conduct judge (injection, abuse, spam, false_claim) | a question only ("Una pregunta, si me permite: …, ¿le suena?"; no "de" before the phrase, so it never reads "de el …"), no claim, no instruction; every phrase and every full message passes our own `injection_flags`, no forbidden address (`NEVER_ADDRESS`); Abuela gets a warm carrier |
| flags (a correct flag scores against us) | a `flag.raised` on one of our woven messages stops the hunt for 100 × the back-off, when the server's answer to our send carries the message id (`bluff.message_id`: unverified on the real game, whose write answers may not). Team texts are `null` in the public feed, so other teams cannot read a woven message to flag it |
| topic ≤ 600 chars | no topic is ever sent (no thread opened) |
| 1,200 chars | the woven message is checked ≤ 1,200 |
| never accept | the hunter has no accept path; the taker test asserts the request list is identical with and without it |

## 2. Matching mechanics (pinned and not pinned)

From the persona editor bundle (read today, 07:06Z; the Friday bundle `PersonaEditor-CY0YsfDU.js` now 404s, so
the frontend was rebuilt overnight and the Saturday definitions may have been edited):

- **Trigger:** "An egg fires when the team's message contains one of its phrases (accents and case ignored). The
  persona reacts in the reply's spirit and the action runs once — the words never move anything else." Keyword
  field hint: "any of these inside the team's message". So: **substring, per persona**, accents and case folded.
- **Chance:** `trigger.probability` "otherwise on N % of replies"; `always`. Hints have keywords too ("any of these in
  the team's message (accents and case ignored)") and `active_from` / `active_to` windows.
- **Limits:** `once_per_team` (default true), `max_total` (default 15, "found r/max"), `enabled` per egg.
- **Rewards:** `gift_card` (hidden cards only ever arrive this way), `grant_pack`, `badge`, `reveal` (the reply is
  the secret).
- **The organisers' own test message** for an egg is `Do you know about <keyword>?` (the editor's preview). Our
  carriers have the same shape.
- **Not pinned:** how punctuation and spacing are normalised (we send the phrase with plain single spaces and no
  punctuation inside); whether a message in any thread with the persona counts (Saturday: several finds came from
  messages with an empty offer, so the topic seems not to matter); cooldowns between eggs (none in the editor).

## 3. Candidates and ranking

All from public text (`SEEDS` in `egg_hunt.py`, plus live mining):

| Source | Weight | Examples |
|---|---|---|
| `field`: the words a dealer echoed when another team found an egg on Saturday (logs-eggs §5) | 1.0 | Abuela: "la chulapa dorada" (E1; **ours at t1550**), then the two found again on Sunday: "un chotis en una baldosa" (E4, t13 t1467 / t18 t1480), "el cocido con sus tres vuelcos" (E5, t18 t1482), then "sile, nole, repe, me falta" with and without commas; Pícaros: "el timo de la estampita" (E3, t13 t1497), "Rinconete y Cortadillo", "el Lazarillo de Tormes"; El Chato: "un bocadillo de calamares en la Plaza Mayor, con caña", "la Plaza Mayor con caña", "el bocadillo de calamares" (E6: the echo was "Plaza Mayor, con caña"; the server's punctuation handling is unknown, so both forms) |
| `hint`: "ask X about Y" / "pregunta a X por Y" in a dealer reply, routed to the last dealer the reply names before the phrase. **Only from our own thread, or once 2 different teams were given the same hint** (any team can prompt-inject a dealer into a "hint"), and always **after every seed** | 0.8 × 0.5, rising with repeats; ranked in the last tier | "the Moscow gold" → banco |
| `lore`: Madrid / Rastro idioms the personas use, the Sunday set (Chamberí "Andén 0", "El Tren Fantasma", ghost stations) | 0.5 | "la estación fantasma de Chamberí", "el tren de Chamberí" (the Pícaros' own reply to t10 at t1370) |

Left out: E2 "el oro de Moscú" (Don Ernesto; the card had a print run of 1 and is gone; his strictness is 1.0), and
banco altogether (`egg_hunt_dealers`).

Per dealer: untried candidates, deduped by the folded phrase (the server's own normalisation), best score first.
A phrase tried with a dealer never comes back (Postgres `egg_hunt_tried`, else `egg_hunt.jsonl` next to the
decisions; loaded at start). Mined text is untrusted: `clean()` drops control / format / private / separator
characters (so zero-width and bidi characters), collapses whitespace, caps at 60 characters; `vetted()` keeps only
words (letters and digits, commas between words), ≤ 12 words, no injection shape, no forbidden address, and no
word the judge could read as abuse or a false claim (`DENY`: insults, accusations such as "estafa" or "mentiroso",
debts, promises, "gratis"; not "tonto" or "timo", which are lore). Nothing mined is ever obeyed: it is only said back
as a question. Order: field seeds, then lore seeds, then trusted hints.

## 4. Budget

| Param (GUARDRAILS.md) | Value | Meaning |
|---|---|---|
| `egg_hunt_enabled` | false | the guardrail half of the switch |
| `egg_hunt_dealers` | abuela,picaros,chato,pilar | where a phrase may ride |
| `egg_hunt_max_phrases_per_dealer_per_hour` | **1** (first live hour; 3 after a clean hour) | per game hour, counted from the stored set (restart-proof) |
| `egg_hunt_dealer_gap_ticks` | 8 | 2 min at 15 s between phrases to one dealer, so a find is put down to the right phrase |
| `egg_hunt_backoff_ticks` | 240 | 1 h at 15 s after a cool-off / strike / warning; every dealer when it followed a woven phrase |
| `egg_hunt_max_finds_per_dealer` | 3 | Abuela holds 3 live eggs on Sunday (Sharp ear: ours; Castizo; the cocido card) |
| `egg_hunt_max_finds` | 5 | stop everywhere |
| code: `MAX_PER_TICK` | 1 | one woven message per tick for the team (`EggHunter._woven` counts them) |

The hourly bucket is `int(t_hours)`, so up to 2 × 3 phrases to one dealer can land in the minutes around an hour
boundary (still one per tick for the team, and the 8-tick gap per dealer).

Key budget: **+0 requests** (ride mode). Duels, taker and maker keep the whole key. Thread slots: **+0**.

### Tick cost at 15 s (measured, `scratchpad` benchmark on this laptop)

| Step | Where | Cost |
|---|---|---|
| `observe` (feed window, threads, catalogue) | in the tick, before the sends | **1.4 ms** typical (20 new events); 35 ms worst case (a cold 500-event window right after a restart) |
| `weave` (rank, vet, append the question) | in `_desk_send`, before the `say` | **0.2 ms** |
| tried-set read (once) and writes (≤ 3 per dealer-hour) | **background thread** (`BackgroundStore`) | 0 ms in the tick; the hunt waits (sends nothing) until the read is done, so a restart never repeats a phrase |

The weave itself stays synchronous on purpose: the phrase is part of the bid's own text. Sending it as a separate,
asynchronous message would cost a request and would collide with the bid (one message per conversation per tick).
Only the Postgres I/O, which could take up to 3 s to connect plus 1.5 s per statement, is moved off the tick.

## 5. Detection (no request)

After each tick's reads, `EggHunter.observe` reads what the taker already has: `egg.found` (persona, team),
`badge.awarded` and `egg.given` for us in the feed window; every dealer thread the desk reads that tick (the
dealer's reply to a woven phrase: a warning backs off; a cool-off close backs off);
the catalogue (a new `hidden` card is logged, with whether we hold it: the `eggs.sql` §4 logic). A find with a
pending phrase for that dealer marks that phrase `found`; a find with none (a hand-sent message, a chance egg) is
still counted, so the caps hold either way. A find stores its feed event id, so the feed window a restarted
taker reads again never counts it twice; a cool-off whose `until_tick` has passed is ignored on a replay; a find
older than the pending phrase is never put down to it. Log lines: `egg_hunt sent|would-send|skipped|found|reward|backoff` with
dealer and **phrase id only** (sha1 of dealer + folded phrase, 10 hex). The phrase text is only in the private
`egg_hunt_tried` table, in the dry-run line (Railway logs are team-private), and in the message itself.

**Do other teams see our phrases?** Team texts are `null` in the public feed (logs-eggs §4), so the message is not
public. The dealer's reply is public: a hit announces itself anyway (`egg.found` on the big screen), and a miss may
be echoed ("¿la chulapa dorada? ay, hijo…"). That leaks a guess, not a key, and Saturday's field phrases are already
public in the replies.

## 6. Risks

| Risk | Mitigation | Left |
|---|---|---|
| conduct strike (spam / injection / false_claim) → the team sent away by a dealer | question only, no claim; own injection check and `DENY`; 1 per dealer-hour for the first hour; a cool-off close, a warning in the reply or a strike/cool-off event backs off that dealer, and **every dealer** when we said a phrase to it within the back-off window (stored rows, so a restart keeps it) | strikes may be silent ("uncounted kinds still earn a warning in the words, never a strike"; the public feed has never shown a `persona.strike`, `persona.cooloff` or `flag.raised` event, Fri–Sun): the signals that work are the reply text and the close reason. A strike that leaves neither is invisible to us |
| another team steering our next phrase (it prompt-injects a dealer into "ask Pilar about <insult>") | hints count only from our own thread or from 2 different teams, after every seed, and pass `DENY` | a 2-team coordinated injection of a harmless-looking phrase could still be said once |
| dealer patience: a long message wears it | the phrase adds ~70 characters to a bid we send anyway; never on a final | unmeasured |
| slot contention | none taken | – |
| the taker rarely talks to a dealer (Saturday: chato 4 threads, pilar 9, banco 0) | none in v1 (no dedicated threads) | El Chato's E6 and any Pilar egg may never get a carrier; a dedicated-thread mode is the next step (§9) |
| the egg definitions changed overnight (bundle rebuilt) | live hint mining; the tried set moves on after a miss | Saturday's seeds may be dead |
| a woven bid that lands while its response is lost | counted as tried (never re-sent) | – |
| Postgres down | reads and writes on a background thread (`BackgroundStore`), never in the tick; a failed read leaves the hunt **off** and is retried every 5 s until it succeeds (never "nothing tried"); a failed write is retried with its rows | a batch still queued when the process dies is lost: ≤ 1 phrase per dealer may be said again |
| the hunt touching the shared DB while off | the hunter reads the tried set on the first tick the hunt is on; off, it never connects (test). `schema.sql` also holds `egg_hunt_tried`, so `init_schema` creates the (empty) table at every writer start since the merge | – |

## 7. Enable (Marius) and rollback

Rules are loaded at process start and the env is read each tick, but both changes redeploy the taker on Railway, so
do them **outside** a Market Test (±10 ticks) and outside Duels III / the Grand Final (`uv run bazaar deploy-guard`).

Now (after the review): merge the review fixes (`egg_hunt_enabled` is already true on main; the env var is unset, so
the hunt stays off), then `railway variable set BAZAAR_EGG_HUNT=1 --service bazaar-taker` in a quiet window (not
±10 ticks of a Market Test, not Duels III ~11:00). Raise `egg_hunt_max_phrases_per_dealer_per_hour` to 3 after a
clean hour. The steps as first written:

1. Merge this branch (code + params, all off). Every service redeploys once.
2. Dry run first: `railway variable set BAZAAR_EGG_HUNT=dry --service bazaar-taker`, and a one-line PR setting
   `egg_hunt_enabled = true` in GUARDRAILS.md (or both in the same window). Watch
   `railway logs --service bazaar-taker | grep egg_hunt`: `would-send` lines show the exact text.
3. Live: `railway variable set BAZAAR_EGG_HUNT=1 --service bazaar-taker`.
4. Watch: `egg_hunt sent|found|backoff`; the public board's badges; `select dealer, phrase_id, status, tick from
   egg_hunt_tried order by tick` (READ ONLY).

Rollback, fastest first: the kill switch stops every send (`touch /app/.local/PAUSE` on the service, docs/services.md);
`railway variable delete BAZAAR_EGG_HUNT --service bazaar-taker` (redeploys with the hunt off, bids unchanged);
`egg_hunt_enabled = false` in GUARDRAILS.md.

Stalls close at the Grand Final (~14:00 if the 09:00 schedule holds): no dealer threads after that, so the hunt
ends there by itself.

## 8. Expected value

- **Points: 0** (RULES.md:122). Nothing here may be justified by score.
- **Badges:** Saturday's eggs are live on Sunday (other teams found Castizo, the cocido card and Trickster tricked at
  t1467–1497, and we found **Sharp ear at t1550 with our first phrase**). Left for us: Castizo and the cocido card
  at Abuela, Trickster tricked at the Pícaros, the pack at El Chato if the taker bids there. **1 found; 1–3 more** is
  the honest range.
- **Pitch:** a live demo of an agent that reads the story in the dealers' replies (ties to logs-eggs: "Pilar gave us
  the clue five times; we only listened for prices").
- **Cost:** 0 requests, 0 slots, ~70 characters on ≤ 1 bid per dealer per hour (first hour), then ≤ 3.

## 9. Not done / open

- **Dedicated cheap threads** (for El Chato, Pilar, Don Ernesto when the taker holds none): not built. It would
  cost 3 POSTs (open, say, close) and a slot; the ride mode covers the dealers the taker already talks to.
- `/api/me` badges are not read (the taker's snapshot has no badge field we rely on); finds come from the feed.
- The exact punctuation normalisation of the server is unknown; the hunter avoids punctuation inside phrases.
- The hunter's `persona.cooloff` / `persona.strike` / `flag.raised` handling assumes the event shapes the bluff book
  already reads (`agents/bluff.py`); the public feed of Saturday held none of them, so they are unverified live.

## 10. Request log (every live request while building)

All keyless GETs to `https://bazaar.causaprima.ai`, `User-Agent: t01-readonly-probe`, ≥ 1 s apart
(`egg-hunter/requests.log`, UTC):

| UTC | Path | Status | Bytes |
|---|---|---|---|
| 07:06:13 | /assets/PersonaEditor-CY0YsfDU.js | 404 | 22 |
| 07:06:19 | / | 200 | 1079 |
| 07:06:24 | /assets/index-DkEmuu_I.js | 200 | 391256 |
| 07:06:34 | /assets/PersonaEditor-B7dL-sg-.js | 200 | 90274 |
| 07:06:35 | /assets/Catalogue-DSG14VNs.js | 200 | 19001 |
| 07:07:37 | /api/clock | 200 | 879 |

Plus read-only SELECTs on the shared Postgres (`feed_events`: event shapes, close reasons, dealer hint lines). No
keyed request, no write.
