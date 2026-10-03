-- Thread-level Saturday dealer negotiations rebuilt from the public feed (dealer_curves.fill_price is unreliable:
-- e.g. t07 threads 1399/1413 have their fills swapped vs the settlements at ticks 968/975).
-- One row per persona thread opened at tick >= 159.
-- side buy: dealer price = offer.want.cash (maker = dealer); team price = offer.give.cash.
-- side sell: dealer price = offer.give.cash; team price = offer.want.cash.
-- deal = settlement between the same team and persona, same item (card ref / pack ref / asset id), same direction,
--        between the open tick and the last message tick + 3 (closest in time wins).
WITH th AS (
  SELECT (payload->>'thread')::int th, min(tick) t0, payload->>'team' team, payload->>'with' dealer,
         CASE WHEN payload->'topic' ? 'sell' THEN 'sell' ELSE 'buy' END side, payload->'topic' topic
  FROM feed_events WHERE type='thread.opened' AND tick >= 159 GROUP BY 1,3,4,5,6
), m AS (
  SELECT (payload->>'thread')::int th, id, tick, payload->>'sender' sender,
         (payload->'offer'->'want'->>'cash')::int want, (payload->'offer'->'give'->>'cash')::int give,
         (payload->'offer'->>'final')::boolean fin
  FROM feed_events WHERE type='thread.message' AND tick >= 159
), agg AS (
  SELECT th.th, max(m.tick) t1,
    (array_agg(CASE WHEN th.side='buy' THEN m.want ELSE m.give END ORDER BY m.id) FILTER (WHERE m.sender=th.dealer AND (m.want>0 OR m.give>0)))[1] d_open,
    (array_agg(CASE WHEN th.side='buy' THEN m.want ELSE m.give END ORDER BY m.id DESC) FILTER (WHERE m.sender=th.dealer AND (m.want>0 OR m.give>0)))[1] d_last,
    (array_agg(CASE WHEN th.side='buy' THEN m.give ELSE m.want END ORDER BY m.id) FILTER (WHERE m.sender=th.team AND (m.want>0 OR m.give>0)))[1] t_open,
    count(*) FILTER (WHERE m.sender=th.team AND (m.want>0 OR m.give>0)) t_steps,
    count(*) FILTER (WHERE m.sender=th.dealer) d_msgs,
    bool_or(m.fin AND m.sender=th.dealer) d_final
  FROM th LEFT JOIN m ON m.th=th.th GROUP BY th.th
), s AS (
  SELECT tick, (payload->>'price')::int price, payload->>'persona' persona, i->>'frm' frm, i->>'to' "to", i->>'ref' ref, (i->>'id')::int aid, i->>'rarity' rarity, i->>'kind' ikind
  FROM feed_events, jsonb_array_elements(payload->'items') i WHERE type='settlement' AND tick >= 159 AND payload->>'persona' IS NOT NULL
), j AS (
  SELECT th.*, agg.t1, agg.d_open, agg.d_last, agg.t_open, agg.t_steps, agg.d_msgs, agg.d_final,
    (SELECT s.price FROM s WHERE s.persona=th.dealer AND s.tick BETWEEN th.t0 AND coalesce(agg.t1,th.t0)+3
       AND ((th.side='buy' AND s."to"=th.team AND (s.ref = th.topic->'buy'->>'card' OR s.ref = th.topic->'buy'->>'pack'
             OR (th.topic->'buy' ? 'set' AND s.ref LIKE (th.topic->'buy'->>'set')||'-%' AND s.rarity = th.topic->'buy'->>'rarity')))
         OR (th.side='sell' AND s.frm=th.team AND (th.topic->'sell'->'assets') @> to_jsonb(s.aid)))
     ORDER BY abs(s.tick - coalesce(agg.t1,th.t0)) LIMIT 1) fill,
    (SELECT coalesce(s.rarity, s.ikind) FROM s WHERE s.persona=th.dealer AND s.tick BETWEEN th.t0 AND coalesce(agg.t1,th.t0)+3
       AND ((th.side='buy' AND s."to"=th.team AND (s.ref = th.topic->'buy'->>'card' OR s.ref = th.topic->'buy'->>'pack'
             OR (th.topic->'buy' ? 'set' AND s.ref LIKE (th.topic->'buy'->>'set')||'-%' AND s.rarity = th.topic->'buy'->>'rarity')))
         OR (th.side='sell' AND s.frm=th.team AND (th.topic->'sell'->'assets') @> to_jsonb(s.aid)))
     ORDER BY abs(s.tick - coalesce(agg.t1,th.t0)) LIMIT 1) cls
  FROM th JOIN agg ON agg.th=th.th
)
SELECT * FROM j
