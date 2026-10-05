-- schema.sql - Supabase (Postgres) storage for NOVA V2
--
-- Run this once in the Supabase SQL editor (Dashboard -> SQL Editor -> New query)
-- against a fresh V2 Supabase project, to create the tables the backend
-- reads/writes. Ported from nova_v1/backend/db/schema.sql. V2 has its own
-- Supabase project (bnxjyliakmtfzgwknrdh); V1 keeps ieezniwxisrjtwovqnhi.
--
--   1. episodic_memory - the append-only Memory store (app/store/memory.py).
--   2. tool_gain        - per-tool Controller Gain (app/control/gain/). User-set
--                         in V1/V2, written by the Android app, read by the tools.
--   3. persona          - the durable, vector-searchable Persona
--                         (app/store/persona/), plus match_persona() for
--                         semantic search.
--  3b. persona hybrid search and reconciliation - full-text, content hash,
--                         stated_at, match_persona_hybrid(), persona_superseded
--                         and the per-user write lease.
--  3c. Knowledge Map groups (persona_cluster, persona_cluster_member) and
--                         automatic consolidation state (consolidation_state).
--   4. persona_forgotten - deletions that have to stick (see section 4 below).
--   5. notes / note_chunks - the user's notes (app/store/notes), plus
--                         match_notes() for hybrid search.
--   6. row-level security on every table (anon key gets nothing).
--   7. reminders       - each account's reminders, synced from the phone
--                         (app/store/reminders.py).
--   8. profiles        - display name and onboarding answers (app/store/profile.py).
--   9. canvas_connections - each account's Canvas address and encrypted access
--                         token (app/store/canvas.py). Server-only: no policy.
--  10. context_resets  - when each account last changed what NOVA knows by hand,
--                         so older conversation stops being context (app/store/memory.py).
--  11. delete_episodes_referencing() - a deleted note's episodes go with it.
--  12. deletion_journal - ids of what was deleted for good, for scrubbing backups.
--
-- Every table is per-account: a user_id referencing Supabase Auth's auth.users
-- with on delete cascade, and an owner RLS policy as a backstop to the server's
-- own filtering.
--
-- Re-running this file is safe: every object uses "if not exists" /
-- "create or replace".
--
-- gen_random_uuid() is built into Postgres 13+ (Supabase), no extension needed.
-- pgvector is not - section 3 enables it.


-- ---------------------------------------------------------------------------
-- 1. Episodic Memory
-- ---------------------------------------------------------------------------
-- Append-only log of "what happened". main.py writes one row per event (event
-- + the User State it produced). The Intent Surface reads rows back -
-- typically filtered by event_type - to analyse patterns.
--
-- action / outcome are nullable: they are filled in later by the tool +
-- reinforcement layer once a tool has fired ({event, action, outcome}). Keeping
-- them here means the log shape is stable and the generic write() can populate
-- them without a schema change.

create table if not exists public.episodic_memory (
    id          uuid        primary key default gen_random_uuid(),
    user_id     uuid        not null references auth.users (id) on delete cascade,
    created_at  timestamptz not null    default now(),

    event_type  text        not null,   -- discriminator, e.g. 'notification', 'location', 'voice'
    event       jsonb       not null,   -- full Event payload (app/schemas/event.py)
    user_state  jsonb,                  -- UserState at the time (app/schemas/user_state.py)

    action      jsonb,                  -- tool + params, once a tool fires (later)
    outcome     text                    -- 'accepted' / 'rejected' for gain reinforcement (later)
);

-- read() filters by a chosen column; event_type is the common one, so index it.
create index if not exists episodic_memory_event_type_idx
    on public.episodic_memory (event_type);

-- reads are returned oldest-first for pattern analysis; index the sort key.
create index if not exists episodic_memory_created_at_idx
    on public.episodic_memory (created_at);

-- every read is one user's: recent(event_type) and recent_all()/all().
create index if not exists episodic_memory_user_type_created_idx
    on public.episodic_memory (user_id, event_type, created_at desc);
create index if not exists episodic_memory_user_created_idx
    on public.episodic_memory (user_id, created_at desc);


-- ---------------------------------------------------------------------------
-- 2. Controller Gain per tool
-- ---------------------------------------------------------------------------
-- One row per tool. Mirrors ControllerGain (app/control/gain/controller_gain.py):
--   value    = learned gain      (defaults to DEFAULT_GAIN = 0.2, gain starts low, R5)
--   override = user-set gain, wins over value when present (NULL = no override)
-- Entirely user-set: written by the Android app, read by the tools to decide
-- reactive vs proactive firing.

create table if not exists public.tool_gain (
    user_id     uuid        not null references auth.users (id) on delete cascade,
    tool_name   text        not null,                          -- tool name (ToolSchema.name)
    value       real        not null default 0.2 check (value    between 0 and 1),
    override    real                          check (override between 0 and 1),
    updated_at  timestamptz not null default now(),
    primary key (user_id, tool_name)                           -- one set of dials per user
);


-- ---------------------------------------------------------------------------
-- 3. Persona
-- ---------------------------------------------------------------------------
-- The durable, slow-changing model of who the user is. Vector-searchable.
-- NO raw sensor data.
--
-- EVERYTHING HERE IS DURABLE. Persona is searched by meaning and never
-- expires, so situational detail ('parked on level 3') is deliberately kept
-- out - it stays in episodic_memory, where it stops mattering on its own.
--
-- Rows arrive two ways, told apart by metadata->>'source':
--   * 'stated'  - the user said it and tools/functions/memory_tool.py promoted
--                 it, because the note was filed into the ontology rather than
--                 left situational. metadata->>'note_id' is the source row.
--   * 'derived' - app/store/consolidation counted it out of repeated
--                 behaviour: five navigation requests that all resolved to the
--                 same bagel shop become one fact about where the user goes.
--                 metadata carries the evidence - support, span, episode_ids -
--                 so a derived belief can always be traced back.
-- Both are embedded the same way, so one search reaches both: "what do I like
-- to eat" finds 'likes bagels', and the substring scan over episodic_memory
-- never could. Where two disagree, the more recent (stated_at) is kept and
-- the other removed - see section 3b and app/store/persona/reconcile.py.
--
-- Embeddings: local BAAI/bge-large-en-v1.5 via fastembed, 1024-dim (see
-- app/store/persona/embeddings.py EMBED_DIM). Change both together.

create extension if not exists "vector";

create table if not exists public.persona (
    id          uuid        primary key default gen_random_uuid(),
    user_id     uuid        not null references auth.users (id) on delete cascade,
    created_at  timestamptz not null default now(),
    updated_at  timestamptz not null default now(),

    text        text        not null,                    -- grounded belief statement
    category    text[]      not null default '{}',       -- ontology path, e.g. {opinions, likes, food}
    confidence  real        not null default 1.0,
    metadata    jsonb       not null default '{}'::jsonb,-- provenance, e.g. {"note_id": "...", "tags": [...]}

    embedding   vector(1024) not null                    -- bge embedding of `text`
);

-- Approximate nearest-neighbour index for cosine distance. HNSW rather than
-- ivfflat because ivfflat's lists are trained at CREATE INDEX time: built on
-- the empty table this file creates, it would give poor recall until manually
-- rebuilt. HNSW needs no training and stays honest as rows trickle in.
create index if not exists persona_embedding_idx
    on public.persona using hnsw (embedding vector_cosine_ops);

-- Knowledge Map browses the ontology by path; match_persona's filter_category
-- is a prefix match on the same column.
create index if not exists persona_category_idx
    on public.persona using gin (category);

-- Every read is one user's (all_facts, vectors, match_persona's filter_user).
-- The HNSW index filters by user after the vector search, so a search can
-- return fewer than match_count rows once there are many users; fine at this
-- scale (pgvector 0.8's hnsw.iterative_scan fixes it if it ever isn't).
create index if not exists persona_user_idx on public.persona (user_id);

comment on table public.persona is
    'NOVA V2 durable Persona. Vector-searchable beliefs and promoted notes; no raw sensor data.';

-- Semantic search used by app.store.persona.search(). Returns cosine similarity
-- in [0,1] (higher = closer). `filter_category` restricts to an ontology
-- subtree, e.g. {notes} for only what the user dictated; null searches
-- everything.
-- One user's facts only. The pre-accounts signature (no filter_user) is
-- dropped so PostgREST can't pick an overload that searches everyone.
drop function if exists public.match_persona(vector, integer, double precision, text[]);

create or replace function public.match_persona(
    filter_user     uuid,
    query_embedding vector(1024),
    match_count     int default 5,
    min_similarity  float default 0.0,
    filter_category text[] default null
)
returns table (
    id uuid,
    created_at timestamptz,
    updated_at timestamptz,
    text text,
    category text[],
    confidence real,
    metadata jsonb,
    similarity float
)
language sql stable
as $$
    select
        p.id, p.created_at, p.updated_at, p.text, p.category,
        p.confidence, p.metadata,
        1 - (p.embedding <=> query_embedding) as similarity
    from public.persona p
    where p.user_id = filter_user
      and (filter_category is null
           or p.category[1:array_length(filter_category, 1)] = filter_category)
      and 1 - (p.embedding <=> query_embedding) >= min_similarity
    order by p.embedding <=> query_embedding
    limit match_count
$$;


-- ---------------------------------------------------------------------------
-- 3b. Persona: hybrid search, and no duplicates or contradictions
-- ---------------------------------------------------------------------------
-- Additive and re-runnable. Run it BEFORE deploying the server that uses it,
-- then POST /persona/reconcile?preview=false per user to clean what is
-- already stored, then create the unique index at the bottom of this section.
--
-- Hybrid search is era-memory's design (github.com/Era-Laboratories/
-- era-memory): a vector search and a keyword search side by side, fused by
-- rank in app/store/persona/ranking.py. `fts` is the keyword half. Postgres'
-- english config drops "not"/"no" - fine, the keyword half only finds
-- candidates; meaning is judged elsewhere.
--
-- stated_at is when a belief was last asserted - the utterance, the last
-- episode behind a trend, the edit - not when the row was written. "Most
-- recent wins" compares it.
--
-- content_hash is sha256 of the normalised text (lowercase, collapsed
-- whitespace, no trailing full stop), computed in Python - exact duplicates
-- are merged without a model call. source_keys are the patterns a belief
-- stands for (the same keys a delete tombstones, section 4), so "the fact
-- holding trend:X" is a lookup rather than a similarity search.

alter table public.persona add column if not exists stated_at    timestamptz;
alter table public.persona add column if not exists content_hash text;
alter table public.persona add column if not exists source_keys  text[] not null default '{}';
-- text only: array_to_string(category) is not immutable, so it can't go in a
-- generated column.
alter table public.persona add column if not exists fts tsvector
    generated always as (to_tsvector('english', text)) stored;

-- Backfill stated_at, best evidence first: a user edit, then the episode the
-- statement came from, then a trend's last episode, then when it was written.
update public.persona set stated_at = updated_at
 where stated_at is null and metadata->>'edited' = 'true';
update public.persona p set stated_at = e.created_at
  from public.episodic_memory e
 where p.stated_at is null and e.user_id = p.user_id
   and e.id::text = p.metadata->>'episode_id';
update public.persona set stated_at = (metadata->>'last_seen')::timestamptz
 where stated_at is null and metadata->>'source' = 'derived'
   and metadata->>'last_seen' ~ '^\d{4}-\d{2}-\d{2}';
update public.persona set stated_at = created_at where stated_at is null;
alter table public.persona alter column stated_at set default now();
alter table public.persona alter column stated_at set not null;

create index if not exists persona_fts_idx         on public.persona using gin (fts);
create index if not exists persona_source_keys_idx on public.persona using gin (source_keys);
create index if not exists persona_user_hash_idx   on public.persona (user_id, content_hash);

-- Supersession watermarks: "a newer belief won over this pattern as of
-- superseded_at". NOT a tombstone (section 4): consolidation may bring the
-- pattern back on evidence newer than superseded_at - a habit the user took
-- up again - while an old utterance, which never gets newer, stays down.
create table if not exists public.persona_superseded (
    user_id        uuid        not null references auth.users (id) on delete cascade,
    key            text        not null,        -- same key space as persona_forgotten
    superseded_at  timestamptz not null,        -- stated_at of the winning belief
    by_fact_id     uuid,                        -- no FK: the winner may be deleted later
    created_at     timestamptz not null default now(),
    primary key (user_id, key)
);

comment on table public.persona_superseded is
    'Persona patterns a newer belief overruled, and when. Consolidation re-offers one only with newer evidence.';

-- Per-user write lease around persona.remember()'s read -> judge -> write.
-- Several server instances can write one user's Persona at once (a voice save
-- and a consolidation run), and PostgREST has no session to hold an advisory
-- lock in, so the lease is a row: taken if free or expired, released by its
-- holder, and expiring on its own if the holder dies.
create table if not exists public.persona_write_lock (
    user_id    uuid        primary key references auth.users (id) on delete cascade,
    holder     text        not null,
    expires_at timestamptz not null
);

create or replace function public.persona_lock(p_user uuid, p_holder text, p_ttl_ms int default 15000)
returns boolean
language sql volatile
as $$
    insert into public.persona_write_lock as l (user_id, holder, expires_at)
    values (p_user, p_holder, now() + make_interval(secs => p_ttl_ms / 1000.0))
    on conflict (user_id) do update
        set holder = excluded.holder, expires_at = excluded.expires_at
        where l.expires_at < now() or l.holder = excluded.holder
    returning true
$$;  -- null when not acquired

create or replace function public.persona_unlock(p_user uuid, p_holder text)
returns void
language sql volatile
as $$
    delete from public.persona_write_lock where user_id = p_user and holder = p_holder
$$;

-- Hybrid candidates for app.store.persona search() and remember(): the union
-- of the top match_count by vector and the top match_count by full-text, each
-- row with both scores. Python fuses them (ranking.rank) - rank fusion needs
-- the two lists separately, which is why this doesn't order its output.
-- `lexical_any` ORs the query's terms (write-time candidate recall) instead of
-- websearch AND semantics (a user's search). match_persona stays until every
-- server is on this.
create or replace function public.match_persona_hybrid(
    filter_user     uuid,
    query_embedding vector(1024),
    query_text      text,
    match_count     int     default 20,
    min_similarity  float   default 0.0,
    filter_category text[]  default null,
    lexical_any     boolean default false,
    exclude_ids     uuid[]  default '{}'
)
returns table (
    id uuid,
    created_at timestamptz,
    updated_at timestamptz,
    stated_at timestamptz,
    text text,
    category text[],
    confidence real,
    metadata jsonb,
    similarity float,
    lexical float
)
language sql stable
as $$
    with q as (
        select case
            when lexical_any then
                nullif(replace(plainto_tsquery('english', coalesce(query_text, ''))::text, ' & ', ' | '), '')::tsquery
            else websearch_to_tsquery('english', coalesce(query_text, ''))
        end as tsq
    ),
    mine as (
        select p.* from public.persona p
        where p.user_id = filter_user
          and not (p.id = any(coalesce(exclude_ids, '{}')))
          and (filter_category is null
               or p.category[1:array_length(filter_category, 1)] = filter_category)
    ),
    sem as (
        select m.id from mine m
        where 1 - (m.embedding <=> query_embedding) >= min_similarity
        order by m.embedding <=> query_embedding
        limit match_count
    ),
    lex as (
        select m.id from mine m, q
        where q.tsq is not null and m.fts @@ q.tsq
        order by ts_rank(m.fts, q.tsq, 32) desc
        limit match_count
    )
    select
        m.id, m.created_at, m.updated_at, m.stated_at, m.text, m.category,
        m.confidence, m.metadata,
        1 - (m.embedding <=> query_embedding) as similarity,
        coalesce(case when q.tsq is not null and m.fts @@ q.tsq
                      then ts_rank(m.fts, q.tsq, 32) end, 0) as lexical
    from mine m cross join q
    where m.id in (select id from sem union select id from lex)
$$;

-- ONLY after the reconcile sweep has merged the exact duplicates already
-- stored (it fails while any remain). The backstop against two instances
-- writing the same belief at the same moment; the server turns a violation
-- into a merge (store.DuplicateHash).
-- create unique index if not exists persona_user_hash_uidx
--     on public.persona (user_id, content_hash) where content_hash is not null;


-- ---------------------------------------------------------------------------
-- 3c. Knowledge Map groups, and automatic consolidation
-- ---------------------------------------------------------------------------
-- Additive and re-runnable; run after 3b. Until it has run, the server works
-- as before: the map groups by category and consolidation runs a full pass at
-- most daily when the phone asks.
--
-- persona_cluster / persona_cluster_member: the map's meaning groups
-- (app/store/persona/clusters.py). Membership is its own table, not fact
-- metadata, because an in-place rewrite of a fact replaces its metadata; the
-- cascade drops a fact's membership when the fact goes. `centroid` is the
-- running mean of the members' embeddings (not normalised).
--
-- consolidation_state: per user, where automatic consolidation is up to
-- (app/store/consolidation/state.py) - the last episode read, the trend
-- tallies counted so far, the lease that stops two passes overlapping, and
-- when the last pass finished. The tallies and the watermark live in one row
-- so a single update moves both together.

create table if not exists public.persona_cluster (
    id            uuid        primary key default gen_random_uuid(),
    user_id       uuid        not null references auth.users (id) on delete cascade,
    title         text        not null,
    title_source  text        not null default 'pending'
                  check (title_source in ('pending', 'fallback', 'model', 'user', 'topic')),
    titled_size   int         not null default 0,     -- size when last named
    centroid      vector(1024) not null,
    size          int         not null default 0,
    created_at    timestamptz not null default now(),
    title_at      timestamptz not null default now()
);
create index if not exists persona_cluster_user_idx on public.persona_cluster (user_id, created_at);

create table if not exists public.persona_cluster_member (
    fact_id     uuid        primary key references public.persona (id) on delete cascade,
    user_id     uuid        not null references auth.users (id) on delete cascade,
    cluster_id  uuid        not null references public.persona_cluster (id) on delete cascade,
    assigned_at timestamptz not null default now()
);
create index if not exists persona_cluster_member_user_idx on public.persona_cluster_member (user_id);
-- Whether the model has checked this fact's topic (clusters.py). A fact placed
-- by keyword guess alone is re-checked once, then left where it is.
alter table public.persona_cluster_member add column if not exists confirmed boolean not null default false;
-- Topics (a fixed list, clusters.py) replaced model-named groups; an existing
-- table needs 'topic' added to what title_source may hold.
alter table public.persona_cluster drop constraint if exists persona_cluster_title_source_check;
alter table public.persona_cluster add constraint persona_cluster_title_source_check
    check (title_source in ('pending', 'fallback', 'model', 'user', 'topic'));

create table if not exists public.consolidation_state (
    user_id          uuid        primary key references auth.users (id) on delete cascade,
    last_run_at      timestamptz,
    last_episode_at  text,                    -- episodic_memory.created_at exactly as read
    last_episode_ids text[]      not null default '{}',   -- ids read AT last_episode_at
    tally_version    int         not null default 0,
    tallies          jsonb       not null default '[]'::jsonb,
    lease_holder     text,
    lease_until      timestamptz,
    last_result      jsonb
);

-- Take this user's consolidation lease: free, expired, or already ours.
-- Returns true when taken, null when another pass holds it.
create or replace function public.claim_consolidation(p_user uuid, p_holder text, p_ttl_ms int default 300000)
returns boolean
language sql volatile
as $$
    insert into public.consolidation_state as s (user_id, lease_holder, lease_until)
    values (p_user, p_holder, now() + make_interval(secs => p_ttl_ms / 1000.0))
    on conflict (user_id) do update
        set lease_holder = excluded.lease_holder, lease_until = excluded.lease_until
        where s.lease_until is null or s.lease_until < now() or s.lease_holder = excluded.lease_holder
    returning true
$$;

comment on table public.persona_cluster is
    'Knowledge Map meaning groups: sticky, headed once by a model. See app/store/persona/clusters.py.';
comment on table public.consolidation_state is
    'Per-user automatic consolidation: watermark, trend tallies, lease. See app/store/consolidation/state.py.';


-- ---------------------------------------------------------------------------
-- 4. Forgotten patterns
-- ---------------------------------------------------------------------------
-- Deletions that have to stick.
--
-- Deleting a belief from the Knowledge Map removes the row, but consolidation
-- runs again over the same episodes and re-derives it - so without this table
-- the user's correction quietly undoes itself, in the one component whose whole
-- job is to make the Agency and Privacy pillars visible.
--
-- `key` is the identity of the PATTERN, never the belief:
--   'trend:<signal>:<value>'  a counted repetition (app/store/persona/models.py
--                             tombstone_key), so that habit is not re-derived
--   'episode:<uuid>'          an utterance already extracted from, so that
--                             statement is not re-read
--   'note:<uuid>'             a note a fact was promoted from (section 5), so
--                             a deleted note or belief is not brought back
-- No belief text and no embedding are kept here. What was believed is gone;
-- only the instruction to stop believing it remains.

create table if not exists public.persona_forgotten (
    user_id     uuid        not null references auth.users (id) on delete cascade,
    key         text        not null,
    created_at  timestamptz not null default now(),
    primary key (user_id, key)
);

comment on table public.persona_forgotten is
    'Patterns the user deleted from the Knowledge Map. Consolidation must not re-derive these.';


-- ---------------------------------------------------------------------------
-- 5. Notes  (app/store/notes)
-- ---------------------------------------------------------------------------
-- What the user wrote down, dictated or captured. Not episodic memory (the
-- user edits and deletes these) and not Persona (a lecture capture is the
-- lecturer's words, not facts about the user). Consolidation never reads this
-- table; a note reaches Persona only through explicit promotion, and the
-- promoted fact carries metadata->>'note_id' back to here.
--
-- id is generated by the phone and doubles as the idempotency key for its
-- offline outbox, so there is no default.
--
-- One owner per note (user_id), from the signed-in token. The server filters
-- by it on every query (app/store/notes/store.py); the owner policies below
-- are the backstop.

create table if not exists public.notes (
    id                  uuid        primary key,
    user_id             uuid        not null references auth.users (id) on delete cascade,
    created_at          timestamptz not null default now(),
    updated_at          timestamptz not null default now(),

    source              text        not null
                        check (source in ('device_voice', 'phone_voice', 'typed', 'assistant')),
    kind                text        not null
                        check (kind in ('quick', 'dictation', 'capture')),

    title               text,
    text                text        not null,
    segments            jsonb       not null default '[]'::jsonb,  -- [{start_s, end_s, text}]
    duration_s          real,
    context             jsonb,                                     -- {calendar_title, location}
    stt                 jsonb,                                     -- {engine, avg_conf}
    tags                text[]      not null default '{}',

    summary             jsonb,                                     -- NoteSummary
    summary_status      text        not null default 'none'
                        check (summary_status in ('none', 'pending', 'done', 'stale', 'failed')),

    origin_episode_id   uuid,                                      -- the voice turn, for assistant saves
    promoted_fact_ids   uuid[]      not null default '{}',

    embedding           vector(1024),                              -- bge over title + text (first 512 tokens)
    fts                 tsvector generated always as
                        (to_tsvector('english', coalesce(title, '') || ' ' || text)) stored
);

create index if not exists notes_created_at_idx on public.notes (created_at desc);
create index if not exists notes_user_created_idx on public.notes (user_id, created_at desc);
create index if not exists notes_kind_idx       on public.notes (kind);
create index if not exists notes_fts_idx        on public.notes using gin (fts);
create index if not exists notes_embedding_idx
    on public.notes using hnsw (embedding vector_cosine_ops);

-- What Nova thinks a spoken note meant (app/notes_pipeline/interpret.py):
-- likely mis-hearings fixed, punctuation and line breaks added. Kept beside
-- `text`, which stays exactly as heard. Null until read, or if nothing changed.
alter table public.notes add column if not exists interpreted_text text;

comment on table public.notes is
    'NOVA V2 user notes (quick, dictation, capture), one owner each. Never read by consolidation.';


-- ~200-word windows over long notes, so search can see past bge's 512-token
-- limit. Written by app/notes_pipeline via notes.replace_chunks().
create table if not exists public.note_chunks (
    id          uuid        primary key default gen_random_uuid(),
    note_id     uuid        not null references public.notes (id) on delete cascade,
    user_id     uuid        not null references auth.users (id) on delete cascade,
    idx         int         not null,
    text        text        not null,
    start_s     real,                     -- where the chunk starts in the recording
    end_s       real,
    embedding   vector(1024) not null
);

create index if not exists note_chunks_note_idx on public.note_chunks (note_id, idx);
create index if not exists note_chunks_user_note_idx on public.note_chunks (user_id, note_id);

alter table public.notes       enable row level security;
alter table public.note_chunks enable row level security;
drop policy if exists notes_owner on public.notes;
create policy notes_owner on public.notes for all to authenticated
    using (user_id = auth.uid()) with check (user_id = auth.uid());
drop policy if exists note_chunks_owner on public.note_chunks;
create policy note_chunks_owner on public.note_chunks for all to authenticated
    using (user_id = auth.uid()) with check (user_id = auth.uid());
create index if not exists note_chunks_embedding_idx
    on public.note_chunks using hnsw (embedding vector_cosine_ops);


-- Hybrid search used by app.store.notes.search(). Returns candidates only -
-- the caller fetches the rows and applies the final ranking (recency decay
-- for quick notes, see app/store/notes/scoring.py), so the weighting lives in
-- one place for both backends.
--
--   similarity  best cosine similarity over the note's own embedding and its
--               chunks'
--   lexical     ts_rank(..., 32), i.e. rank/(rank+1) in [0,1) - finds exact
--               tokens like 'Q3' or 'COMP2100' that bge embeds poorly
--   chunk_text  the chunk that beat the note-level embedding, if one did -
--               the snippet the caller shows
--
-- Only ever one user's notes: filter_user is required.
--
-- The per-note best-chunk step scans all of that user's chunks. Fine at one
-- user's scale; revisit (e.g. a top-k ANN pass over note_chunks first) if a
-- user's notes grow large.
--
-- The pre-accounts signature is dropped first: create-or-replace with a
-- different argument list would leave it behind as an overload that searches
-- everyone's notes.
drop function if exists public.match_notes(vector, text, int, float, timestamptz, timestamptz, text);

create or replace function public.match_notes(
    filter_user     uuid,
    query_embedding vector(1024),
    query_text      text,
    match_count     int     default 50,
    min_similarity  float   default 0.35,
    filter_since    timestamptz default null,
    filter_until    timestamptz default null,
    filter_kind     text    default null
)
returns table (
    note_id       uuid,
    similarity    float,
    lexical       float,
    chunk_text    text,
    chunk_start_s real
)
language sql stable
as $$
    with q as (
        select websearch_to_tsquery('english', coalesce(query_text, '')) as tsq
    ),
    filtered as (
        select n.id, n.embedding, n.fts
        from public.notes n
        where n.user_id = filter_user
          and (filter_since is null or n.created_at >= filter_since)
          and (filter_until is null or n.created_at <= filter_until)
          and (filter_kind  is null or n.kind = filter_kind)
    ),
    best_chunk as (
        select distinct on (c.note_id)
               c.note_id, c.text, c.start_s,
               1 - (c.embedding <=> query_embedding) as sim
        from public.note_chunks c
        join filtered f on f.id = c.note_id
        where c.user_id = filter_user
        order by c.note_id, c.embedding <=> query_embedding
    ),
    scored as (
        select f.id as note_id,
               coalesce(1 - (f.embedding <=> query_embedding), 0) as note_sim,
               bc.sim  as chunk_sim,
               bc.text as chunk_text,
               bc.start_s as chunk_start_s,
               ts_rank(f.fts, q.tsq, 32) as lexical
        from filtered f
        cross join q
        left join best_chunk bc on bc.note_id = f.id
    )
    select s.note_id,
           greatest(s.note_sim, coalesce(s.chunk_sim, 0)) as similarity,
           s.lexical,
           case when coalesce(s.chunk_sim, 0) > s.note_sim then s.chunk_text end,
           case when coalesce(s.chunk_sim, 0) > s.note_sim then s.chunk_start_s end
    from scored s
    where greatest(s.note_sim, coalesce(s.chunk_sim, 0)) >= min_similarity
       or s.lexical > 0
    order by greatest(s.note_sim, coalesce(s.chunk_sim, 0)) + 0.5 * s.lexical desc
    limit match_count
$$;


-- ---------------------------------------------------------------------------
-- 6. Row-level security
-- ---------------------------------------------------------------------------
-- RLS on everywhere, so the anon key gets nothing through PostgREST, plus an
-- owner policy so a signed-in user's own token could only ever reach their own
-- rows. The backend uses the service-role key, which bypasses RLS, and filters
-- by user itself (app/store/*): these are the backstop, not the boundary.

alter table public.episodic_memory   enable row level security;
alter table public.tool_gain         enable row level security;
alter table public.persona           enable row level security;
alter table public.persona_forgotten enable row level security;
alter table public.persona_superseded enable row level security;
alter table public.persona_write_lock enable row level security;
alter table public.persona_cluster        enable row level security;
alter table public.persona_cluster_member enable row level security;
alter table public.consolidation_state    enable row level security;
alter table public.notes             enable row level security;
alter table public.note_chunks       enable row level security;

drop policy if exists episodic_memory_owner on public.episodic_memory;
create policy episodic_memory_owner on public.episodic_memory for all to authenticated
    using (user_id = auth.uid()) with check (user_id = auth.uid());
drop policy if exists tool_gain_owner on public.tool_gain;
create policy tool_gain_owner on public.tool_gain for all to authenticated
    using (user_id = auth.uid()) with check (user_id = auth.uid());
drop policy if exists persona_owner on public.persona;
create policy persona_owner on public.persona for all to authenticated
    using (user_id = auth.uid()) with check (user_id = auth.uid());
drop policy if exists persona_forgotten_owner on public.persona_forgotten;
create policy persona_forgotten_owner on public.persona_forgotten for all to authenticated
    using (user_id = auth.uid()) with check (user_id = auth.uid());
-- Read-only for the owner: only the server records supersession. The write
-- lease gets no policy at all - it is the server's bookkeeping, nobody else's.
drop policy if exists persona_superseded_owner on public.persona_superseded;
create policy persona_superseded_owner on public.persona_superseded for select to authenticated
    using (user_id = auth.uid());
-- Groups are readable by their owner; only the server writes them. Consolidation
-- state is the server's bookkeeping and gets no policy.
drop policy if exists persona_cluster_owner on public.persona_cluster;
create policy persona_cluster_owner on public.persona_cluster for select to authenticated
    using (user_id = auth.uid());
drop policy if exists persona_cluster_member_owner on public.persona_cluster_member;
create policy persona_cluster_member_owner on public.persona_cluster_member for select to authenticated
    using (user_id = auth.uid());


-- ---------------------------------------------------------------------------
-- 7. Reminders  (app/store/reminders.py)
-- ---------------------------------------------------------------------------
-- The phone owns reminders day to day; this is the account's copy, so they
-- survive sign-out, reinstall and a new phone. Last writer wins on updated_at_ms.

create table if not exists public.reminders (
    user_id        uuid        not null references auth.users (id) on delete cascade,
    id             text        not null,              -- the phone's UUID for the reminder
    data           jsonb       not null,              -- the phone's ReminderEntity, as-is
    status         text        not null,              -- copied out of data, for purging
    updated_at_ms  bigint      not null,              -- phone's clock at its last edit: last writer wins
    synced_at      timestamptz not null default now(),-- server's clock at the last write: the pull cursor
    primary key (user_id, id)
);

create index if not exists reminders_user_synced_idx on public.reminders (user_id, synced_at);

-- The server uses the service-role key and filters by user itself; this policy
-- is the backstop for anything that ever reaches the table with a user's token.
alter table public.reminders enable row level security;
drop policy if exists reminders_owner on public.reminders;
create policy reminders_owner on public.reminders for all to authenticated
    using (user_id = auth.uid()) with check (user_id = auth.uid());

comment on table public.reminders is
    'Each account''s reminders, synced from the phone (last writer wins on updated_at_ms).';


-- ---------------------------------------------------------------------------
-- 8. Profiles  (app/store/profile.py)
-- ---------------------------------------------------------------------------
-- One row per account: display name and onboarding answers. `answers` is
-- app/schemas/profile.py's OnboardingAnswers - typed and versioned in code, so
-- a new question needs no migration. Not GoTrue's user_metadata: users can
-- write that directly, and it rides along in every token.

create table if not exists public.profiles (
    user_id                 uuid        primary key references auth.users (id) on delete cascade,
    display_name            text        check (char_length(display_name) <= 60),
    onboarding_version      int         not null default 0,   -- 0 = never finished
    onboarding_completed_at timestamptz,
    answers                 jsonb       not null default '{}'::jsonb,
    created_at              timestamptz not null default now(),
    updated_at              timestamptz not null default now()
);

alter table public.profiles enable row level security;
drop policy if exists profiles_owner on public.profiles;
create policy profiles_owner on public.profiles for all to authenticated
    using (user_id = auth.uid()) with check (user_id = auth.uid());

comment on table public.profiles is
    'One per account: display name and onboarding answers (app/schemas/profile.py).';


-- ---------------------------------------------------------------------------
-- 9. Canvas connections
-- ---------------------------------------------------------------------------
-- The Canvas address a user signs in at and the personal access token they
-- made for Nova (Account -> Settings -> Approved Integrations), encrypted by
-- the server (app/store/canvas.py). The token reads everything the student can
-- in Canvas, grades included, so RLS is on with NO policy: only the service
-- role - the Nova server - can read or write a row, never a user's own JWT.

create table if not exists public.canvas_connections (
    user_id          uuid        primary key references auth.users (id) on delete cascade,
    base_url         text        not null check (base_url like 'https://%'),
    token_ciphertext text        not null,
    canvas_user_name text,
    connected_at     timestamptz not null default now()
);

alter table public.canvas_connections enable row level security;

comment on table public.canvas_connections is
    'One per account: Canvas address and Fernet-encrypted access token. Server-only.';


-- ---------------------------------------------------------------------------
-- 10. Context resets
-- ---------------------------------------------------------------------------
-- The last time the user edited or deleted something on the Knowledge Map.
-- Conversation from before it is no longer handed to the model as recent
-- context, so a behaviour the user just deleted ("always reply in pirate
-- speak") can't carry on by the model copying its own recent replies.

create table if not exists public.context_resets (
    user_id  uuid        primary key references auth.users (id) on delete cascade,
    reset_at timestamptz not null default now()
);

alter table public.context_resets enable row level security;
drop policy if exists context_resets_owner on public.context_resets;
create policy context_resets_owner on public.context_resets for all to authenticated
    using (user_id = auth.uid()) with check (user_id = auth.uid());


-- ---------------------------------------------------------------------------
-- 11. Deleting a note's episodes
-- ---------------------------------------------------------------------------
-- Episodes are append-only except when the user deletes what they recorded: a
-- deleted note's words must not survive in the voice turn that saved it, or in
-- a later turn that read it back or set a reminder from it
-- (docs/plans/notes-hard-delete-plan.md). Those turns carry the note's id in
-- their event or action, so they are found by exact id - never by text.
-- app/store/memory.py delete_referencing() calls this; PostgREST can't filter
-- jsonb as text.
--
-- Invoker's rights, deliberately not security definer: the server's service
-- key bypasses RLS, and anyone else's token is held to their own rows by the
-- owner policy above. Execute is revoked from the API roles anyway.

create or replace function public.delete_episodes_referencing(p_user uuid, p_ref text)
returns setof uuid
language sql volatile
as $$
    delete from public.episodic_memory
    where user_id = p_user
      and length(p_ref) >= 8
      and (strpos(event::text, p_ref) > 0 or strpos(coalesce(action::text, ''), p_ref) > 0)
    returning id
$$;

revoke execute on function public.delete_episodes_referencing(uuid, text) from public, anon, authenticated;


-- ---------------------------------------------------------------------------
-- 12. Deletion journal
-- ---------------------------------------------------------------------------
-- What has been deleted for good, so the nightly backups can forget it too:
-- deploy/scrub-backups.sh removes every row listed here from every dump in
-- data/backups, then empties the journal (docs/plans/notes-hard-delete-plan.md
-- phase 4). Ids only, never content.
--
-- kind 'delete': the row is gone; delete it from the dumps.
-- kind 'overwrite': the row survives but lost some of the deleted thing (a
--   Persona fact that merged a deleted note, minus that note's provenance);
--   the dumps get the live row's current metadata.
-- table_name 'account': a deleted account (row_id = its user id). Every row
--   with that user_id, in every table, and its auth schema rows leave the
--   dumps (app/store/account.py).
--
-- No foreign key to auth.users, deliberately: deleting an account must not
-- cascade away the journal before its backups are scrubbed.

create table if not exists public.deletion_journal (
    id          bigserial   primary key,
    user_id     uuid        not null,
    table_name  text        not null,
    row_id      text        not null,
    kind        text        not null default 'delete' check (kind in ('delete', 'overwrite')),
    deleted_at  timestamptz not null default now()
);

-- 'account' came later; the check is replaced so databases made before it get it.
alter table public.deletion_journal drop constraint if exists deletion_journal_table_name_check;
alter table public.deletion_journal add constraint deletion_journal_table_name_check
    check (table_name in ('notes', 'episodic_memory', 'persona', 'account'));

create index if not exists deletion_journal_deleted_at_idx on public.deletion_journal (deleted_at);

-- Server-only: RLS on and no policy, like canvas_connections.
alter table public.deletion_journal enable row level security;

-- GoTrue's audit log keeps a line per sign-in, sign-out and admin action, with
-- the account's id and email in its payload, and deleting the account leaves
-- them. app/store/account.py calls this after the delete. Its own audit line
-- names the user too, so it goes as well. Matched on the user id, which is a
-- UUID, so it can't match anyone else's line.
--
-- Security definer because the server's key can't reach the auth schema;
-- execute is revoked from the API roles, like delete_episodes_referencing.
-- The table is GoTrue's, so it's looked up at run time rather than assumed.

create or replace function public.delete_auth_audit(p_user uuid)
returns integer
language plpgsql volatile security definer
set search_path = ''
as $$
declare
    removed integer := 0;
begin
    if to_regclass('auth.audit_log_entries') is not null then
        execute 'delete from auth.audit_log_entries where strpos(payload::text, $1) > 0'
            using p_user::text;
        get diagnostics removed = row_count;
    end if;
    return removed;
end
$$;

revoke execute on function public.delete_auth_audit(uuid) from public, anon, authenticated;
