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

-- Other teams' set multipliers (AF1, `team_affinity.py`): what a team SAID in a team thread (untrusted words,
-- parsed; `quote` is their scrubbed message, at most 200 characters) and what we INFERRED from the feed
-- (`affinity.affinity_map`: the likeliest multiplier and its probability). One row per team, set and source.
create table if not exists team_affinity (
  team text not null, set_code text not null, multiplier numeric not null,
  source text not null check (source in ('said','inferred')), confidence numeric, tick int not null,
  thread_id bigint, quote text check (char_length(quote) <= 200), updated_at timestamptz not null default now(),
  primary key (team, set_code, source));
-- DataGrip and bazaar-live: per team and set, the stated multiplier beside the inferred one.
create or replace view team_affinity_board as
  select coalesce(s.team, i.team) as team, coalesce(s.set_code, i.set_code) as set_code,
         s.multiplier as said, s.confidence as said_confidence, s.tick as said_tick, s.thread_id, s.quote,
         i.multiplier as inferred, i.confidence as inferred_confidence, i.tick as inferred_tick
  from (select * from team_affinity where source = 'said') s
  full join (select * from team_affinity where source = 'inferred') i
    on i.team = s.team and i.set_code = s.set_code;

-- Rival board (bazaar-live's Rivals tab, DataGrip): one row per OTHER team, read-only, from what we already store:
-- the leaderboard history, our latest /me, the catalog, the board (feed `offer.listed`, `offer.cancelled`), the tape,
-- competitor_profiles and the rank watch's `rival_move` learnings. "They want": cards a team bid cash for (or asked for
-- in a swap) in the last 60 feed ticks and has not bought since; "they have": copies it asked cash for (or gave in a
-- swap) in that window and has not sold since. A price comes only from a listing still live (not lapsed, not cancelled,
-- open to anyone or to us). Values are estimates: a page card we miss is worth book × our set multiplier to us, one of
-- our spare copies its `your_value`; a card a team wants is worth book × the highest multiplier to it (every team has
-- the same six multipliers, shuffled; page bonuses are unknown), a copy it lists nothing. A sale or a buy pays the fee
-- cap (10 %, at most 5 P a card); a swap none. The move never gives value away (our gain > 0) and never helps a guarded
-- team (top 5, within 3 ranks of us, or any team while our rank is unknown) unless our gain is at least twice theirs.
-- Every feed field is hostile: shapes are checked, refs must be catalog cards, cash outside [0, 100000) drops a listing.
--
-- Replaced only when this version is newer than the one stored in the view's comment, with a short lock wait, and never
-- fatally: an older checkout, bazaar-live's dependent show view blocking a column change, or a reader holding the view
-- only leaves a warning and the previous definition, and init_schema (the ledger's start) goes on. Bump `board_version` with
-- every change; new columns go last (`create or replace view` only appends).
do $do$
declare
  board_version constant int := 1;
  stored int := coalesce(substring(obj_description(to_regclass(format('%I.rival_board', current_schema())), 'pg_class')
                                   from '^rival_board v(\d+)$')::int, 0);
begin
  if stored >= board_version then
    return;
  end if;
  begin
    perform set_config('lock_timeout', '2s', true);
    execute $view$
create or replace view rival_board as
with lb as (
  select max(tick) as tick from leaderboard_snapshots where world = 'real'
), board as (
  select l.* from leaderboard_snapshots l join lb on l.tick = lb.tick where l.world = 'real'
), me as (
  select s.team, s.cards, s.affinity from me_snapshots s where s.world = 'real' order by s.tick desc, s.read_at desc limit 1
), our_id as (
  select coalesce((select team from me), (select t.id from traders t where t.status = 'us' order by t.id limit 1)) as team
), us as (
  select b.* from board b join our_id o on o.team = b.team
), top_mult as (
  -- the highest set multiplier: what a card is worth at most to the team that chases its set
  select greatest(coalesce(max(case when jsonb_typeof(e.v) = 'number' then (e.v #>> '{}')::numeric end), 1), 1) as m
    from me cross join lateral jsonb_each(case when jsonb_typeof(me.affinity) = 'object' then me.affinity else '{}'::jsonb end) e(k, v)
), trend as (
  select l.team, jsonb_agg(jsonb_build_object('tick', l.tick, 'rank', l.rank, 'score', l.score) order by l.tick) as points,
         (array_agg(l.tick order by l.tick))[1] as first_tick, (array_agg(l.rank order by l.tick))[1] as first_rank,
         (array_agg(l.score order by l.tick))[1] as first_score
    from leaderboard_snapshots l join lb on l.tick between lb.tick - 60 and lb.tick
   where l.world = 'real'
   group by l.team
), now_tick as (
  select coalesce((select max(f.tick) from feed_events f), (select tick from lb)) as tick
), cancelled as (
  select distinct case when jsonb_typeof(c.payload -> 'offer') = 'number' then (c.payload ->> 'offer')::numeric end as offer_id
    from feed_events c cross join now_tick n
   where c.type = 'offer.cancelled' and c.tick >= n.tick - 60
), listing as (
  select e.id, e.tick, o ->> 'maker' as team,
         case when jsonb_typeof(o -> 'id') = 'number' then (o ->> 'id')::numeric end as offer_id,
         case when jsonb_typeof(o -> 'expires_tick') = 'number' then (o ->> 'expires_tick')::numeric end as expires_tick,
         case when jsonb_typeof(o -> 'to') = 'string' then o ->> 'to' end as to_team,
         case when jsonb_typeof(o -> 'give' -> 'assets') = 'array' then o -> 'give' -> 'assets' else '[]'::jsonb end as gives,
         case when jsonb_typeof(o -> 'want' -> 'types') = 'array' then o -> 'want' -> 'types' else '[]'::jsonb end as wants,
         case when jsonb_typeof(o -> 'give' -> 'cash') = 'number' then (o -> 'give' ->> 'cash')::numeric end as give_cash,
         case when jsonb_typeof(o -> 'want' -> 'cash') = 'number' then (o -> 'want' ->> 'cash')::numeric end as want_cash
    from feed_events e
   cross join lateral (select e.payload -> 'offer' as o) x
   where e.type = 'offer.listed' and jsonb_typeof(x.o) = 'object' and e.tick >= (select tick from now_tick) - 60
), listed as (
  -- an absurd amount of cash on either side makes the whole listing noise; `live`: its price can still be taken by us
  select l.*,
         (l.expires_tick is null or l.expires_tick > (select tick from now_tick))
         and (l.to_team is null or l.to_team = (select team from our_id))
         and not exists (select 1 from cancelled c where c.offer_id = l.offer_id) as live
    from listing l
   where coalesce(l.give_cash, 0) >= 0 and coalesce(l.give_cash, 0) < 100000
     and coalesce(l.want_cash, 0) >= 0 and coalesce(l.want_cash, 0) < 100000
), wanted as (
  -- a bid (cash for card:X) or a swap (a copy for card:X); the price is the latest live cash bid for X
  select l.team, substr(t.ref, 6) as ref, max(l.tick) as tick,
         (array_agg(l.give_cash order by l.tick desc, l.id desc)
            filter (where l.live and jsonb_array_length(l.gives) = 0 and jsonb_array_length(l.wants) = 1
                      and l.give_cash > 0))[1] as price
    from listed l cross join lateral jsonb_array_elements_text(l.wants) t(ref)
   where t.ref like 'card:%'
   group by l.team, substr(t.ref, 6)
), wants_open as (
  select w.* from wanted w join cards k on k.id = w.ref
   where not exists (select 1 from tape t where t.buyer = w.team and t.card_id = w.ref and t.tick >= w.tick)
), offered as (
  -- an ask (a copy for cash) or a swap (a copy for card:Y); the price is the latest live cash ask for that card
  select l.team, a ->> 'ref' as ref, max(l.tick) as tick,
         (array_agg(l.want_cash order by l.tick desc, l.id desc)
            filter (where l.live and jsonb_array_length(l.gives) = 1 and jsonb_array_length(l.wants) = 0
                      and l.want_cash > 0))[1] as price
    from listed l cross join lateral jsonb_array_elements(l.gives) a
   where jsonb_typeof(a) = 'object'
   group by l.team, a ->> 'ref'
), haves_open as (
  select h.* from offered h join cards k on k.id = h.ref
   where not exists (select 1 from tape t where t.seller = h.team and t.card_id = h.ref and t.tick >= h.tick)
), ours as (
  select c ->> 'ref' as ref, count(*) as copies,
         min(case when jsonb_typeof(c -> 'your_value') = 'number' then (c ->> 'your_value')::numeric end) as value
    from me cross join lateral jsonb_array_elements(case when jsonb_typeof(me.cards) = 'array' then me.cards else '[]'::jsonb end) c
   where jsonb_typeof(c) = 'object' and c ->> 'ref' is not null
   group by c ->> 'ref'
), missing as (
  -- page cards of a released set we hold no copy of, at book × our multiplier for the set
  select k.id as ref,
         round(k.book * coalesce(case when jsonb_typeof(me.affinity -> k.set_code) = 'number'
                                      then (me.affinity ->> k.set_code)::numeric end, 1), 1) as value
    from cards k cross join me
   where k.page and k.released and k.book is not null and not exists (select 1 from ours o where o.ref = k.id)
), spare_matches as (
  select w.team, w.ref, (o.copies - 1)::int as spare, w.price as their_price, o.value as our_value,
         k.book * (select m from top_mult) as their_value, w.tick
    from wants_open w join ours o on o.ref = w.ref and o.copies >= 2 join cards k on k.id = w.ref
), copy_matches as (
  select h.team, h.ref, h.price as their_price, m.value as value_to_us, h.tick
    from haves_open h join missing m on m.ref = h.ref
), moves as (
  select s.team, 'swap' as kind, s.ref as give, c.ref as get, null::numeric as price,
         round(c.value_to_us - coalesce(s.our_value, 0), 1) as our_gain, round(s.their_value, 1) as their_gain
    from spare_matches s join copy_matches c on c.team = s.team
  union all
  select s.team, 'sell', s.ref, null, s.their_price,
         round(s.their_price - least(s.their_price * 0.1, 5) - coalesce(s.our_value, 0), 1),
         round(s.their_value - s.their_price, 1)
    from spare_matches s where s.their_price is not null
  union all
  select c.team, 'buy', null, c.ref, c.their_price,
         round(c.value_to_us - c.their_price - least(c.their_price * 0.1, 5), 1), round(c.their_price, 1)
    from copy_matches c where c.their_price is not null
), guard as (
  select b.team,
         case when b.rank <= 5 then 'top5'
              when (select u.rank from us u) is null or abs(b.rank - (select u.rank from us u)) <= 3 then 'near' end as reason
    from board b
), best as (
  select distinct on (m.team) m.*
    from moves m join guard g on g.team = m.team
   where m.our_gain > 0 and (g.reason is null or m.our_gain >= 2 * m.their_gain)
   order by m.team, m.our_gain desc, m.their_gain, m.kind, m.give, m.get
), dealer_deals as (
  select team, count(*)::int as n
    from (select buyer as team from tape where coalesce(persona, '') <> ''
          union all select seller from tape where coalesce(persona, '') <> '') d
   group by team
), venue_trades as (
  select venue, count(*)::int as n from tape where coalesce(venue, '') <> '' group by venue
), climbs as (
  select distinct on (l.subject) l.subject as team, l.claim, l.created_tick
    from learnings l
   where l.kind = 'rival_move' and l.subject_kind = 'team' and coalesce(l.source, 'rules') <> 'llm'
   order by l.subject, l.created_tick desc nulls last, l.id desc
)
select b.team, b.tick, b.rank, b.score, b.negotiating, b.market, b.level, b.pages, b.deals, b.venue,
       t.first_rank - b.rank as rank_change, b.score - t.first_score as score_change, b.tick - t.first_tick as trend_ticks,
       coalesce(t.points, '[]'::jsonb) as trend,
       o.team as our_team, u.rank as our_rank, u.score as our_score, u.negotiating as our_negotiating,
       u.market as our_market, u.pages as our_pages,
       coalesce(dd.n, 0) as dealer_deals, coalesce(vt.n, 0) as venue_trades,
       case when p.notes ->> 'top_set' ~ '^[A-Z]{3}$' then p.notes ->> 'top_set' end as top_set,
       coalesce((select jsonb_object_agg(e.k, e.v)
                   from jsonb_each(case when jsonb_typeof(p.set_interest) = 'object' then p.set_interest else '{}'::jsonb end) e(k, v)
                  where jsonb_typeof(e.v) = 'number' and e.k ~ '^[A-Z]{3}$'), '{}'::jsonb) as set_interest,
       array_remove(array[
         case when b.negotiating > u.negotiating then 'negotiating' end,
         case when b.market > u.market then 'market' end,
         case when b.pages > u.pages then 'pages' end,
         case when coalesce(dd.n, 0) > coalesce(ud.n, 0) then 'dealer_ladder' end,
         case when coalesce(vt.n, 0) > coalesce(uv.n, 0) then 'venue' end,
         case when t.first_rank - b.rank >= 2 then 'climbing' end], null) as strengths,
       array_remove(array[
         case when b.negotiating < u.negotiating then 'negotiating' end,
         case when b.market < u.market then 'market' end,
         case when b.pages < u.pages then 'pages' end,
         case when coalesce(vt.n, 0) = 0 then 'no_venue_trades' end,
         case when t.first_rank - b.rank <= -2 then 'falling' end,
         case when exists (select 1 from wants_open w where w.team = b.team) then 'needs_cards' end], null) as weaknesses,
       coalesce((select jsonb_agg(jsonb_build_object('ref', w.ref, 'price', w.price, 'tick', w.tick) order by w.tick desc, w.ref)
                   from (select * from wants_open w where w.team = b.team order by w.tick desc, w.ref limit 20) w), '[]'::jsonb) as they_want,
       coalesce((select jsonb_agg(jsonb_build_object('ref', h.ref, 'price', h.price, 'tick', h.tick) order by h.tick desc, h.ref)
                   from (select * from haves_open h where h.team = b.team order by h.tick desc, h.ref limit 20) h), '[]'::jsonb) as they_have,
       coalesce((select jsonb_agg(jsonb_build_object('ref', s.ref, 'spare', s.spare, 'their_price', s.their_price, 'our_value', s.our_value)
                                  order by s.tick desc, s.ref)
                   from (select * from spare_matches s where s.team = b.team order by s.tick desc, s.ref limit 20) s), '[]'::jsonb) as we_have_for_them,
       coalesce((select jsonb_agg(jsonb_build_object('ref', c.ref, 'their_price', c.their_price, 'value_to_us', c.value_to_us)
                                  order by c.tick desc, c.ref)
                   from (select * from copy_matches c where c.team = b.team order by c.tick desc, c.ref limit 20) c), '[]'::jsonb) as they_have_for_us,
       ((select count(*) from spare_matches s where s.team = b.team) + (select count(*) from copy_matches c where c.team = b.team))::int as match_count,
       g.reason is not null as guarded, g.reason as guard_reason,
       coalesce(m.kind, case when g.reason is not null then 'hold' else 'watch' end) as move_kind,
       m.give as move_give, m.get as move_get, round(m.price)::int as move_price, m.our_gain, m.their_gain,
       case m.kind
         when 'swap' then format('offer our spare %s for their %s (+%s for us, +%s for them)', m.give, m.get, m.our_gain, m.their_gain)
         when 'sell' then format('sell our spare %s into their bid of %s (+%s for us after the fee)', m.give, m.price, m.our_gain)
         when 'buy' then format('buy their %s at their ask of %s (+%s for us after the fee)', m.get, m.price, m.our_gain)
         else case
                when g.reason = 'top5' then format('don''t trade: top-5 rival (rank %s)', b.rank)
                when g.reason = 'near' and u.rank is null then format('don''t trade: our rank is unknown (theirs %s)', b.rank)
                when g.reason = 'near' then format('don''t trade: within 3 ranks of us (rank %s, ours %s)', b.rank, u.rank)
                else 'nothing to trade yet: watch their bids' end
       end as suggested_move,
       cl.claim as why_climbed, cl.created_tick as why_climbed_tick
  from board b
  cross join our_id o
  left join us u on true
  left join trend t on t.team = b.team
  left join competitor_profiles p on p.team = b.team
  left join guard g on g.team = b.team
  left join best m on m.team = b.team
  left join dealer_deals dd on dd.team = b.team
  left join dealer_deals ud on ud.team = u.team
  left join venue_trades vt on vt.venue = b.venue
  left join venue_trades uv on uv.venue = u.venue
  left join climbs cl on cl.team = b.team
 where b.team is distinct from o.team
    $view$;
    execute format('comment on view rival_board is %L', 'rival_board v' || board_version);
    -- the teammates' read-only login (`bazaar db readonly-user`) reads it like every table of the schema
    if exists (select from pg_roles where rolname = 'bazaar_team_ro') then
      execute format('grant select on %I.rival_board to bazaar_team_ro', current_schema());
    end if;
    perform set_config('lock_timeout', '15s', true);
  exception when others then
    raise warning 'rival_board v% not applied (%): the previous definition stays', board_version, sqlerrm;
  end;
end $do$;
