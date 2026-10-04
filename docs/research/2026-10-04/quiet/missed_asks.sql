-- Sell asks by others for page cards t01 did NOT hold at that tick, priced within our rarity caps.
WITH held AS (
  SELECT m.tick, array_agg(DISTINCT c->>'ref') refs FROM me_snapshots m, jsonb_array_elements(m.cards) c
  WHERE m.team='t01' AND m.tick>=159 GROUP BY m.tick),
asks AS (
  SELECT f.tick, f.actor, f.payload->'offer'->>'id' oid, f.payload->'offer'->>'to' to_,
         a->>'ref' ref, a->>'rarity' rarity, (f.payload->'offer'->'want'->>'cash')::int ask, f.payload->>'venue' venue
  FROM feed_events f, jsonb_array_elements(f.payload->'offer'->'give'->'assets') a
  WHERE f.type='offer.listed' AND f.tick>=159 AND f.actor<>'t01'
    AND jsonb_array_length(f.payload->'offer'->'give'->'assets')=1
    AND coalesce((f.payload->'offer'->'give'->>'cash')::int,0)=0
    AND jsonb_array_length(f.payload->'offer'->'want'->'assets')=0
    AND jsonb_array_length(coalesce(f.payload->'offer'->'want'->'types','[]'::jsonb))=0  -- swaps (want a card type) are not cash asks
    AND (f.payload->'offer'->>'to' IS NULL OR f.payload->'offer'->>'to'='t01'))
SELECT CASE WHEN a.tick<262 THEN 'a 159-261' WHEN a.tick<441 THEN 'b 262-440' WHEN a.tick<631 THEN 'c 441-630'
            WHEN a.tick<898 THEN 'd 631-897' WHEN a.tick<1202 THEN 'e 898-1201' ELSE 'f 1202-1445' END w,
       a.rarity, count(*) asks, count(DISTINCT a.ref) refs, min(a.ask), percentile_disc(0.5) WITHIN GROUP (ORDER BY a.ask) med,
       string_agg(DISTINCT a.ref, ',') 
FROM asks a JOIN held h ON h.tick = a.tick
WHERE NOT (a.ref = ANY(h.refs)) AND a.rarity IN ('common','uncommon','rare')
  AND a.ask <= CASE a.rarity WHEN 'common' THEN 12 WHEN 'uncommon' THEN 26 ELSE 95 END
GROUP BY 1,2 ORDER BY 1,2
