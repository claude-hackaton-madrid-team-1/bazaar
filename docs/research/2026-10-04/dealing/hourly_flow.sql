-- Deal flow per Madrid wall-clock hour on Saturday (tick -> wall time from the first feed event of each tick).
with tw as (
  select tick, min(received_at at time zone 'Europe/Madrid') wall from feed_events where tick >= 160 group by tick
), h as (select tick, extract(hour from wall)::int hr from tw)
select h.hr,
  min(h.tick) first_tick, max(h.tick) last_tick,
  (select count(*) from feed_events f join h h2 on h2.tick=f.tick where h2.hr=h.hr and f.type='settlement'
     and f.payload->'parties' ? 't01' and f.payload->>'persona' is not null) dealer_deals,
  (select count(*) from feed_events f join h h2 on h2.tick=f.tick where h2.hr=h.hr and f.type='settlement'
     and f.payload->'parties' ? 't01' and f.payload->>'persona' is null) team_deals,
  (select count(*) from feed_events f join h h2 on h2.tick=f.tick where h2.hr=h.hr and f.type='thread.opened'
     and f.payload->>'team'='t01') dealer_threads,
  (select count(*) from executions e join h h2 on h2.tick=e.tick where h2.hr=h.hr and e.sdk_method='open_thread'
     and e.request->>'team' is not null and e.error_code is null) team_threads,
  (select count(*) from decisions d join h h2 on h2.tick=d.tick where h2.hr=h.hr and d.agent='duels' and d.kind='duel_accept') duel_accepts,
  (select count(*) from decisions d join h h2 on h2.tick=d.tick where h2.hr=h.hr and d.kind='process_started') taker_restarts,
  (select count(*) from decisions d join h h2 on h2.tick=d.tick where h2.hr=h.hr and d.agent='taker' and d.kind='accept_ask' and d.status='rejected') board_accepts_rejected
from h group by h.hr order by h.hr;
