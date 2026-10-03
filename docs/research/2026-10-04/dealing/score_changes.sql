-- Every tick where one of t01's score components changed (me_snapshots, Saturday).
with s as (
  select tick, cash,
         (score->>'score')::numeric sc, (score->>'negotiating')::numeric neg, (score->>'market')::numeric mkt,
         (score->>'neg_points')::numeric np, (score->>'duel_points')::numeric dp, (score->>'ladder_points')::numeric lp,
         (score->>'deals')::int deals, (score->>'rank')::int rnk, (score->>'level')::int lvl,
         (score->>'pages_complete')::int pages
  from me_snapshots where team = 't01'
), d as (
  select *, lag(np) over w lnp, lag(dp) over w ldp, lag(lp) over w llp, lag(deals) over w ldeals,
         lag(neg) over w lneg, lag(tick) over w ltick, lag(lvl) over w llvl, lag(pages) over w lpages
  from s window w as (order by tick)
)
select ltick, tick, rnk, sc, neg, round(neg - lneg, 2) dneg, np, round(np - lnp, 1) dnp, dp, round(dp - ldp, 2) ddp,
       lp, round(lp - llp, 3) dlp, deals, deals - ldeals ddeals, lvl, pages
from d
where np is distinct from lnp or lp is distinct from llp or deals is distinct from ldeals
   or lvl is distinct from llvl or pages is distinct from lpages
order by tick;
