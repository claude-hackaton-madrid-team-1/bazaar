-- Jumps in a team's `negotiating` between consecutive leaderboard snapshots (|delta| >= 0.6), with that team's
-- settlements in the window (prev snapshot tick - 2, tick] and the field's median delta in the same window.
-- Kinds: B=dealer buy, S=dealer sell (persona initial), b/s = board buy/sell, x = swap. Price after '@'.
WITH lb AS (
  SELECT team, tick, negotiating, lag(negotiating) OVER w prev, lag(tick) OVER w ptick
  FROM leaderboard_snapshots WINDOW w AS (PARTITION BY team ORDER BY tick)
), field AS (
  SELECT tick, percentile_cont(0.5) WITHIN GROUP (ORDER BY negotiating - prev) med FROM lb WHERE prev IS NOT NULL AND team <> 't11' GROUP BY tick
), s AS (
  SELECT f.tick, t.team, (f.payload->>'price')::int price, f.payload->>'persona' persona, f.payload->>'venue' venue, f.payload->'items' items
  FROM feed_events f, jsonb_array_elements_text(f.payload->'parties') t(team)
  WHERE f.type='settlement' AND f.tick >= 600 AND t.team ~ '^t'
)
SELECT lb.team, lb.ptick, lb.tick, round(lb.negotiating - lb.prev, 2) d_neg, round(field.med::numeric, 2) field_med,
  (SELECT string_agg(
     CASE WHEN s.persona IS NOT NULL THEN
            (CASE WHEN EXISTS (SELECT 1 FROM jsonb_array_elements(s.items) i WHERE i->>'to'=lb.team) THEN 'B:' ELSE 'S:' END) || s.persona
          WHEN (SELECT count(DISTINCT i->>'frm') FROM jsonb_array_elements(s.items) i) > 1 THEN 'x:' || coalesce(s.venue,'')
          WHEN EXISTS (SELECT 1 FROM jsonb_array_elements(s.items) i WHERE i->>'to'=lb.team) THEN 'b:' || coalesce(s.venue,'')
          ELSE 's:' || coalesce(s.venue,'') END
     || ' ' || (SELECT string_agg(i->>'ref', '+') FROM jsonb_array_elements(s.items) i) || '@' || s.price, ', ' ORDER BY s.tick)
   FROM s WHERE s.team = lb.team AND s.tick > lb.ptick - 2 AND s.tick <= lb.tick) deals
FROM lb JOIN field ON field.tick = lb.tick
WHERE lb.prev IS NOT NULL AND abs(lb.negotiating - lb.prev) >= 0.6
ORDER BY lb.tick, lb.team;
