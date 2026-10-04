-- other-dealers, Sun 4 Oct. Read-only. Tick 1445 = 09:00 local.
select counterpart, kind, status, closed_reason, count(*) from threads where opened_tick >= 1380 group by 1,2,3,4 order by 1;
select id, tick, kind, status, reason from decisions
 where tick >= 1445 and kind in ('ladder_slot','ladder_probe','dealer_skip','strategy_gate') order by id desc;
select left(regexp_replace(reason,'[0-9]+','N','g'),140) r, count(*) from decisions
 where tick >= 1445 and kind in ('dealer_open','dealer_skip') and status = 'rejected' group by 1 order by 2 desc;
select coalesce(payload->>'with','?') w, count(*) from feed_events where type = 'thread.opened' and tick >= 1445 group by 1;
with s as (select tick, i->>'frm' frm, i->>'to' t, i->>'ref' ref, i->>'rarity' r, i->>'set' st
             from feed_events, jsonb_array_elements(payload->'items') i where type = 'settlement')
select t dealer, case when tick >= 1445 then 'sun' else 'sat' end d, r, st, count(*), string_agg(ref, ' ')
  from s where t in ('pilar','chato','banco') group by 1,2,3,4 order by 1,2,3,4;
