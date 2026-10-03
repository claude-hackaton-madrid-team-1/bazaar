-- Board asks refused by a guardrail, per card: best model surplus refused, and whether/when we got the card later.
WITH r AS (
  SELECT tick, candidates->>'ref' ref, (candidates->>'surplus')::numeric surplus, (candidates->>'total')::int total,
         policy_checks->>'guardrail' g
  FROM decisions WHERE agent='taker' AND kind='accept_ask' AND tick>=159 AND status='rejected'
    AND policy_checks->>'guardrail' LIKE 'denied%'),
cls AS (
  SELECT *, CASE WHEN g ~ 'max_price_' AND g !~ '(cash_floor|max_spend)' THEN 'price cap only'
                 WHEN g ~ 'max_price_' THEN 'price cap + cash/spend'
                 WHEN g ~ 'block_buying_held' AND g !~ '(cash_floor|max_spend)' THEN 'held only'
                 ELSE 'cash floor / spend cap only' END c FROM r),
got AS (
  SELECT card_id ref, min(tick) t, min(price) p FROM tape WHERE buyer='t01' AND tick>=159 GROUP BY 1)
SELECT c, cls.ref, min(cls.tick) first_refused, max(cls.tick) last_refused, count(*) n, max(surplus) best_surplus,
       min(total) cheapest_total, got.t got_tick, got.p got_price
FROM cls LEFT JOIN got ON got.ref = cls.ref AND got.t >= (SELECT min(tick) FROM cls c2 WHERE c2.ref=cls.ref)
GROUP BY 1,2,8,9 ORDER BY 1, best_surplus DESC
