-- Duels II (session 3): delivery days. For a SELLER every day ADDS your_days_weight to our result
-- (days_meaning; verified on 5632/5744). Compare the most days the rival ever offered with the
-- days in the closed deal: the gap is value the rival was willing to give and we did not take.
with m as (
  select d.duel, d.role, d.status, d.rounds, d.days final_days, d.result, d.your_limit,
         (d.payload->>'your_days_weight')::numeric w, d.decay_per_round dec,
         max((x->>'days')::int) filter (where x->>'from' <> 'you') rival_max_days,
         max((x->>'days')::int) filter (where x->>'from' = 'you') our_max_days,
         count(*) filter (where x->>'from' <> 'you' and (x->>'days')::int > 0) rival_msgs_with_days
  from duels d cross join lateral jsonb_array_elements(d.payload->'messages') x
  where d.session = 3 group by 1,2,3,4,5,6,7,8,9
)
select role, status, count(*) n,
       count(*) filter (where rival_max_days > 0) rival_offered_days,
       count(*) filter (where our_max_days > 0) we_offered_days,
       round(avg(rival_max_days), 1) mean_rival_max_days,
       round(sum(case when role='seller' and status='deal'
                 then w * greatest(rival_max_days - coalesce(final_days,0), 0) * power(1 - dec, rounds) end), 1)
         seller_days_value_left_P,
       round(sum(result), 1) sum_result
from m group by rollup(role, status) order by 1, 2;
