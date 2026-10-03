-- Saturday dealer threads (opened tick >= 159) from dealer_curves joined to feed thread.opened for the tick.
-- side: buy = team buys from dealer; sell = item 'assets:...' (team sells to dealer).
-- For buys, opening_ask is the dealer's first ask: discount = 1 - fill/opening.
-- For sells, opening_ask is the dealer's first bid: premium = fill/opening - 1.
WITH o AS (SELECT (payload->>'thread')::bigint th, min(tick) tick FROM feed_events WHERE type='thread.opened' GROUP BY 1),
c AS (SELECT dc.*, o.tick, CASE WHEN item LIKE 'assets:%' THEN 'sell' ELSE 'buy' END side
      FROM dealer_curves dc JOIN o ON o.th=dc.thread_id WHERE o.tick >= 159)
SELECT dealer, side, count(*) threads, count(*) FILTER (WHERE outcome='deal') deals,
  round(100.0*count(*) FILTER (WHERE outcome='deal')/count(*),0) deal_pct,
  round(avg(CASE WHEN outcome='deal' AND opening_ask>0 THEN
     CASE WHEN side='buy' THEN 1 - fill_price::numeric/opening_ask ELSE fill_price::numeric/opening_ask - 1 END END)*100,1) gain_vs_open_pct,
  round(avg(steps) FILTER (WHERE outcome='deal'),1) steps_deal,
  round(avg(steps) FILTER (WHERE outcome<>'deal'),1) steps_nodeal
FROM c GROUP BY 1,2 ORDER BY 1,2;
