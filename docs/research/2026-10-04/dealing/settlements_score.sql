-- Every Saturday settlement with t01 as a party, with the score components just before
-- (last snapshot at tick < T) and after (the snapshot at tick T: a settlement at T is already in it; review item 10),
-- PRIVATE OUTPUT: a per-deal neg_points delta next to a public price gives our value; never commit the output.
-- plus the board 'negotiating' 12 ticks later (the board refreshes about every 10 ticks).
with st as (
  select tick, (payload->>'settlement')::int sid, coalesce(payload->>'persona', 'team') cp_kind,
         payload->>'venue' venue, (payload->>'price')::int price, (payload->>'fee')::int fee,
         (select string_agg(i->>'ref', ',') from jsonb_array_elements(payload->'items') i) refs,
         (select string_agg(left(i->>'rarity',1), ',') from jsonb_array_elements(payload->'items') i) rar,
         case when exists (select 1 from jsonb_array_elements(payload->'items') i where i->>'to' = 't01')
              then 'buy' else 'sell' end side,
         (select string_agg(p, ',') from jsonb_array_elements_text(payload->'parties') p where p <> 't01') cp
  from feed_events where type = 'settlement' and tick >= 159 and payload->'parties' ? 't01'
), s as (
  select tick, (score->>'neg_points')::numeric np, (score->>'ladder_points')::numeric lp,
         (score->>'negotiating')::numeric neg, (score->>'deals')::int deals
  from me_snapshots where team = 't01'
)
select st.tick, st.cp, st.side, st.refs, st.rar, st.price, st.fee, coalesce(st.venue,'-') venue,
       a.np - b.np dnp, a.lp - b.lp dlp, a.deals - b.deals ddeals,
       round(c.neg - b.neg, 2) dneg_board_12t
from st
cross join lateral (select * from s where s.tick < st.tick order by tick desc limit 1) b
cross join lateral (select * from s where s.tick >= st.tick order by tick limit 1) a
cross join lateral (select * from s where s.tick >= st.tick + 12 order by tick limit 1) c
order by st.tick;
