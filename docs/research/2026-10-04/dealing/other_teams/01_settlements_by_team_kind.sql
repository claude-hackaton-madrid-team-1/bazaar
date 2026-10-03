-- Saturday (tick >= 159) settlements per team by kind.
-- dealer_buy: card moves persona -> team; dealer_sell: team -> persona; board: no persona, cash trade; swap: no persona, cards both ways.
WITH s AS (
  SELECT tick, payload->>'persona' persona, payload->>'venue' venue, (payload->>'price')::int price,
         payload->'items' items, payload->'parties' parties
  FROM feed_events WHERE type='settlement' AND tick >= 159
), legs AS (
  SELECT s.*, i->>'frm' frm, i->>'to' "to", i->>'rarity' rarity FROM s, jsonb_array_elements(items) i
), per AS (
  SELECT team, persona, venue, kind, count(*) n FROM (
    SELECT DISTINCT ON (s.tick, s.items::text, t.team) t.team, s.persona, s.venue,
      CASE WHEN s.persona IS NOT NULL AND EXISTS (SELECT 1 FROM jsonb_array_elements(s.items) i WHERE i->>'to'=t.team) THEN 'dealer_buy'
           WHEN s.persona IS NOT NULL THEN 'dealer_sell'
           WHEN (SELECT count(DISTINCT i->>'frm') FROM jsonb_array_elements(s.items) i) > 1 THEN 'swap'
           WHEN EXISTS (SELECT 1 FROM jsonb_array_elements(s.items) i WHERE i->>'to'=t.team) THEN 'board_buy'
           ELSE 'board_sell' END kind
    FROM s, jsonb_array_elements_text(s.parties) t(team)
    WHERE t.team ~ '^t[0-9]+$'
  ) x GROUP BY 1,2,3,4
)
SELECT team,
  sum(n) FILTER (WHERE kind='dealer_buy' AND persona='abuela') ab_buy,
  sum(n) FILTER (WHERE kind='dealer_sell' AND persona='abuela') ab_sell,
  sum(n) FILTER (WHERE kind='dealer_buy' AND persona='chato') ch_buy,
  sum(n) FILTER (WHERE kind='dealer_sell' AND persona='chato') ch_sell,
  sum(n) FILTER (WHERE kind='dealer_buy' AND persona='pilar') pi_buy,
  sum(n) FILTER (WHERE kind='dealer_sell' AND persona='pilar') pi_sell,
  sum(n) FILTER (WHERE kind='dealer_buy' AND persona='picaros') pc_buy,
  sum(n) FILTER (WHERE kind='dealer_sell' AND persona='picaros') pc_sell,
  sum(n) FILTER (WHERE persona='banco') banco,
  sum(n) FILTER (WHERE persona IS NOT NULL) dealer_total,
  sum(n) FILTER (WHERE kind IN ('board_buy','board_sell') AND venue='rastro') rastro,
  sum(n) FILTER (WHERE kind IN ('board_buy','board_sell') AND venue<>'rastro') team_venue,
  sum(n) FILTER (WHERE kind='swap') swaps
FROM per GROUP BY team ORDER BY dealer_total DESC NULLS LAST;
