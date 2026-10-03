-- Pilar's purchases by set and rarity: before the Salamanca fever (< 939), during (939-1178), after (>= 1179).
SELECT CASE WHEN f.tick < 939 THEN '1 before' WHEN f.tick < 1179 THEN '2 fever' ELSE '3 after' END phase,
       i->>'set' set, i->>'rarity' rarity, count(*) n, round(avg((f.payload->>'price')::int),1) avg_price,
       min((f.payload->>'price')::int), max((f.payload->>'price')::int),
       string_agg(DISTINCT i->>'frm', ',') sellers
FROM feed_events f, jsonb_array_elements(f.payload->'items') i
WHERE f.type='settlement' AND f.tick >= 159 AND f.payload->>'persona'='pilar'
GROUP BY 1,2,3 ORDER BY 2,3,1;
