-- Duels II (session 3) per rival pseudonym: duels, deals, rounds per duel, result lost to decay.
select rival, count(*) duels, count(*) filter (where status = 'deal') deals,
       round(avg(rounds), 2) rounds_per_duel,
       round(coalesce(sum(result / power(1 - decay_per_round, rounds) - result) filter (where status = 'deal'), 0), 1)
         lost_to_decay_P
from duels where session = 3 group by rival order by rounds_per_duel desc;
