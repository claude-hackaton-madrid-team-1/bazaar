-- Duels II proxy (ticks 1240-1430): per team, the sum of `negotiating` deltas over snapshot windows in which the team
-- had NO settlement ("quiet windows": duels + relative drift only) vs windows with settlements. Duel results carry no
-- team in the public feed, so this is the only per-team view; it is confounded by the field moving.
WITH lb AS (
  SELECT team, tick, negotiating - lag(negotiating) OVER (PARTITION BY team ORDER BY tick) d, lag(tick) OVER (PARTITION BY team ORDER BY tick) t0
  FROM leaderboard_snapshots
), s AS (
  SELECT f.tick, t.team FROM feed_events f, jsonb_array_elements_text(f.payload->'parties') t(team)
  WHERE f.type='settlement' AND t.team ~ '^t'
)
SELECT lb.team,
  round(sum(d) FILTER (WHERE NOT EXISTS (SELECT 1 FROM s WHERE s.team=lb.team AND s.tick > lb.t0 AND s.tick <= lb.tick)),2) quiet_windows,
  count(*) FILTER (WHERE NOT EXISTS (SELECT 1 FROM s WHERE s.team=lb.team AND s.tick > lb.t0 AND s.tick <= lb.tick)) n_quiet,
  round(sum(d) FILTER (WHERE EXISTS (SELECT 1 FROM s WHERE s.team=lb.team AND s.tick > lb.t0 AND s.tick <= lb.tick)),2) deal_windows,
  round(sum(d),2) total
FROM lb WHERE lb.t0 >= 1240 AND lb.tick <= 1430 GROUP BY 1 ORDER BY 2 DESC;
