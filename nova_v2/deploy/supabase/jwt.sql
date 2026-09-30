-- Vendored from supabase/supabase docker/volumes/db/jwt.sql (master, 2026-09-29).
\set jwt_exp `echo "$JWT_EXP"`

ALTER DATABASE postgres SET "app.settings.jwt_exp" TO :'jwt_exp';
