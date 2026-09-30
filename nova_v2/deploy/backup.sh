#!/usr/bin/env bash
# A compressed dump of the whole database into data/backups, keeping the newest 14.
# Nightly, from the owner's crontab:
#     15 3 * * * /srv/nova/backup.sh >> /srv/nova/data/backups/backup.log 2>&1
set -euo pipefail
cd "$(dirname "$0")"

name="nova-$(date +%Y%m%d-%H%M).dump"
docker compose exec -T db pg_dump -U supabase_admin -d postgres -Fc -f "/backups/$name"
echo "$(date -Is) wrote data/backups/$name"
ls -1t data/backups/nova-*.dump | tail -n +15 | xargs -r rm --
