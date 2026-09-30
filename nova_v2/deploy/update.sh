#!/usr/bin/env bash
# After new server code has been copied into ./server: apply any schema
# additions, rebuild the API image and restart it. The model and database
# keep running.
set -euo pipefail
cd "$(dirname "$0")"

docker compose exec -T db psql -v ON_ERROR_STOP=1 -q -U postgres -d postgres -f /nova-schema/schema.sql
docker compose exec -T db psql -q -U postgres -d postgres -c "notify pgrst, 'reload schema'"
docker compose up -d --build nova
docker compose ps
