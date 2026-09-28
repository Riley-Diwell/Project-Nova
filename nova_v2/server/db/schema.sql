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
--   4. persona_forgotten - deletions that have to stick (see section 4 below).
--   5. notes / note_chunks - the user's notes (app/store/notes), plus
--                         match_notes() for hybrid search.
--   6. row-level security on every table (anon key gets nothing).
--   7. reminders       - each account's reminders, synced from the phone
--                         (app/store/reminders.py).
--   8. profiles        - display name and onboarding answers (app/store/profile.py).
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
-- never could. Where they disagree, 'stated' wins.
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
