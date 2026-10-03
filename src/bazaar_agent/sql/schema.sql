-- Bazaar memory schema (spec .ai/specs/01-spec.md §5). All ticks are game ticks.
-- Idempotent and safe to re-run while other processes are connected (several laptops share one
-- Railway Postgres): one transaction, serialized by an advisory lock, with a lock timeout so a
-- busy table makes `init` fail fast instead of queueing every other session behind it.
-- Works without pgvector (Railway's default image has none): the 384-d embedding columns (local
-- fastembed model) are added at the end, only when the extension exists.
set local lock_timeout = '15s';
select pg_advisory_xact_lock(hashtext('bazaar_agent.schema'));

-- pgvector when the server ships it. Always in `public`, never in a test schema that gets dropped.
do $$
begin
  create extension if not exists vector with schema public;
exception when others then
  raise notice 'pgvector unavailable (%): embedding columns skipped', sqlerrm;
end $$;

-- Reference data
create table if not exists cards (
  id text primary key, set_code text, name text, rarity text, book numeric,
  print_run int, minted int, released bool, updated_tick int);
create table if not exists traders (
  id text primary key, kind text check (kind in ('dealer','team','bench','rival_alias')),
  name text, level int, traits jsonb, menu jsonb, unlock jsonb,
  first_seen_tick int, last_seen_tick int, status text, updated_tick int);

-- Raw memory (append-only; the collector is the only writer)
create table if not exists feed_events (
  id bigint primary key, tick int, type text, actor text, payload jsonb,
  received_at timestamptz default now());
create index if not exists feed_events_tick on feed_events (tick);
create index if not exists feed_events_type on feed_events (type);
create table if not exists threads (
  id bigint primary key, counterpart text, kind text, topic jsonb, venue text, status text,
  opened_tick int, closed_tick int, closed_reason text, ours bool default false);
create table if not exists messages (
  id bigint primary key, thread_id bigint, sender text, tick int, text text, price int,
  offer jsonb, final bool);
create index if not exists messages_thread on messages (thread_id, tick);
create table if not exists offers (
  id bigint primary key, thread_id bigint, maker text, to_ text, venue text, give jsonb,
  want jsonb, created_tick int, expires_tick int, final bool, status text);
create table if not exists snapshots (
  tick int primary key, cash int, level int, assets jsonb, album jsonb, score jsonb);
create table if not exists card_values (
  card_id text, tick int, your_value numeric, copies int, primary key (card_id, tick));

-- Market intel: the live game as an order book
create table if not exists book_levels (
  offer_id bigint primary key, venue text, card_id text, side text check (side in ('bid','ask')),
  price int, maker text, created_tick int, expires_tick int, status text);
create table if not exists tape (
  settlement_id bigint primary key, tick int, venue text, persona text, buyer text, seller text,
  items jsonb, card_id text, price int, fee int);
create index if not exists tape_card on tape (card_id, tick);
create table if not exists dealer_curves (
  thread_id bigint primary key, dealer text, team text, item text, opening_ask int,
  asks int[], bids int[], final_ask int, outcome text, fill_price int, steps int, ticks int);
create table if not exists competitor_profiles (
  team text primary key, updated_tick int, set_interest jsonb, avg_pack_price numeric,
  dealer_deal_rate numeric, concession_style jsonb, listings jsonb, fills jsonb,
  level int, venue text, notes jsonb);

-- Behaviour memory and learnings
create table if not exists trader_behaviors (
  id bigserial primary key, trader_id text, thread_id bigint, tick int,
  event text check (event in ('open','counter','concede','hold','final','walk','deal','cooloff','lie_suspected')),
  our_price int, their_price int, step int, final bool, words_match_structure bool,
  source text check (source in ('ours','feed')));
create index if not exists trader_behaviors_trader on trader_behaviors (trader_id, tick);
create table if not exists learnings (
  id bigserial primary key, scope text check (scope in ('trader','card','market','duel','bench')),
  subject text, claim text, stats jsonb, support_n int, confidence numeric, created_tick int,
  superseded_by bigint references learnings(id));

-- Decisions and execution
create table if not exists intents (
  id bigserial primary key, source text, kind text, target jsonb, hard_limit int,
  deadline_tick int, status text default 'open', created_tick int);
create table if not exists decisions (
  id bigserial primary key, intent_id bigint references intents(id), thread_id bigint, tick int,
  state_digest text, rag_context jsonb, candidates jsonb, jev jsonb, jev_digest text,
  policy_checks jsonb, chosen jsonb,
  status text check (status in ('proposed','approved','rejected','claimed','done','failed','expired')),
  reason text);
create index if not exists decisions_queue on decisions (status, tick);
create table if not exists executions (
  id bigserial primary key, decision_id bigint references decisions(id), tick int, sdk_method text,
  request jsonb, response jsonb, error_code text);
create table if not exists outcomes (
  decision_id bigint primary key references decisions(id), realized_surplus numeric,
  ladder_share numeric, jev_right bool, recorded_tick int);

-- Duels as /api/duels shows them: the latest payload per duel (`bazaar duel run` each tick, the
-- finished one from `?done=true`). The evals read it instead of calling the game API.
create table if not exists duels (
  duel int primary key, session int, tick int, status text, role text, item text, your_limit int,
  rival text, deadline_tick int, rounds int, decay_per_round numeric, price int, days int,
  result numeric, payload jsonb, updated_at timestamptz default now());

-- The guardrail ledger shared by every process on every machine (taker, maker, duels, the CLI):
-- spend per game hour, accepts per tick, listings per tick. Append-only; a refund is a negative spend.
create table if not exists ledger (
  id bigserial primary key, kind text not null check (kind in ('spend','accept','listing')),
  tick int not null, t_hours double precision not null, price int not null default 0,
  item text not null default '', source text, created_at timestamptz default now());
create index if not exists ledger_kind_tick on ledger (kind, tick);
create index if not exists ledger_kind_hours on ledger (kind, t_hours);

-- Circuit breakers (breakers.py): a tripped scope makes guardrails.check() refuse that kind of write in every
-- process. Set by hand (`bazaar breaker trip|reset`) or by the live watchdog; read once per tick, fail open.
-- The same statement as breakers.DDL, which a writer runs before its first write.
create table if not exists guard_breakers (scope text primary key, tripped bool not null default false, reason text, tick int, at timestamptz not null default now(), until_tick int, source text);

-- Our venue's broker key (RULES.md "Your own market"), returned once by the opening: a SECRET like the team
-- key. Written and read only by `venue.KeyVault` (the maker on Railway, `bazaar venue open`); no public route,
-- view or eval reads this table. The vault runs the same statement before it writes (venue.VENUE_KEYS_DDL).
create table if not exists venue_broker_keys (target text not null, venue text not null, broker_key text not null, opened_tick int, created_at timestamptz not null default now(), primary key (target, venue));

-- Monitoring agent (bazaar monitor): the announcements and trader changes it saw.
create table if not exists alerts (
  id bigserial primary key, tick int, kind text, subject text, detail text,
  created_at timestamptz default now());

-- One row per alert even when several monitors (several laptops) raise the same one. The first
-- run removes the duplicates an older schema allowed, then adds the natural key.
do $$
begin
  if to_regclass(format('%I.alerts_natural_key', current_schema())) is null then
    delete from alerts a using alerts b
     where a.id > b.id and a.tick is not distinct from b.tick and a.kind is not distinct from b.kind
       and a.subject is not distinct from b.subject and a.detail is not distinct from b.detail;
    create unique index alerts_natural_key on alerts (tick, kind, subject, md5(detail));
  end if;
end $$;

-- Columns added after a table first shipped, and the pgvector columns. ALTER only what is missing:
-- a re-run takes no table lock. Turning pgvector on later and re-running `init` adds the embeddings.
do $$
declare
  vec text := (select format('%I.vector(384)', n.nspname) from pg_extension e
                 join pg_namespace n on n.oid = e.extnamespace where e.extname = 'vector');
  col record;
begin
  for col in
    select c.tbl, c.name, c.type from (values
      ('traders', 'status', 'text'),
      ('traders', 'updated_tick', 'int'),
      ('dealer_curves', 'ours', 'boolean'),  -- our own thread; null = written before we knew our team id
      ('decisions', 'agent', 'text'),  -- taker | maker: which autonomous agent proposed the move
      ('decisions', 'kind', 'text'),  -- accept_ask, dealer_bid, post_ask, cancel, ...
      ('decisions', 'dry_run', 'boolean'),
      ('ledger', 'slot', 'int'),  -- an accept's slot in its tick (1..accepts_per_team_per_tick)
      -- Evals (`bazaar evals run`): one row per settled duel, dealer thread, team trade or Market Test.
      ('outcomes', 'target', 'text'),  -- duel | dealer | trade | market_test
      ('outcomes', 'subject', 'text'),  -- duel:85, thread:101, settlement:67, market_test:sat
      ('outcomes', 'score', 'numeric'),  -- 0..1; null = settled but not scorable yet
      ('outcomes', 'label', 'text'),  -- good | ok | bad
      ('outcomes', 'explanation', 'text'),
      ('outcomes', 'details', 'jsonb'),
      ('outcomes', 'day', 'text'),  -- the game day (round) it settled in: fri | sat | sun
      ('outcomes', 'jev_question', 'text'),
      ('outcomes', 'jev_verdict', 'text'),
      ('outcomes', 'trace_id', 'text'),  -- the Phoenix span the score is attached to
      ('outcomes', 'span_id', 'text'),
      ('outcomes', 'annotated_at', 'timestamptz'),
      ('outcomes', 'annotation_tries', 'int'),
      ('outcomes', 'scored_at', 'timestamptz'),
      -- Our own dealer threads as the taker reads them (N12 part 3, `bazaar_agent.learn.threads`).
      ('threads', 'until_tick', 'int'),  -- a cooloff's end (from the thread's own answer)
      ('threads', 'updated_tick', 'int'),  -- the tick of the newest answer stored (a lagging writer never rolls back)
      ('messages', 'ours', 'boolean'),  -- our own message
      ('messages', 'tactic', 'text'),  -- which of our tactics sent it (N16), when known
      -- The live-feed reader (N12, `bazaar_agent.learn`): one structured fact per row, deduped by key.
      ('learnings', 'subject_kind', 'text'),  -- dealer | venue | team | organiser
      ('learnings', 'kind', 'text'),  -- blocker | cooloff | quota | sold_out | price_floor | behaviour | ...
      ('learnings', 'until_tick', 'int'),  -- expiry, exclusive (null = no expiry)
      ('learnings', 'team', 'text'),  -- whom it binds (null = everyone)
      ('learnings', 'evidence', 'bigint[]'),  -- feed event ids
      ('learnings', 'source', 'text'),  -- rules | llm
      ('learnings', 'dedupe_key', 'text'),
      ('learnings', 'updated_at', 'timestamptz'),
      -- The outcome learner (N3, `learn.lessons` / `learn.recall`): md5 of the claim last embedded, so an
      -- edited claim is embedded again; a per-move dedupe key so re-reading the feed adds no duplicate.
      ('learnings', 'embedded_hash', 'text'),
      ('trader_behaviors', 'dedupe_key', 'text'),
      ('messages', 'embedding', vec),
      ('trader_behaviors', 'embedding', vec),
      ('learnings', 'embedding', vec)) as c(tbl, name, type)
    where c.type is not null and not exists (
      select 1 from information_schema.columns i
       where i.table_schema = current_schema() and i.table_name = c.tbl and i.column_name = c.name)
  loop
    execute format('alter table %I add column %I %s', col.tbl, col.name, col.type);
  end loop;
end $$;

-- One row per learned fact, whoever read it first (the taker on Railway, a laptop's CLI).
create unique index if not exists learnings_dedupe_key on learnings (dedupe_key);
create index if not exists learnings_recall on learnings (subject_kind, subject, kind, until_tick);
create unique index if not exists trader_behaviors_dedupe_key on trader_behaviors (dedupe_key);

-- Cosine search for the hybrid recall (N3), only where pgvector made the embedding column.
do $$
begin
  if exists (select 1 from information_schema.columns where table_schema = current_schema()
              and table_name = 'learnings' and column_name = 'embedding')
     and to_regclass(format('%I.learnings_embedding_hnsw', current_schema())) is null then
    execute 'create index learnings_embedding_hnsw on learnings using hnsw (embedding vector_cosine_ops)';
  end if;
end $$;

-- One team accept per slot per tick, enforced by the database itself: two processes on two machines
-- can never both take the same slot (`ledger_pg.PgLedger.reserve_accept`).
create unique index if not exists ledger_accept_slot on ledger (tick, slot) where kind = 'accept' and slot is not null;

-- Competitor activity: every feed event our team neither did nor is party to (`intel.is_ours`).
-- "Us" is the trader row with status 'us' (written by the monitor); feed_events keeps everything.
do $$
begin
  if to_regclass(format('%I.their_events', current_schema())) is null then
    create view their_events as
      select e.* from feed_events e
       where not exists (
         select 1 from traders u
          where u.status = 'us'
            and (u.id in (e.actor, e.payload->>'team', e.payload->>'with', e.payload->>'owner',
                          e.payload->>'sender', e.payload->'offer'->>'maker')
                 or coalesce(e.payload->'parties', '[]'::jsonb) ? u.id));
  end if;
end $$;

-- Evals: an outcome is keyed by what it scores (target, subject), not by a decision, since duels and
-- dealer threads have no decisions row. The first run drops the old decision_id primary key (the table
-- was empty until the evals shipped); decision_id stays as a nullable link.
do $$
declare
  pk text := (select c.conname from pg_constraint c
               where c.conrelid = format('%I.outcomes', current_schema())::regclass and c.contype = 'p');
begin
  if pk is not null then
    execute format('alter table outcomes drop constraint %I', pk);
    alter table outcomes alter column decision_id drop not null;
  end if;
end $$;
create unique index if not exists outcomes_subject on outcomes (target, subject);

-- What the dashboard and `bazaar evals report` read: per target and game day.
create or replace view eval_scorecard as
  select target, coalesce(day, '?') as day, count(*) as outcomes, count(score) as scored,
         round(avg(score), 3) as mean_score, min(score) as worst_score,
         count(*) filter (where label = 'good') as good, count(*) filter (where label = 'ok') as ok,
         count(*) filter (where label = 'bad') as bad, round(sum(realized_surplus), 1) as surplus,
         (array_agg(subject order by score asc nulls last, subject))[1:5] as worst, max(recorded_tick) as last_tick
    from outcomes where target is not null group by target, coalesce(day, '?');

-- The dealer ladder as RULES.md scores it: per level, the best three shares (a missing one is zero).
create or replace view eval_ladder as
  with ranked as (
    select (details->>'level')::int as level, details->>'dealer' as dealer, subject,
           details->>'fill_price' is not null as deal, coalesce(score, 0) as share,
           row_number() over (partition by (details->>'level')::int order by coalesce(score, 0) desc, subject) as rank
      from outcomes where target = 'dealer' and details->>'level' is not null)
  select level, string_agg(distinct dealer, ', ') as dealers, count(*) as threads,
         count(*) filter (where deal) as deals,
         round(coalesce(sum(share) filter (where rank <= 3), 0) / 3, 3) as best3_share,
         array_agg(subject order by rank) filter (where rank <= 3) as best3
    from ranked group by level;

-- Jev calibration: was each decided verdict right once its decision settled?
create or replace view eval_jev_calibration as
  select jev_question as question, count(*) as outcomes,
         count(*) filter (where jev_verdict in ('yes', 'no')) as decided,
         count(*) filter (where jev_right) as n_right, count(*) filter (where not jev_right) as n_wrong,
         count(*) filter (where jev_right is null) as n_unknown
    from outcomes where jev_question is not null group by jev_question;

-- Card catalog and our holdings (catalog_db.py, holdings.py). `cards` above is the catalog: every card
-- of every set from the public /api/catalog, rewritten when a set is released and at most every few ticks
-- (`minted` grows as packs open). The columns it lacked when it shipped empty:
do $$
declare
  col record;
begin
  for col in
    select c.name, c.type from (values
      ('set_name', 'text'), ('page', 'boolean'), ('hidden', 'boolean'), ('flavour', 'text')) as c(name, type)
    where not exists (
      select 1 from information_schema.columns i
       where i.table_schema = current_schema() and i.table_name = 'cards' and i.column_name = c.name)
  loop
    execute format('alter table cards add column %I %s', col.name, col.type);
  end loop;
end $$;
create index if not exists cards_set on cards (set_code, rarity);

-- Our /api/me as our processes read it: one row per team and game tick (the server's tick in /me). A
-- row is a decision input only while it is provably current (holdings.py: same tick, same epoch, young,
-- no thread message of ours this tick); otherwise the reader calls /api/me and upserts the newer view.
-- `world` is "real" or "sim:<host:port>": a simulator's tick and team ids (sim-team1 is t01 too) never
-- answer for the real game, even in a shared database. A cache: a pre-release copy without `world` is
-- dropped and recreated (every reader then reads /me live once).
do $$
begin
  if to_regclass(format('%I.me_snapshots', current_schema())) is not null and not exists (
      select 1 from information_schema.columns
       where table_schema = current_schema() and table_name = 'me_snapshots' and column_name = 'world') then
    drop table me_snapshots;
  end if;
end $$;
create table if not exists me_snapshots (
  world text not null, team text not null, tick int not null, epoch bigint not null, digest text not null,
  read_at timestamptz not null, read_by text not null,
  cash int, level int, cards jsonb, duplicates jsonb, packs jsonb, pages jsonb, affinity jsonb,
  score jsonb, me jsonb not null,
  primary key (world, team, tick));
-- The version every state-changing send of ours bumps, before and after it goes (`sdk.TrackedBazaar`):
-- a snapshot read under an older epoch is stale. One scope per world (every team of that world): a send
-- may only ever invalidate more, never less. `thread_message_at` is our last thread message: a dealer
-- may still answer and accept it, so a snapshot of that tick is not trusted.
create table if not exists holdings_state (
  scope text primary key, epoch bigint not null default 0, written_at timestamptz,
  thread_message_at timestamptz, last_write text, last_writer text);

-- Supply map (N14b, `bazaar supply`): the card scan (`GET /api/cards/{id}`: ids 1-270 are the starting
-- hands, block k = team k; newer ids are pack pulls and dealer mints), and per card and per set who holds
-- what and how many complete pages can exist. The agents read `supply_assets` back for valuation.
create table if not exists supply_assets (
  id int primary key, ref text, kind text, scanned jsonb, scanned_tick int);
create table if not exists supply_cards (
  ref text primary key, set_code text, rarity text, page bool, minted int, print_run int, ours int,
  holders jsonb, others int, unplaced int, updated_tick int);
create table if not exists supply_sets (
  set_code text primary key, released bool, pages_possible int, bottleneck jsonb, our_have int,
  page_cards int, packs_opened int, updated_tick int);

-- Buyer ranking (`bazaar buyers --save`): per card, every other team as a buyer, best first (`position`),
-- with the reasons (`buyers.rank_buyers`). A save replaces the rows of the cards it ranks.
create table if not exists team_buyer_rank (
  card text not null, team text not null, position int, rank int, willing numeric, interest numeric,
  missing bool, expected numeric, rival text, blocked bool, why text, updated_tick int,
  primary key (card, team));

-- The public leaderboard, one row per team per news-sentinel window (`leaderboard_store.py`): the rank watch
-- reloads its history from here after a restart. `world`: "real" or "sim:<host:port>", as `me_snapshots`.
create table if not exists leaderboard_snapshots (
  world text not null, tick int not null, team text not null, rank int not null, score numeric,
  negotiating numeric, market numeric, level int, pages int, deals int, venue text,
  read_at timestamptz not null default now(),
  primary key (world, tick, team));

-- Prompt-injection attempts (`injection_log.py`): every counterparty text whose words carry an injection shape
-- (`llm.chooser.injection_flags`), with the RAW text verbatim as the proof (our own secrets scrubbed only) and
-- the ids that let anyone check it against the game (`proof`: the feed event, thread or duel endpoint). A record,
-- never a report: nothing here is sent to the game. `severity` 'attempt' needs a strong shape (an override, a
-- role tag or role play, an asset grab, hidden unicode); 'weak' is JSON, a URL or a priced verb alone. Ids that do
-- not apply are 0, so the natural key dedupes. The same statement as injection_log.DDL.
create table if not exists injection_attempts (id bigserial primary key, world text not null default 'real', tick int, source text not null check (source in ('feed','team_thread','duel','dealer_thread','offer_text')), event_id bigint not null default 0, thread_id bigint not null default 0, duel_id bigint not null default 0, message_id bigint not null default 0, from_team text, to_us bool not null default false, tags text[] not null, severity text not null check (severity in ('attempt','weak')), raw text not null, normalised text not null, our_response text not null, proof text not null, seen_at timestamptz not null default now(), unique (world, source, event_id, thread_id, duel_id, message_id, tags));
