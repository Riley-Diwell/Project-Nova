-- ============================================================================
-- NOVA V1 - backend schema (Build Plan sections 5.4 & 5.5)
-- ----------------------------------------------------------------------------
-- Paste this whole file into the Supabase SQL editor and Run. It is idempotent
-- (safe to re-run): every object uses "if not exists" / "create or replace".
--
--   * public.memory  - episodic log {event, user_state, action, outcome}  (5.5)
--   * public.persona - durable, vector-searchable beliefs                 (5.4)
--   * match_persona() - semantic search used by app.persona.search()
-- ============================================================================

create extension if not exists "pgcrypto";
create extension if not exists "vector";


-- ── Memory store (episodic, section 5.5) ────────────────────────────────────
-- Append-only. `outcome` is patched after the action resolves (two-phase).
create table if not exists public.memory (
    id          uuid primary key default gen_random_uuid(),
    created_at  timestamptz not null default now(),

    event       jsonb not null,                       -- what triggered the pipeline
    user_state  jsonb not null default '{}'::jsonb,   -- UserState snapshot (5.2)
    action      jsonb not null,                       -- dispatched Tool {tool, params}
    outcome     jsonb,                                -- {status, user_response, gain_delta, detail}

    tool        text not null                         -- denormalised action->>'tool' for indexed gain queries
);

create index if not exists memory_tool_created_idx  on public.memory (tool, created_at desc);
create index if not exists memory_created_idx        on public.memory (created_at desc);
create index if not exists memory_outcome_status_idx on public.memory ((outcome ->> 'status'));

comment on table public.memory is
    'NOVA V1 episodic memory. Append-only; outcome is patched after the action resolves.';


-- ── Persona store (durable, vector-searchable, section 5.4) ──────────────────
-- The slow-changing model of who the user is. NO raw sensor data.
-- Embeddings: local BAAI/bge-large-en-v1.5 via fastembed, 1024-dim (see
-- backend/app/persona/embeddings.py EMBED_DIM). Change both together.
create table if not exists public.persona (
    id          uuid primary key default gen_random_uuid(),
    created_at  timestamptz not null default now(),
    updated_at  timestamptz not null default now(),

    text        text not null,                        -- grounded belief statement
    category    text[] not null default '{}',         -- ontology path, e.g. {opinions, likes, food}
    confidence  real not null default 1.0,
    metadata    jsonb not null default '{}'::jsonb,

    embedding   vector(1024) not null                 -- bge embedding of `text`
);

-- Approximate nearest-neighbour index for cosine distance.
create index if not exists persona_embedding_idx
    on public.persona using ivfflat (embedding vector_cosine_ops) with (lists = 100);

comment on table public.persona is
    'NOVA V1 durable Persona. Vector-searchable beliefs; no raw sensor data.';

-- Semantic search used by app.persona.search(). Returns cosine similarity in
-- [0,1] (higher = closer). `filter_category` restricts to an ontology subtree.
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
    where (filter_category is null or p.category[1:array_length(filter_category,1)] = filter_category)
      and 1 - (p.embedding <=> query_embedding) >= min_similarity
    order by p.embedding <=> query_embedding
    limit match_count
$$;
