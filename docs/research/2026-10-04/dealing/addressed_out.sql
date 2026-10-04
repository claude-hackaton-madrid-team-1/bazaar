-- Our Saturday listings (feed offer.listed, maker t01, tick >= 160) not posted through an `executions` list_offer row:
-- which process posted them? Grouped by addressed/public, card, side, tick range.
with ex as (select (response->>'id')::bigint oid from executions where sdk_method = 'list_offer' and response ? 'id'),
o as (
  select tick, (payload#>>'{offer,id}')::bigint oid, payload#>>'{offer,to}' to_, payload#>>'{offer,venue}' venue,
         coalesce((select string_agg(a->>'ref', ',') from jsonb_array_elements(payload#>'{offer,give,assets}') a), '')
           || coalesce((select string_agg(t, ',') from jsonb_array_elements_text(payload#>'{offer,give,types}') t), '') give,
         coalesce((select string_agg(t, ',') from jsonb_array_elements_text(payload#>'{offer,want,types}') t), '') want_types,
         (payload#>>'{offer,give,cash}')::int give_cash, (payload#>>'{offer,want,cash}')::int want_cash
  from feed_events where type = 'offer.listed' and tick >= 160 and payload#>>'{offer,maker}' = 't01'
)
select case when oid in (select oid from ex) then 'executions' else 'other path' end path,
       case when to_ is null then 'public' else 'addressed' end kind,
       count(*), min(tick), max(tick), string_agg(distinct coalesce(to_, '-'), ',') to_teams
from o group by 1, 2 order by 1, 2;
