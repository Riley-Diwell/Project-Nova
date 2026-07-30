-- schema.sql - Supabase (Postgres) storage for NOVA V1  (Jay)
--
-- Run this once in the Supabase SQL editor (Dashboard -> SQL Editor -> New query)
-- to create the tables the backend reads/writes.
--
-- V1 scope in this file:
--   1. episodic_memory - the append-only Memory store (Section 5.5).
--   2. tool_gain        - per-tool Controller Gain (Section 5.7). User-set in V1,
--                         written by the Android app, read by the tools. Owned by
--                         another team member - defined here only so the schema is
--                         complete; change only if the implementation requires it.
--   3. persona          - the durable, vector-searchable Persona (Section 5.4),
--                         plus match_persona() for semantic search.
--
-- Re-running this file is safe: every object uses "if not exists" /
-- "create or replace".
--
-- gen_random_uuid() is built into Postgres 13+ (Supabase), no extension needed.
-- pgvector is not - section 3 enables it.


-- ---------------------------------------------------------------------------
-- 1. Episodic Memory  (Section 5.5)
-- ---------------------------------------------------------------------------
-- Append-only log of "what happened". The State Estimator writes one row per
-- event (event + the User State it produced). The Intent Surface reads rows
-- back - typically filtered by event_type - to analyse patterns.
--
-- action / outcome are nullable: they are filled in later by the tool +
-- reinforcement layer once a tool has fired ({event, action, outcome}). Keeping
-- them here means the log shape is stable and the generic write() can populate
-- them without a schema change.

create table if not exists public.episodic_memory (
    id          uuid        primary key default gen_random_uuid(),
    created_at  timestamptz not null    default now(),

    event_type  text        not null,   -- discriminator, e.g. 'notification', 'location', 'STT'
    event       jsonb       not null,   -- full Event payload (schemas/event.py)
    user_state  jsonb,                  -- UserState at the time (schemas/user_state.py)

    action      jsonb,                  -- tool + params, once a tool fires (later)
    outcome     text                    -- 'accepted' / 'rejected' for gain reinforcement (later)
);

-- read() filters by a chosen column; event_type is the common one, so index it.
create index if not exists episodic_memory_event_type_idx
    on public.episodic_memory (event_type);

-- reads are returned oldest-first for pattern analysis; index the sort key.
create index if not exists episodic_memory_created_at_idx
    on public.episodic_memory (created_at);


-- ---------------------------------------------------------------------------
-- 2. Controller Gain per tool  (Section 5.7 - owned by another team member)
-- ---------------------------------------------------------------------------
-- One row per tool. Mirrors ControllerGain (gain/controller_gain.py):
--   value    = learned gain      (defaults to DEFAULT_GAIN = 0.2, gain starts low, R5)
--   override = user-set gain, wins over value when present (NULL = no override)
-- In V1 this is entirely user-set: written by the Android app, read by the tools
-- to decide reactive vs proactive firing.

create table if not exists public.tool_gain (
    tool_name   text        primary key,                       -- unique tool name (ToolSchema.name)
    value       real        not null default 0.2 check (value    between 0 and 1),
    override    real                          check (override between 0 and 1),
    updated_at  timestamptz not null default now()
);


-- ---------------------------------------------------------------------------
-- 3. Persona  (Section 5.4)
-- ---------------------------------------------------------------------------
-- The durable, slow-changing model of who the user is. Vector-searchable.
-- NO raw sensor data.
--
-- EVERYTHING HERE IS DURABLE. Persona is searched by meaning and never
-- expires, so situational detail ('parked on level 3') is deliberately kept
-- out - it stays in episodic_memory, where it stops mattering on its own.
--
-- Rows arrive two ways, told apart by metadata->>'source':
--   * 'stated'  - the user said it and tools/memory_tool.py promoted it,
--                 because the note was filed into the ontology rather than
--                 left situational. metadata->>'note_id' is the source row.
--   * 'derived' - app/consolidation counted it out of repeated behaviour:
--                 five navigation requests that all resolved to the same
--                 bagel shop become one fact about where the user goes.
--                 metadata carries the evidence - support, span, episode_ids -
--                 so a derived belief can always be traced back.
-- Both are embedded the same way, so one search reaches both: "what do I like
-- to eat" finds 'likes bagels', and the substring scan over episodic_memory
-- never could. Where they disagree, 'stated' wins.
--
-- Embeddings: local BAAI/bge-large-en-v1.5 via fastembed, 1024-dim (see
-- backend/app/persona/embeddings.py EMBED_DIM). Change both together.

create extension if not exists "vector";

create table if not exists public.persona (
    id          uuid        primary key default gen_random_uuid(),
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

-- Knowledge Map (Section 5.6) browses the ontology by path; match_persona's
-- filter_category is a prefix match on the same column.
create index if not exists persona_category_idx
    on public.persona using gin (category);

comment on table public.persona is
    'NOVA V1 durable Persona. Vector-searchable beliefs and promoted notes; no raw sensor data.';

-- Semantic search used by app.persona.search(). Returns cosine similarity in
-- [0,1] (higher = closer). `filter_category` restricts to an ontology subtree,
-- e.g. {notes} for only what the user dictated; null searches everything.
create or replace function public.match_persona(
    query_embedding vector(1024),
    match_count int default 5,
    min_similarity float default 0.0,
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
    where (filter_category is null
           or p.category[1:array_length(filter_category, 1)] = filter_category)
      and 1 - (p.embedding <=> query_embedding) >= min_similarity
    order by p.embedding <=> query_embedding
    limit match_count
$$;


-- ---------------------------------------------------------------------------
-- 4. Forgotten patterns  (Section 5.6)
-- ---------------------------------------------------------------------------
-- Deletions that have to stick.
--
-- Deleting a belief from the Knowledge Map removes the row, but consolidation
-- runs again over the same episodes and re-derives it - so without this table
-- the user's correction quietly undoes itself, in the one component whose whole
-- job is to make the Agency and Privacy pillars visible. See docs/adr/0003.
--
-- `key` is the identity of the PATTERN, never the belief:
--   'trend:<signal>:<value>'  a counted repetition (app/persona/models.py
--                             tombstone_key), so that habit is not re-derived
--   'episode:<uuid>'          an utterance already extracted from, so that
--                             statement is not re-read
-- No belief text and no embedding are kept here. What was believed is gone;
-- only the instruction to stop believing it remains.

create table if not exists public.persona_forgotten (
    key         text        primary key,
    created_at  timestamptz not null default now()
);

comment on table public.persona_forgotten is
    'Patterns the user deleted from the Knowledge Map. Consolidation must not re-derive these.';
