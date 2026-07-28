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
--
-- Out of scope here (built later): Persona (pgvector) and RAG.
--
-- gen_random_uuid() is built into Postgres 13+ (Supabase), no extension needed.


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
