-- Our duels per session and role: deal rate, rounds, result as % of our limit, days used.
-- result = (|price - limit| + days term) * (1 - decay)^rounds (verified on Duels I and 5632 in Duels II).
select session, role, count(*) n, count(*) filter (where status='deal') deals,
       round(100.0 * count(*) filter (where status='deal') / count(*), 0) deal_rate_pct,
       round(avg(rounds) filter (where status='deal'), 2) mean_rounds_deal,
       round(avg(rounds) filter (where status='no_deal'), 2) mean_rounds_nodeal,
       count(*) filter (where status='deal' and rounds = 0) deals_0_rounds,
       count(*) filter (where status='deal' and rounds >= 4) deals_4plus_rounds,
       round(avg(100.0 * result / nullif(your_limit,0)) filter (where status='deal'), 1) mean_result_pct_limit,
       count(*) filter (where status='deal' and days > 0) deals_with_days,
       round(sum(result), 1) sum_result
from duels group by rollup(session, role) order by session nulls last, role nulls last;
