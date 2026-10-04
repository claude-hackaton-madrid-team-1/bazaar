-- Duels as a counterparty row: priced messages each way per session, our accepts (decisions), rounds paid,
-- and the decay those rounds cost (share of the result lost to (1 - decay)^rounds, deals only).
with m as (
  select d.session, d.duel, d.status, d.rounds, d.decay_per_round dec, d.result,
         count(*) filter (where x->>'from' = 'you' and x->>'price' is not null) ours,
         count(*) filter (where x->>'from' <> 'you' and x->>'price' is not null) theirs
  from duels d left join lateral jsonb_array_elements(d.payload->'messages') x on true
  group by 1,2,3,4,5,6
)
select session, count(*) duels, sum(ours) our_priced_msgs, sum(theirs) rival_priced_msgs,
       count(*) filter (where status='deal') deals,
       sum(rounds) filter (where status='deal') rounds_paid,
       round(sum(result / power(1 - dec, rounds)) filter (where status='deal') - sum(result) filter (where status='deal'), 1)
         result_lost_to_decay_P,
       round(sum(result) filter (where status='deal'), 1) result_P
from m group by 1 order by 1;
