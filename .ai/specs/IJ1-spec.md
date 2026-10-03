# IJ1 — Prompt-injection attempts recorded with proofs

Source: Omar via the coordinator, 2026-10-03 ("save when another team's LLM or agent tries prompt injection, and
show it in bazaar-live with PROOFS"). Local backlog (no external tracker). The bazaar-live panel is its own PR.

## Rules check (vendor/bazaar-kit/RULES.md)
Prompt-injecting other agents is allowed and barely moves a negotiation. Reporting another team earns points only
if correct and costs points if wrong: this task only RECORDS. Nothing is sent to the game; no extra game request.

## Design
- Table `injection_attempts` (schema.sql = `injection_log.DDL`): world, tick, source (`feed`, `team_thread`,
  `duel`, `dealer_thread`, `offer_text`), event/thread/duel/message ids (0 when not applicable), from_team, to_us,
  tags, severity (`attempt` = a strong shape; `weak` = JSON, URL or a priced verb alone), the RAW text verbatim
  (only our own secret values cut, at most 2,000 chars), the folded text, our_response, `proof` (the endpoint and
  ids to check it against), seen_at. Natural key (world, source, ids, tags): every writer and the backfill agree.
- Tags: `llm.chooser.injection_flags` (NFKD-folded patterns, zero-width and homoglyph detection).
- Writers, all buffered in the tick and written after the sends (one bounded transaction, retry every 5 ticks):
  the taker (its feed window, our team-thread payloads the team desk read, our dealer threads), the duel runner
  (`/api/duels` messages not `from: "you"`).
- `bazaar injections [--backfill] [--json] [--weak] [--limit] [--us]`: the backfill reads feed_events, messages +
  threads and duels (read-only on the game). Output escapes markup and shows hidden characters as ⟨U+XXXX⟩.
- The read-only role (`bazaar_team_ro`) reads it through the default privileges (DataGrip).

## Acceptance criteria
1. Red-team payloads (override, role tag, sell-all, invisible, no digits, long) are recorded as `attempt`, raw verbatim.
2. Zero-width, combining-joiner, Hangul-filler and homoglyph hiding is tagged and kept visible in the raw text.
3. Organiser text and our own words are never recorded; JSON or a URL alone is `weak`.
4. A dealer message seen in the feed and in our thread read is one row (same key).
5. Writes happen after the tick's sends; a database failure never raises into the tick and keeps the buffer.
6. The backfill finds every channel once; a rerun adds nothing; the CLI lists ASCII JSON with proofs.
7. The read-only role can select the table.
8. Full gate and the sim smoke pass.
