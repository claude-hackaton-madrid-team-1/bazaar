-- Saturday clock (organiser pauses), our settlements per window vs the field, cash room per window (bucketed).
SELECT received_at AT TIME ZONE 'Europe/Madrid' AS madrid, tick, type, payload
FROM feed_events WHERE type IN ('clock.changed','day.opened','day.closed') AND tick >= 159 ORDER BY received_at;

WITH p AS (SELECT tick, buyer team FROM tape WHERE tick>=159 UNION ALL SELECT tick, seller FROM tape WHERE tick>=159),
w AS (SELECT CASE WHEN tick<262 THEN 'A' WHEN tick<441 THEN 'B' WHEN tick<631 THEN 'C' WHEN tick<898 THEN 'D'
                  WHEN tick<1202 THEN 'E' ELSE 'F' END w, team, count(*) n FROM p WHERE team ~ '^t[0-9]' GROUP BY 1,2)
SELECT w, sum(n) FILTER (WHERE team='t01') t01,
       percentile_disc(0.5) WITHIN GROUP (ORDER BY n) FILTER (WHERE team<>'t01') med_others, max(n)
FROM w GROUP BY 1 ORDER BY 1;

-- cash room above the cash_floor in force (floor history from the guardrail refusal texts: 270, 100, 50, 20, 5)
WITH c AS (SELECT tick, cash - CASE WHEN tick<262 THEN 270 WHEN tick<319 THEN 100 WHEN tick<773 THEN 50
                                    WHEN tick<858 THEN 20 ELSE 5 END room
           FROM me_snapshots WHERE team='t01' AND tick BETWEEN 159 AND 1445)
SELECT CASE WHEN tick<262 THEN 'A' WHEN tick<441 THEN 'B' WHEN tick<631 THEN 'C' WHEN tick<898 THEN 'D'
            WHEN tick<1202 THEN 'E' ELSE 'F' END w, count(*) ticks,
       count(*) FILTER (WHERE room<25) lt25, count(*) FILTER (WHERE room BETWEEN 25 AND 59) r25_59,
       count(*) FILTER (WHERE room>=60) ge60
FROM c GROUP BY 1 ORDER BY 1;

-- refusals by guardrail class (policy_checks->>'guardrail')
SELECT agent, kind, regexp_replace(regexp_replace(coalesce(policy_checks->>'guardrail', policy_checks::text),
       '[A-Z]{3}-[0-9]+', 'CARD', 'g'), '[0-9]+(\.[0-9]+)?', 'N', 'g') AS g, count(*), min(tick), max(tick)
FROM decisions WHERE tick>=159 AND status IN ('rejected','failed','expired') GROUP BY 1,2,3 ORDER BY 1,2,4 DESC;

-- team swap ladder: which steps were ever posted, and the cancel results
SELECT substring(d.reason from 'step ([0-9]+) of the ladder') step, d.status, e.sdk_method, e.error_code, count(*),
       min(d.tick), max(d.tick)
FROM decisions d LEFT JOIN executions e ON e.decision_id=d.id
WHERE d.kind='team_offer' AND d.tick>=159 GROUP BY 1,2,3,4 ORDER BY 1,2;

-- Jev swap verdicts before / after the LLM decider (#213, ~16:55, tick ~805)
SELECT CASE WHEN tick<805 THEN 'pre' ELSE 'post' END p, jev->>'reason' r, jev->>'verdict' v, count(*),
       round(avg((jev->>'value')::numeric),2)
FROM decisions WHERE tick>=159 AND jev IS NOT NULL AND agent='taker' GROUP BY 1,2,3 ORDER BY 1,4 DESC;
