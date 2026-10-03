-- Per team: first (tick ~610) vs last Saturday leaderboard snapshot; score / negotiating / market / deals / level / pages.
WITH f AS (SELECT DISTINCT ON (team) * FROM leaderboard_snapshots ORDER BY team, tick),
     l AS (SELECT DISTINCT ON (team) * FROM leaderboard_snapshots ORDER BY team, tick DESC)
SELECT l.rank, l.team, f.tick t_first, l.tick t_last, l.score, round(l.score-f.score,2) d_score,
       l.negotiating, round(l.negotiating-f.negotiating,2) d_neg, l.market, round(l.market-f.market,2) d_mkt,
       f.deals deals_first, l.deals deals_last, l.level, l.pages, l.venue
FROM f JOIN l USING (team) ORDER BY l.rank;
