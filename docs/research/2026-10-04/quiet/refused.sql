WITH r AS (
  SELECT tick, candidates->>'offer_id' oid, candidates->>'ref' ref, (candidates->>'surplus')::numeric surplus,
         (candidates->>'total')::int total, candidates->>'rarity' rarity,
         policy_checks->>'guardrail' g, status
  FROM decisions WHERE agent='taker' AND kind='accept_ask' AND tick>=159),
c AS (
  SELECT *, (g LIKE '%cash_floor%')::int floor_, (g LIKE '%max_spend_per_game_hour%')::int spend_,
            (g ~ 'max_price_(uncommon|rare)')::int price_, (g LIKE '%block_buying_held%')::int held_
  FROM r)
SELECT CASE WHEN status='done' THEN 'TAKEN'
            WHEN g IS NULL OR g NOT LIKE 'denied%' THEN 'not denied by guard (lost slot/other)'
            ELSE concat_ws('+', CASE WHEN floor_=1 THEN 'floor' END, CASE WHEN spend_=1 THEN 'spendcap' END,
                           CASE WHEN price_=1 THEN 'pricecap' END, CASE WHEN held_=1 THEN 'held' END) END AS cls,
       CASE WHEN tick<262 THEN 'a 159-261' WHEN tick<441 THEN 'b 262-440' WHEN tick<631 THEN 'c 441-630' WHEN tick<898 THEN 'd 631-897' ELSE 'e 898+' END w,
       count(*) rows_, count(DISTINCT oid) offers, round(sum(s)) surplus_unique_offers
FROM (SELECT DISTINCT ON (oid, status, g) *, surplus s FROM c ORDER BY oid, status, g, tick) x
GROUP BY 1,2 ORDER BY 2,1
