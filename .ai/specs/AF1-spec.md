# AF1 — Ask other teams their multipliers, store said + inferred, show them

Source: Omar via the coordinator (Sat 3 Oct): "In negotiations with other teams, ask for their multiplier, save it
in the database, and show it in bazaar-live." Local backlog (no external tracker): id AF1.

RULES.md re-read: "Words persuade, structure binds" (:11) — a stated multiplier is words, never trusted as fact;
"Your values are private ... every team gets the same six set multipliers, shuffled" (:24) — naming the top of the
shared multiset reveals nothing of ours; team threads on a venue (:64); fair play (:128) — asking is plain talk,
no key sharing, no feeding another team.

## Scope
1. The team desk's FIRST message in a team thread (ours, or an inbound one we answer at step 0) appends one line:
   "Por cierto, ¿qué barrio es vuestro ×1,6? / By the way, which set is your ×1.6?" — once per team per game day
   (Madrid date, a key not a schedule), never to a team that already told us, never without the shared database.
   The structured offer is unchanged. The game day is `/api/clock` `round` (the Madrid date only when the clock
   names none). Teams whose 'said' rows are stored are loaded at start (off the tick): nobody is asked until then.
2. Every message the other team writes in a team thread is parsed once (`team_affinity.parse`): set codes in
   capitals (LAV|SAL|MAL|RET|LAT|CHA) or barrio names ("retiro"/"latina" lower case only after their article), next
   to a multiplier (×1.6, x1,3, 1,6, "1.6") of the shared multiset, inside one plain statement: a question, a
   sentence about OUR sets (vuestro/tu/your) or a negation is no claim (review #217). A set given two values or a
   value claimed twice is dropped; text folded like the injection patterns. Confidence 0.5 (0.25 with an injection
   shape). Quote: the first 264 chars NFKC-normalised, then control/format/bidi chars, NUL and lone surrogates
   turned to spaces, scrubbed, ≤ 200 chars (bounded cost: security review #217).
3. `team_affinity(team, set_code, multiplier, source said|inferred, confidence, tick, thread_id, quote, updated_at)`,
   primary key (team, set_code, source); an older tick never overwrites. Inferred rows: per team with any signal,
   the assignment of the multiset (each multiplier once) that best agrees with `affinity.affinity_map`'s marginals,
   each set with its probability, every 10 ticks (the plan's map reused), also while the desk is off. Written off
   the tick (`AffinityBook`, daemon thread): a row Postgres refuses is dropped alone, a connection error retries
   the batch at the next flush, a write still running after 10 ticks is logged once.
4. Read-only: `bazaar affinity --teams [--json]` and the view `team_affinity_board` (said beside inferred). The
   read-only role reads both through its default privileges (test).
5. Not done (optional in the brief): feeding 'said' values into the desk's valuation. Out of scope, see 98.

## Acceptance
- Tests in `tests/test_team_affinity.py` (parser, quote, book, desk wiring, CLI, Postgres) and
  `tests/test_readonly_user.py::test_the_team_affinity_table_and_board_made_by_the_schema_are_readable`.
- Full gate green except the known main failure; sim smoke passes on a private port.
