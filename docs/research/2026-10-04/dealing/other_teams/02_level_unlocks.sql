-- Level unlock per team: tick, level, why (earned vs "open to everyone now").
SELECT payload->>'team' team, (payload->>'level')::int lvl, tick, payload->>'why' why
FROM feed_events WHERE type='level.unlocked' ORDER BY team, lvl;
