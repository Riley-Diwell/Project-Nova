#!/usr/bin/env bash
# A compressed dump of the whole database into data/backups, keeping the newest 14.
# Nightly, from the owner's crontab:
#     15 3 * * * /srv/nova/backup.sh >> /srv/nova/data/backups/backup.log 2>&1
set -euo pipefail
cd "$(dirname "$0")"

# Never at the same time as scrub-backups.sh: it rewrites dumps in this folder.
exec 9>data/backups/.scrub.lock
flock 9

# Deleted notes leave the existing dumps first (scrub-backups.sh). A failure
# there doesn't stop tonight's backup; the journal waits for the next scrub.
bash scrub-backups.sh --now --locked || echo "$(date -Is) scrub failed; backing up anyway"

name="nova-$(date +%Y%m%d-%H%M).dump"
docker compose exec -T db pg_dump -U supabase_admin -d postgres -Fc -f "/backups/$name"
echo "$(date -Is) wrote data/backups/$name"
# Newest 14 by name, which carries the date - not by modification time, which
# scrub-backups.sh keeps but shouldn't have to be trusted with.
ls -1 data/backups/nova-*.dump | sort -r | tail -n +15 | xargs -r rm --
