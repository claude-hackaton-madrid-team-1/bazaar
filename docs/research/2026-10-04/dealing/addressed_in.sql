-- Saturday (tick >= 160) public offers addressed to us (offer.to = 't01'), per maker team and shape:
-- bid = they give cash for one of our cards; ask = they sell us a card for cash; swap = card(s) for card(s).
with o as (
  select tick, payload#>>'{offer,maker}' maker, payload->'offer' off from feed_events
  where type = 'offer.listed' and tick >= 160 and payload#>>'{offer,to}' = 't01'
)
select maker, count(*) n,
  count(*) filter (where (off#>>'{give,cash}')::int > 0 and jsonb_array_length(off#>'{give,assets}') = 0
                   and jsonb_array_length(off#>'{give,types}') = 0) bids,
  count(*) filter (where (off#>>'{want,cash}')::int > 0 and jsonb_array_length(off#>'{want,types}') = 0
                   and jsonb_array_length(off#>'{want,assets}') = 0) asks,
  min(tick) first_tick, max(tick) last_tick
from o group by maker order by n desc;
