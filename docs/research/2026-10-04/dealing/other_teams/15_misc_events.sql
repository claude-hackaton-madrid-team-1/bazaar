-- Saturday counts per team of taller.crafted, gift.given, egg.found, badge.awarded, pack.opened (actor or payload team).
SELECT type, coalesce(payload->>'team', actor) team, count(*), min(tick), max(tick)
FROM feed_events WHERE tick >= 159 AND type IN ('taller.crafted','gift.given','egg.found','egg.given','badge.awarded','pack.opened')
GROUP BY 1,2 ORDER BY 1,3 DESC;
