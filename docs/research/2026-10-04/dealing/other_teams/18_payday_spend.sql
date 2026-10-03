-- Payday (+400 P to every team at tick 1201): what each team bought afterwards (settlements where a card/pack moves to
-- the team), and Pícaros buys per team by phase (before L4-for-all 881, 881-1200, after Payday).
SELECT t.team, count(*) n_buys, sum((f.payload->>'price')::int) spend
FROM feed_events f, jsonb_array_elements_text(f.payload->'parties') t(team)
WHERE f.type='settlement' AND f.tick >= 1201 AND t.team ~ '^t'
  AND EXISTS (SELECT 1 FROM jsonb_array_elements(f.payload->'items') i WHERE i->>'to' = t.team)
GROUP BY 1 ORDER BY 3 DESC;

SELECT t.team, count(*) FILTER (WHERE f.tick < 881) pre881, count(*) FILTER (WHERE f.tick >= 881 AND f.tick < 1201) mid,
       count(*) FILTER (WHERE f.tick >= 1201) post_payday, sum((f.payload->>'price')::int) FILTER (WHERE f.tick >= 1201) spend_post
FROM feed_events f, jsonb_array_elements_text(f.payload->'parties') t(team)
WHERE f.type='settlement' AND f.payload->>'persona'='picaros' AND t.team ~ '^t'
  AND EXISTS (SELECT 1 FROM jsonb_array_elements(f.payload->'items') i WHERE i->>'to' = t.team)
GROUP BY 1 ORDER BY 1;
