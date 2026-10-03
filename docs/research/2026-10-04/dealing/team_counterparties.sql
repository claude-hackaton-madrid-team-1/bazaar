-- Per other team, Saturday (tick >= 160): team threads we opened and swap offers we sent in them; public offers
-- addressed to us by them (inbound) and by us to them (outbound, all hand `bazaar sell list --to` posts); board
-- settlements with us; cash before and after the venue fee. The fee was always paid by our side on our buys and never
-- on our sells (me_snapshots.cash deltas at each settlement tick; on every one of our buys we were also the accepting
-- side, so buyer-pays and taker-pays cannot be told apart). neg_points per team are left out on purpose: one deal
-- per team next to its public price would give our value (review item 3).
with th as (
  select (response->>'id')::bigint tid, request->>'team' team
  from executions where sdk_method = 'open_thread' and request->>'team' is not null and error_code is null
), offers as (
  select th.team, count(*) n from executions e join th on th.tid = (e.request->>'thread_id')::bigint
  where e.sdk_method = 'say' and e.error_code is null group by 1
), inb as (
  select payload#>>'{offer,maker}' team, count(*) n from feed_events
  where type = 'offer.listed' and tick >= 160 and payload#>>'{offer,to}' = 't01' group by 1
), outb as (
  select payload#>>'{offer,to}' team, count(*) n from feed_events
  where type = 'offer.listed' and tick >= 160 and payload#>>'{offer,maker}' = 't01'
    and payload#>>'{offer,to}' is not null group by 1
), st as (
  select f.tick, (f.payload->>'price')::int price, (f.payload->>'fee')::int fee,
         (select p from jsonb_array_elements_text(f.payload->'parties') p where p <> 't01') team,
         exists (select 1 from jsonb_array_elements(f.payload->'items') i where i->>'to' = 't01') we_buy
  from feed_events f
  where f.type = 'settlement' and f.tick >= 160 and f.payload->'parties' ? 't01' and f.payload->>'persona' is null
), inbound_threads as (
  -- threads other teams opened with us are not in the feed; t03/t13 come from the one keyed /api/me/threads read
  select unnest(array['t03', 't13']) team
), teams as (
  select team from th union select team from st union select team from inb union select team from outb
  union select team from inbound_threads
)
select t.team,
  (select count(*) from th where th.team = t.team) threads_opened,
  coalesce((select n from offers o where o.team = t.team), 0) thread_offers_sent,
  coalesce((select n from outb where outb.team = t.team), 0) addressed_out,
  coalesce((select n from inb where inb.team = t.team), 0) addressed_in,
  (select count(*) from st where st.team = t.team and st.we_buy) board_buys,
  (select count(*) from st where st.team = t.team and not st.we_buy) board_sells,
  coalesce((select sum(price) from st where st.team = t.team and not st.we_buy), 0)
    - coalesce((select sum(price) from st where st.team = t.team and st.we_buy), 0) cash_before_fees,
  coalesce((select sum(fee) from st where st.team = t.team and st.we_buy), 0) fees_we_paid
from teams t order by t.team;
