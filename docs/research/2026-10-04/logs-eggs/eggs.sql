-- Read-only egg watcher (sat-logs-eggs). Run with q.py; SELECTs only, zero game requests.
-- 1) Every egg / badge / egg gift, with the dealer reply in the same thread on the egg's tick
--    (team texts are null in the public feed: the reply is the only evidence of the trigger).
WITH e AS (
  SELECT id, tick, type, payload->>'team' AS team, payload->>'persona' AS persona,
         coalesce(payload->>'badge', array_to_string(ARRAY(SELECT jsonb_array_elements_text(payload->'cards')), ',')
                  || array_to_string(ARRAY(SELECT jsonb_array_elements_text(payload->'packs')), ',')) AS reward
  FROM feed_events WHERE type IN ('egg.found', 'badge.awarded', 'egg.given')
)
SELECT e.tick, e.type, e.team, coalesce(e.persona, '') AS persona, e.reward,
       left(replace(m.payload->>'text', E'\n', ' '), 240) AS dealer_reply
FROM e LEFT JOIN LATERAL (
  SELECT payload FROM feed_events m
  WHERE m.type = 'thread.message' AND m.payload->>'team' = e.team AND m.payload->>'with' = e.persona
    AND m.actor = e.persona AND m.tick BETWEEN e.tick - 1 AND e.tick
  ORDER BY m.id DESC LIMIT 1) m ON true
ORDER BY e.tick, e.team;

-- 2) Hint lines dealers sent to US (threads.ours): what we were told and never followed up.
-- SELECT t.counterpart, m.tick, left(m.text, 240) FROM messages m JOIN threads t ON t.id = m.thread_id
--  WHERE t.ours AND m.sender = t.counterpart
--    AND m.text ~* '(chulapa|mosc|estampita|lazarillo|rinconete|chotis|baldosa|cocido|rosquilla|santo|plaza mayor|cascorro|casa prima|ernesto|ask (her|him))'
--  ORDER BY m.tick;

-- 3) Sunday leads: new eggs at Pilar / Los Pícaros after the open, and Chamberí lore in dealer replies.
-- SELECT tick, type, payload FROM feed_events
--  WHERE type = 'egg.found' AND payload->>'persona' IN ('picaros', 'pilar') AND tick > 1445 ORDER BY tick;
-- SELECT tick, actor, payload->>'team' AS team, left(payload->>'text', 240) FROM feed_events
--  WHERE type = 'thread.message' AND actor IN ('abuela', 'chato', 'pilar', 'picaros', 'banco')
--    AND payload->>'text' ~* '(chamber|andén|anden|fantasma)' AND tick > 1445 ORDER BY tick;

-- 4) Secret cards now visible in the catalog (= the public catalogue's "Secret cards found" KPI).
--    A hidden card appears in /api/catalog only once someone finds it; the monitor upserts it into `cards`.
-- SELECT id, rarity, print_run, minted, updated_tick FROM cards WHERE hidden ORDER BY id;
