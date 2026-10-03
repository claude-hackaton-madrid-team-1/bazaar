-- Read-only login for teammates (DataGrip): SELECT on every table of one schema, nothing else.
--
-- Idempotent: re-running it re-asserts every privilege and replaces the password (a rotation).
-- Applied by `uv run bazaar db readonly-user` with the admin DATABASE_URL, never by hand: the
-- placeholders are filled by psycopg (`{role}`/`{schema}` identifiers, `{role_name}` a literal),
-- and the password arrives already hashed (SCRAM verifier, computed client-side) through the
-- transaction-local setting `bazaar.readonly_password`, so the plaintext never reaches the server.
--
-- Default privileges cover tables created later by the role running this file (the admin role,
-- which is the one every bazaar process writes with). Secret tables are revoked at the end; the
-- caller creates them first, so a default privilege never re-grants one later.

do $$
begin
    if not exists (select 1 from pg_roles where rolname = {role_name}) then
        create role {role} login;
    end if;
end
$$;

alter role {role} with login nosuperuser nocreatedb nocreaterole noinherit noreplication nobypassrls
    connection limit 10;

do $$
begin
    execute format('alter role %I password %L', {role_name}, current_setting('bazaar.readonly_password'));
end
$$;

alter role {role} set default_transaction_read_only = on;
alter role {role} set statement_timeout = '30s';
-- A DataGrip tab left open in a transaction holds locks our writers' DDL waits on.
alter role {role} set idle_in_transaction_session_timeout = '60s';
alter role {role} set idle_session_timeout = '10min';

do $$
begin
    execute format('revoke all on database %I from %I', current_database(), {role_name});
    execute format('grant connect on database %I to %I', current_database(), {role_name});
end
$$;

revoke all on schema {schema} from {role};
grant usage on schema {schema} to {role};

revoke all on all tables in schema {schema} from {role};
revoke all on all sequences in schema {schema} from {role};
grant select on all tables in schema {schema} to {role};
grant select on all sequences in schema {schema} to {role};

alter default privileges in schema {schema} revoke all on tables from {role};
alter default privileges in schema {schema} revoke all on sequences from {role};
alter default privileges in schema {schema} grant select on tables to {role};
alter default privileges in schema {schema} grant select on sequences to {role};

-- Secrets: our venue broker key acts as our venue (schema.sql). Never readable by this role.
revoke all on table {schema}.venue_broker_keys from {role};
