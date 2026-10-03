-- Round trips: a team buys an asset (by asset id) and later sells the same asset, Saturday. Source/sink = persona or board venue.
WITH s AS (
  SELECT f.tick, coalesce(f.payload->>'persona', 'board:'||coalesce(f.payload->>'venue','?')) via, (f.payload->>'price')::int price,
         (i->>'id')::int aid, i->>'ref' ref, i->>'rarity' rarity, i->>'frm' frm, i->>'to' "to",
         jsonb_array_length(f.payload->'items') nitems
  FROM feed_events f, jsonb_array_elements(f.payload->'items') i
  WHERE f.type='settlement' AND f.tick >= 159
)
SELECT b."to" team, b.ref, b.rarity, b.via bought_from, b.tick t_buy, b.price p_buy, x.via sold_to, x.tick t_sell, x.price p_sell, x.price - b.price margin
FROM s b JOIN s x ON x.aid = b.aid AND x.frm = b."to" AND x.tick > b.tick
WHERE b."to" ~ '^t' AND b.nitems = 1 AND x.nitems = 1
  AND NOT EXISTS (SELECT 1 FROM s m WHERE m.aid = b.aid AND m.tick > b.tick AND m.tick < x.tick)
ORDER BY margin DESC;
