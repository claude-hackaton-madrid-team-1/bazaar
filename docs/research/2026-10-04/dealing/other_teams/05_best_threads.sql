-- Top Saturday dealer deals by gain vs the dealer's opening (buy: discount; sell: premium), other teams only, ties broken by steps.
WITH o AS (SELECT (payload->>'thread')::bigint th, min(tick) tick FROM feed_events WHERE type='thread.opened' GROUP BY 1),
c AS (SELECT dc.*, o.tick, CASE WHEN item LIKE 'assets:%' THEN 'sell' ELSE 'buy' END side
      FROM dealer_curves dc JOIN o ON o.th=dc.thread_id WHERE o.tick >= 159 AND outcome='deal' AND opening_ask>0),
g AS (SELECT *, CASE WHEN side='buy' THEN 1 - fill_price::numeric/opening_ask ELSE fill_price::numeric/opening_ask - 1 END gain FROM c),
r AS (SELECT *, row_number() OVER (PARTITION BY dealer, side ORDER BY gain DESC, steps) rk FROM g WHERE NOT ours)
SELECT dealer, side, rk, thread_id, team, item, tick, opening_ask, fill_price, round(gain*100) gain_pct, steps, asks, bids
FROM r WHERE rk <= 4 ORDER BY dealer, side, rk;
