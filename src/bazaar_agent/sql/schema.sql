-- Bazaar memory schema (spec .ai/specs/01-spec.md §5). Idempotent: safe to re-run.
-- All ticks are game ticks. Embeddings are 384-d (local fastembed model, Phase 1).
create extension if not exists vector;

-- Reference data
create table if not exists cards (
  id text primary key, set_code text, name text, rarity text, book numeric,
  print_run int, minted int, released bool, updated_tick int);
create table if not exists traders (
  id text primary key, kind text check (kind in ('dealer','team','bench','rival_alias')),
  name text, level int, traits jsonb, menu jsonb, unlock jsonb,
  first_seen_tick int, last_seen_tick int);

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
  offer jsonb, final bool, embedding vector(384));
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
  source text check (source in ('ours','feed')), embedding vector(384));
create index if not exists trader_behaviors_trader on trader_behaviors (trader_id, tick);
create table if not exists learnings (
  id bigserial primary key, scope text check (scope in ('trader','card','market','duel','bench')),
  subject text, claim text, stats jsonb, support_n int, confidence numeric, created_tick int,
  superseded_by bigint references learnings(id), embedding vector(384));

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
