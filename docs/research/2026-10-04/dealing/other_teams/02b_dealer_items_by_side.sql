-- Saturday dealer settlements by persona, item kind, rarity and side (buy = card to team, sell = card to persona).
SELECT payload->>'persona' p, i->>'kind' k, i->>'rarity' r,
       CASE WHEN i->>'to'=payload->>'persona' THEN 'sell' ELSE 'buy' END side,
       count(*), round(avg((payload->>'price')::int),1) avgp, min((payload->>'price')::int), max((payload->>'price')::int)
FROM feed_events, jsonb_array_elements(payload->'items') i
WHERE type='settlement' AND tick>=159 AND payload->>'persona' IS NOT NULL
GROUP BY 1,2,3,4 ORDER BY 1,4,2,3;
