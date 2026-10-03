-- t01 score components every ~20 ticks plus around key settlements (me_snapshots).
select tick, cash, score->>'rank' rnk, score->>'score' sc, score->>'negotiating' neg, score->>'market' mkt,
       score->>'neg_points' np, score->>'duel_points' dp, score->>'ladder_points' lp, score->>'deals' deals,
       score->>'pages_complete' pages
from me_snapshots where team='t01'
  and (tick % 20 = 0 or tick between 1296 and 1316 or tick >= 1435 or tick between 1236 and 1242)
order by tick;
