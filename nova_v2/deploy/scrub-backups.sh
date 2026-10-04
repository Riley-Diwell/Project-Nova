#!/usr/bin/env bash
# Deleted notes leave the backups too (docs/plans/notes-hard-delete-plan.md phase 4).
#
# Every hard delete writes the ids it removed to public.deletion_journal. This
# removes the same rows from every dump in data/backups, then empties the
# journal. Each dump is restored into `scrub-db` (a throwaway copy of `db`
# whose data lives in RAM), cleaned, dumped back out, checked, and only then
# put in place of the original - with the original's timestamp, so
# backup.sh's rotation still sees it as the age it is. If anything about a
# dump looks wrong, the original is kept, the journal is kept, and the next
# run tries again.
#
#   bash scrub-backups.sh              scrub if a delete has waited 5 minutes (cron)
#   bash scrub-backups.sh --now        scrub whatever is journalled, now
#   bash scrub-backups.sh --self-test  prove it works on made-up data, touching
#                                      neither the live database nor any backup
#
# From the owner's crontab (docker group, no sudo):
#     */5 * * * * bash /srv/nova/scrub-backups.sh >> /srv/nova/data/backups/scrub.log 2>&1
#
# Logs ids and counts only - never what was deleted.
set -euo pipefail
cd "$(dirname "$0")"

DEBOUNCE="5 minutes"   # so deleting ten notes is one scrub, not ten
LOCK=data/backups/.scrub.lock
PSQL_LIVE=(docker compose exec -T db psql -v ON_ERROR_STOP=1 -X -q -U supabase_admin -d postgres)
PSQL_SCRATCH=(docker compose exec -T scrub-db psql -v ON_ERROR_STOP=1 -X -q -U supabase_admin -d postgres)

log() { echo "$(date -Is) scrub: $*"; }
die() { log "ERROR: $*"; exit 1; }

mode="cron"
locked=0
for arg in "$@"; do
    case "$arg" in
        --now) mode="now" ;;
        --self-test) mode="self-test" ;;
        --locked) locked=1 ;;   # backup.sh already holds the lock
        *) die "unknown option $arg" ;;
    esac
done

# One scrub at a time, and never while backup.sh is writing a dump.
if [ "$locked" = 0 ]; then
    exec 9>"$LOCK"
    if ! flock -n 9; then
        [ "$mode" = cron ] && exit 0
        log "waiting for another scrub or backup to finish"
        flock 9
    fi
fi

# --- the scratch database ------------------------------------------------------

scratch_up() {
    # Recreated every time: a fresh, empty copy of the image, nothing left over.
    trap scratch_down EXIT
    docker compose --profile scrub rm -sfv scrub-db >/dev/null 2>&1 || true
    docker compose --profile scrub up -d scrub-db >/dev/null
    for _ in $(seq 1 90); do
        if docker compose exec -T scrub-db pg_isready -U postgres -h localhost >/dev/null 2>&1; then
            # The image restarts Postgres once after its init scripts; let that settle.
            sleep 2
            docker compose exec -T scrub-db pg_isready -U postgres -h localhost >/dev/null 2>&1 && return 0
        fi
        sleep 2
    done
    die "scrub-db didn't start"
}

scratch_down() {
    docker compose --profile scrub rm -sfv scrub-db >/dev/null 2>&1 || true
}

# The table of contents of a dump, without the ids and OIDs that differ between
# two dumps of the same thing.
toc() {
    docker compose exec -T scrub-db pg_restore -l "$1" \
        | grep -v '^;' | sed -E 's/^[0-9]+; [0-9]+ [0-9]+ //' | sort
}

# Removes the journalled rows from one dump. $1: path inside the containers
# (/backups/<name>). $JOURNAL and $OVERWRITES: COPY-format rows, see below.
# Returns non-zero, leaving the original untouched, if anything is off.
scrub_dump() {
    local dump="$1" name tmp errors
    name="$(basename "$dump")"
    tmp="/backups/.scrub-$name"

    scratch_up
    # The same restore the README tells people to do, into the empty copy.
    errors="$(docker compose exec -T scrub-db pg_restore -U supabase_admin -d postgres \
        --clean --if-exists "$dump" 2>&1 >/dev/null || true)"
    if grep -Eq 'TABLE DATA|COPY ' <<<"$errors"; then
        log "$name: restoring its data failed - kept as it was"
        return 1
    fi

    "${PSQL_SCRATCH[@]}" -c "create table _scrub_journal (table_name text, row_id text, kind text);
                              create table _scrub_persona (row_id text, metadata jsonb);"
    printf '%s\n' "$JOURNAL" | "${PSQL_SCRATCH[@]}" -c "copy _scrub_journal from stdin"
    if [ -n "$OVERWRITES" ]; then
        printf '%s\n' "$OVERWRITES" | "${PSQL_SCRATCH[@]}" -c "copy _scrub_persona from stdin"
    fi

    # Only tables the journal may name (db/schema.sql section 12), each skipped
    # if this dump predates it. A note's chunks go with it.
    "${PSQL_SCRATCH[@]}" <<'SQL'
begin;
do $$
begin
    if to_regclass('public.note_chunks') is not null then
        delete from public.note_chunks c using _scrub_journal j
        where j.table_name = 'notes' and j.kind = 'delete' and c.note_id::text = j.row_id;
    end if;
    if to_regclass('public.notes') is not null then
        delete from public.notes n using _scrub_journal j
        where j.table_name = 'notes' and j.kind = 'delete' and n.id::text = j.row_id;
    end if;
    if to_regclass('public.episodic_memory') is not null then
        delete from public.episodic_memory e using _scrub_journal j
        where j.table_name = 'episodic_memory' and j.kind = 'delete' and e.id::text = j.row_id;
    end if;
    if to_regclass('public.persona') is not null then
        delete from public.persona p using _scrub_journal j
        where j.table_name = 'persona' and j.kind = 'delete' and p.id::text = j.row_id;
        -- A fact that survives without the deleted note: the live copy's metadata.
        update public.persona p set metadata = s.metadata from _scrub_persona s
        where s.metadata is not null and p.id::text = s.row_id;
        -- ...and one that has since gone from the live database goes here too.
        delete from public.persona p using _scrub_persona s
        where s.metadata is null and p.id::text = s.row_id;
    end if;
end $$;
drop table _scrub_journal;
drop table _scrub_persona;
commit;
SQL

    # Dumped exactly as backup.sh dumps, next to the original (same filesystem,
    # so the swap below is atomic).
    docker compose exec -T scrub-db rm -f "$tmp"
    docker compose exec -T scrub-db pg_dump -U supabase_admin -d postgres -Fc -f "$tmp"

    # The new dump must read back and hold the same objects as the old one.
    if ! diff -q <(toc "$dump") <(toc "$tmp") >/dev/null; then
        docker compose exec -T scrub-db rm -f "$tmp"
        log "$name: scrubbed copy doesn't match the original's contents list - kept as it was"
        return 1
    fi

    # Inside the container: it owns the file, and keeps the original's time.
    docker compose exec -T scrub-db sh -c 'touch -r "$1" "$2" && mv -f "$2" "$1"' sh "$dump" "$tmp"
    log "$name: scrubbed"
}

# --- self-test -----------------------------------------------------------------

if [ "$mode" = self-test ]; then
    log "self-test: made-up data in scrub-db only; the live database and backups aren't touched"
    U=5e1f0000-0000-4000-8000-000000000001
    GONE=5e1f0000-0000-4000-8000-0000000000a1      # the deleted note
    KEPT=5e1f0000-0000-4000-8000-0000000000a2      # a note that stays
    EP_GONE=5e1f0000-0000-4000-8000-0000000000b1
    EP_KEPT=5e1f0000-0000-4000-8000-0000000000b2
    FACT_GONE=5e1f0000-0000-4000-8000-0000000000c1
    FACT_KEPT=5e1f0000-0000-4000-8000-0000000000c2  # survives, minus the deleted note
    SECRET="selftest gate code 4417"
    name=".selftest-$(date +%s).dump"
    dump="/backups/$name"

    scratch_up
    docker compose exec -T scrub-db psql -v ON_ERROR_STOP=1 -X -q -U postgres -d postgres \
        -f /nova-schema/schema.sql >/dev/null
    "${PSQL_SCRATCH[@]}" <<SQL
insert into auth.users (id) values ('$U');
insert into public.notes (id, user_id, source, kind, text) values
    ('$GONE', '$U', 'typed', 'quick', '$SECRET'),
    ('$KEPT', '$U', 'typed', 'quick', 'buy milk');
insert into public.note_chunks (note_id, user_id, idx, text, embedding) values
    ('$GONE', '$U', 0, '$SECRET', array_fill(0.1, array[1024])::vector);
insert into public.episodic_memory (id, user_id, event_type, event, action) values
    ('$EP_GONE', '$U', 'voice', '{"text": "note $SECRET"}', '{"actions": [{"input": {"note_id": "$GONE"}}]}'),
    ('$EP_KEPT', '$U', 'voice', '{"text": "what is the weather"}', null);
insert into public.persona (id, user_id, text, metadata, embedding) values
    ('$FACT_GONE', '$U', '$SECRET', '{"note_id": "$GONE"}', array_fill(0.1, array[1024])::vector),
    ('$FACT_KEPT', '$U', 'Lives at number 12', '{"also_keys": ["note:$GONE"], "quote": "$SECRET"}',
     array_fill(0.1, array[1024])::vector);
SQL
    docker compose exec -T scrub-db pg_dump -U supabase_admin -d postgres -Fc -f "$dump"
    docker compose exec -T scrub-db touch -d "2026-01-01 03:15" "$dump"
    before="$(docker compose exec -T scrub-db stat -c %Y "$dump")"
    cleanup_test() { docker compose exec -T scrub-db rm -f "$dump" "/backups/.scrub-$name" >/dev/null 2>&1 || true; }

    JOURNAL="$(printf 'notes\t%s\tdelete\nepisodic_memory\t%s\tdelete\npersona\t%s\tdelete\npersona\t%s\toverwrite' \
        "$GONE" "$EP_GONE" "$FACT_GONE" "$FACT_KEPT")"
    OVERWRITES="$(printf '%s\t{"also_keys": []}' "$FACT_KEPT")"
    if ! scrub_dump "$dump"; then cleanup_test; die "self-test: the scrub refused the test dump"; fi

    # Restore the scrubbed dump into a fresh copy and look.
    scratch_up
    docker compose exec -T scrub-db pg_restore -U supabase_admin -d postgres --clean --if-exists "$dump" \
        >/dev/null 2>&1 || true
    got="$("${PSQL_SCRATCH[@]}" -At -c "
        select (select count(*) from public.notes where id = '$GONE')
            || ',' || (select count(*) from public.note_chunks where note_id = '$GONE')
            || ',' || (select count(*) from public.episodic_memory where id = '$EP_GONE')
            || ',' || (select count(*) from public.persona where id = '$FACT_GONE')
            || ',' || (select metadata::text from public.persona where id = '$FACT_KEPT')
            || ',' || (select count(*) from public.notes where id = '$KEPT')
            || ',' || (select count(*) from public.episodic_memory where id = '$EP_KEPT')")"
    expected='0,0,0,0,{"also_keys": []},1,1'
    leaks="$(docker compose exec -T scrub-db pg_restore -f - "$dump" | grep -c "4417" || true)"
    after="$(docker compose exec -T scrub-db stat -c %Y "$dump")"
    cleanup_test

    [ "$got" = "$expected" ] || die "self-test FAILED: expected $expected, got $got"
    [ "$leaks" = 0 ] || die "self-test FAILED: the deleted note's text is still in the dump ($leaks lines)"
    [ "$before" = "$after" ] || die "self-test FAILED: the dump's time changed ($before -> $after)"
    log "self-test passed: deleted rows gone, kept rows kept, the text appears nowhere in the dump, timestamp kept"
    exit 0
fi

# --- the real thing ------------------------------------------------------------

waiting="$("${PSQL_LIVE[@]}" -At -c "
    select coalesce(max(id), 0), count(*) filter (where deleted_at < now() - interval '$DEBOUNCE')
    from public.deletion_journal")"
upto="${waiting%%|*}"
ripe="${waiting##*|}"
[ "$upto" != 0 ] || exit 0
[ "$mode" = now ] || [ "$ripe" != 0 ] || exit 0

# Ids and kinds (no content); and, for the facts that survive, their live
# metadata (current data, already in tonight's dump anyway). Held in memory,
# never in a file.
JOURNAL="$("${PSQL_LIVE[@]}" -c "copy (
    select table_name, row_id, kind from public.deletion_journal where id <= $upto
) to stdout")"
OVERWRITES="$("${PSQL_LIVE[@]}" -c "copy (
    select j.row_id, p.metadata from public.deletion_journal j
    left join public.persona p on p.id::text = j.row_id
    where j.id <= $upto and j.table_name = 'persona' and j.kind = 'overwrite'
) to stdout")"
rows="$(grep -c . <<<"$JOURNAL" || true)"

shopt -s nullglob
dumps=(data/backups/nova-*.dump)
log "$rows journalled row(s) up to #$upto, ${#dumps[@]} dump(s)"

failed=0
for path in "${dumps[@]}"; do
    scrub_dump "/backups/$(basename "$path")" || failed=$((failed + 1))
done

if [ "$failed" != 0 ]; then
    die "$failed dump(s) not scrubbed; the journal is kept and the next run tries again"
fi

# Every dump is clean of these rows, so the journal can forget them too.
"${PSQL_LIVE[@]}" -c "delete from public.deletion_journal where id <= $upto"
log "done; journal cleared up to #$upto"
