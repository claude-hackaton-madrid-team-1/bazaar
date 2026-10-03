# SP2 — The schedule playbook: every agent knows what to do before, during and after each event

**Source:** local backlog. Omar, Sat 3 Oct 19:50 (Spanish, paraphrased): the system must keep the coming events in mind and decide on its own (time, who, cards, teams, album): go over the schedule so the whole system knows what to do, without a human saying it before or during each event.

## What
- `playbook.py` (pure, deterministic): a table per schedule action (bench, duels, day_closes, day_opens, set_release, round, grant_all, persona_patch incl. a fever's end, persona_opens, level_announced, end_round, and Radio Rastro rumours) turns each event into BEFORE / DURING / AFTER instructions per agent (taker, maker, duels, all) with lead ticks, an optional constraint code, and the context they were decided on (tick, game hour, cash, album pages, missing page cards, duplicates, teams in the matrix).
- The news sentinel feeds it every tick from its own reads (`/api/schedule` with params, `/api/levels` via the schedule watch, Radio Rastro items): no new request. It remembers an event past its start, so DURING and AFTER hold after `/api/schedule` stops listing it.
- Each instruction is stored once as a learnings row (kind `schedule`, subject `playbook`, identity `playbook:<event>:<agent>:<phase>`) and logged once.
- The taker obeys `no_new_dealer_thread` (before and during a Market Test or a duel session, before the doors close) behind GUARDRAILS `playbook_enabled`. A rumour (unofficial) never binds: UNVERIFIED until a price probe confirms it.
- Never weaker: no price, cap or guardrail moves; every write still passes `guardrails.check()`.

## Acceptance
- tests/test_playbook.py: each event type's instructions and windows from the real `/api/schedule` payload; stored once; remembered past the start; a rumour never binds; hostile rows; the context; the sentinel builds it with no extra request; the taker holds only while the flag is on.
