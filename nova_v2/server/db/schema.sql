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
--   6. row-level security on every table (anon key gets nothing).
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
