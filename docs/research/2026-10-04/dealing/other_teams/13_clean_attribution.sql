-- "Clean" attribution: for each Saturday settlement after tick 610 (leaderboard data starts there), each team party's
-- negotiating delta across the snapshot window that contains it, kept only when that team had NO other settlement in
-- the window. field_med = median delta of all active teams in that window (relative-scoring drift).
-- kind: dealer_buy / dealer_sell (by persona) / board_buy / board_sell / swap.
WITH snaps AS (SELECT DISTINCT tick FROM leaderboard_snapshots),
w AS (SELECT lag(tick) OVER (ORDER BY tick) t0, tick t1 FROM snaps),
lb AS (SELECT team, tick, negotiating FROM leaderboard_snapshots),
field AS (
  SELECT w.t1, percentile_cont(0.5) WITHIN GROUP (ORDER BY b.negotiating - a.negotiating) med
  FROM w JOIN lb a ON a.tick = w.t0 JOIN lb b ON b.tick = w.t1 AND b.team = a.team WHERE a.team <> 't11' GROUP BY w.t1
),
s AS (
  SELECT f.id, f.tick, t.team, f.payload->>'persona' persona, f.payload->>'venue' venue, (f.payload->>'price')::int price,
    (SELECT string_agg(i->>'ref' || '/' || left(coalesce(i->>'rarity', i->>'kind'), 1), '+') FROM jsonb_array_elements(f.payload->'items') i) items,
    CASE WHEN f.payload->>'persona' IS NOT NULL THEN
           CASE WHEN EXISTS (SELECT 1 FROM jsonb_array_elements(f.payload->'items') i WHERE i->>'to' = t.team) THEN 'dealer_buy' ELSE 'dealer_sell' END
         WHEN (SELECT count(DISTINCT i->>'frm') FROM jsonb_array_elements(f.payload->'items') i) > 1 THEN 'swap'
         WHEN EXISTS (SELECT 1 FROM jsonb_array_elements(f.payload->'items') i WHERE i->>'to' = t.team) THEN 'board_buy'
         ELSE 'board_sell' END kind
  FROM feed_events f, jsonb_array_elements_text(f.payload->'parties') t(team)
  WHERE f.type = 'settlement' AND f.tick > 610 AND t.team ~ '^t'
),
sw AS (
  SELECT s.*, w.t0, w.t1, count(*) OVER (PARTITION BY s.team, w.t1) n_in_window
  FROM s JOIN w ON s.tick > w.t0 AND s.tick <= w.t1   -- a settlement at the snapshot tick is already in that snapshot (t16 banco t1110: -5.60 in 1100->1110)
)
SELECT sw.kind, coalesce(sw.persona, sw.venue) via, sw.team, sw.tick, sw.items, sw.price,
       round(b.negotiating - a.negotiating, 2) d_neg, round(field.med::numeric, 2) field_med
FROM sw JOIN lb a ON a.team = sw.team AND a.tick = sw.t0 JOIN lb b ON b.team = sw.team AND b.tick = sw.t1
JOIN field ON field.t1 = sw.t1
WHERE sw.n_in_window = 1
ORDER BY sw.kind, via, sw.tick;
