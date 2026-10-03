-- Scored duel no-deals (sessions 2-3): did the rival ever offer a price inside our limit
-- (seller: rival price >= limit; buyer: rival price <= limit), ignoring days? Was the rival silent?
with m as (
  select d.duel, d.session, d.role, d.your_limit lim, d.deadline_tick,
         count(*) filter (where x->>'from' <> 'you' and x->>'price' is not null) rival_priced,
         count(*) filter (where x->>'from' = 'you' and x->>'price' is not null) we_priced,
         bool_or(case when x->>'from' <> 'you' and x->>'price' is not null then
               case when d.role = 'seller' then (x->>'price')::int >= d.your_limit
                    else (x->>'price')::int <= d.your_limit end end) rival_inside,
         max((x->>'tick')::int) filter (where x->>'from' <> 'you' and x->>'price' is not null
               and case when d.role = 'seller' then (x->>'price')::int >= d.your_limit
                        else (x->>'price')::int <= d.your_limit end) last_inside_tick
  from duels d left join lateral jsonb_array_elements(d.payload->'messages') x on true
  where d.status = 'no_deal' and d.session >= 2 group by 1,2,3,4,5
)
select session, duel, role, rival_priced, we_priced, coalesce(rival_inside, false) rival_inside,
       deadline_tick - last_inside_tick ticks_before_deadline
from m order by session, duel;
