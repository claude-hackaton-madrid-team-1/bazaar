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
