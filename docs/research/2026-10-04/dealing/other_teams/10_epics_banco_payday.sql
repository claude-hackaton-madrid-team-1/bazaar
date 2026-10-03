-- Epic trades on Saturday (Picaros sells epics, Banco/Pilar buy epics, team board epics) with the team's
-- `negotiating` at the snapshot before and 10-20 ticks after.
WITH s AS (
  SELECT f.tick, f.payload->>'persona' persona, f.payload->>'venue' venue, (f.payload->>'price')::int price, i->>'ref' ref, i->>'frm' frm, i->>'to' "to"
  FROM feed_events f, jsonb_array_elements(f.payload->'items') i
  WHERE f.type='settlement' AND f.tick >= 159 AND i->>'rarity' = 'epic'
)
SELECT s.tick, coalesce(s.persona, 'board:'||s.venue) via, s.ref, s.frm, s."to", s.price,
  (SELECT negotiating FROM leaderboard_snapshots l WHERE l.team = CASE WHEN s."to" ~ '^t' THEN s."to" ELSE s.frm END AND l.tick <= s.tick ORDER BY tick DESC LIMIT 1) neg_before,
  (SELECT negotiating FROM leaderboard_snapshots l WHERE l.team = CASE WHEN s."to" ~ '^t' THEN s."to" ELSE s.frm END AND l.tick >= s.tick + 2 ORDER BY tick LIMIT 1) neg_after
FROM s ORDER BY s.tick;
