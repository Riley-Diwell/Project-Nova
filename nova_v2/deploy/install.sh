#!/usr/bin/env bash
# Brings the stack up and makes sure the database has NOVA's schema.
# Safe to re-run: every step is idempotent.
set -euo pipefail
cd "$(dirname "$0")"

[ -f .env ] || ./gen-keys.sh
mkdir -p data/postgres data/backups

echo "== database and sign-in"
docker compose up -d db auth rest
until [ "$(docker inspect -f '{{.State.Health.Status}}' "$(docker compose ps -q auth)")" = healthy ]; do
  sleep 2
done

echo "== NOVA schema (server/db/schema.sql)"
# As postgres, so Supabase's default privileges grant the new tables to
# service_role, which is who the server's requests run as.
docker compose exec -T db psql -v ON_ERROR_STOP=1 -q -U postgres -d postgres -f /nova-schema/schema.sql
docker compose exec -T db psql -q -U postgres -d postgres -c "notify pgrst, 'reload schema'"

echo "== everything else"
docker compose up -d --build

echo "== publishing to the tailnet"
tailscale serve --bg --https=443 http://127.0.0.1:8088
tailscale serve status

echo
echo "Up. Qwen takes a few minutes to load on first start: docker compose logs -f qwen"
