-- Message style per team in dealer threads (Saturday): share of priced team messages that carry text, mean text length,
-- mean price step between consecutive priced team messages in the same thread.
WITH m AS (
  SELECT (payload->>'thread')::int th, id, payload->>'sender' team, coalesce(payload->>'text','') txt,
         coalesce(nullif((payload->'offer'->'give'->>'cash')::int,0), (payload->'offer'->'want'->>'cash')::int) p
  FROM feed_events WHERE type='thread.message' AND tick >= 159 AND payload->>'sender' ~ '^t' AND payload ? 'offer'
), st AS (SELECT *, abs(p - lag(p) OVER (PARTITION BY th ORDER BY id)) step FROM m)
SELECT team, count(*) priced_msgs, round(100.0*count(*) FILTER (WHERE length(txt) > 0)/count(*)) pct_with_text,
       round(avg(length(txt)) FILTER (WHERE length(txt) > 0)) avg_len, round(avg(step),1) avg_step,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY step)::numeric,1) med_step
FROM st GROUP BY 1 ORDER BY 1;
