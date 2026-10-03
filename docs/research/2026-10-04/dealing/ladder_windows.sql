-- Board 'negotiating' change for t01 vs the field median over windows where our only score change
-- was ladder (dealer deals) or one team trade. leaderboard_snapshots, world 'real'.
with w(label, a, b) as (values
  ('Pilar L3 sells x3 (718,726,737)', 710, 740),
  ('Picaros L4 buys x2 (771,790)', 760, 800),
  ('Picaros L4 buy LAV-10 (864)', 860, 870),
  ('SAL-07 to Pilar below value (948)', 940, 950),
  ('LAT-10 to t12 on rastro (1304)', 1300, 1310),
  ('Duels II without LAT-10 (1239-1300)', 1240, 1300),
  ('Duels II without LAT-10 (1310-1430)', 1310, 1430)
), d as (
  select w.label, s1.team, s2.negotiating - s1.negotiating dn
  from w join leaderboard_snapshots s1 on s1.tick = w.a and s1.world = 'real'
         join leaderboard_snapshots s2 on s2.tick = w.b and s2.world = 'real' and s2.team = s1.team
  where s1.team <> 't11'
)
select label, round(max(dn) filter (where team = 't01'), 2) t01,
       round((percentile_cont(0.5) within group (order by dn))::numeric, 2) field_median,
       round(max(dn) filter (where team = 't01') - (percentile_cont(0.5) within group (order by dn))::numeric, 2) excess
from d group by label order by min(label);
