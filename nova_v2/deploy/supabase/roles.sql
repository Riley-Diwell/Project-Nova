-- Vendored from supabase/supabase docker/volumes/db/roles.sql (master, 2026-09-29).
-- Sets the service roles' passwords to POSTGRES_PASSWORD on first init.
-- supabase_functions_admin dropped: it comes from webhooks.sql, which this stack leaves out.
-- NOTE: change to your own passwords for production environments
\set pgpass `echo "$POSTGRES_PASSWORD"`

ALTER USER authenticator WITH PASSWORD :'pgpass';
ALTER USER pgbouncer WITH PASSWORD :'pgpass';
ALTER USER supabase_auth_admin WITH PASSWORD :'pgpass';
ALTER USER supabase_storage_admin WITH PASSWORD :'pgpass';
