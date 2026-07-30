"""
memory/ - Section 5.5: Memory store (episodic)

STATUS: working draft

WHAT THIS FILE IS
The episodic Memory store: an append-only log of Episodes, backed by the
`episodic_memory` table in Supabase (see backend/db/schema.sql).

    append(entry)                  -> episode_id
    get(episode_id)                -> one Episode, or None
    recent(event_type, limit)      -> the last few Episodes of that type
    all()                          -> every Episode, oldest first
    close(episode_id, ...)         -> fill in what happened

Every one of these is phrased in the domain's own words, and none of them takes
a column name. This module is the only place that knows the table is called 
`episodic_memory` or that its discriminator column is `event_type`. Callers 
that name columns are callers that break when the schema moves - and ADR-0001 
commits us to moving it, off Supabase and into local-first storage, without 
touching anything on the far side of this file.

Values must be JSON-serialisable for the jsonb columns; callers using Pydantic
should pass model_dump(mode="json").

An Episode is written in two phases. `append` opens the row when the Event
arrives - before the Intent Surface runs, so it survives a failing Claude call -
and `close` completes it once the turn resolves. Nothing is ever replaced or
removed; a row is only ever filled in.

This store is chronological and exact-match only: it answers "what happened, in
order", not "what is this about". Semantic search lives next door in
app/persona (Section 5.4).
"""
from __future__ import annotations

from typing import Any

# Importable both as `app.memory` (scripts/tests) and bare `memory` (the server,
# launched as uvicorn main:app from backend/app/). See db.py for the same dance.
try:
    from ..db import get_client           # app.memory
except ImportError:                       # pragma: no cover
    from db import get_client             # memory (cwd = backend/app)

# Everything below is private to this module by intention - see the seam note
# above. Nothing outside it should ever mention these strings.
_TABLE = "episodic_memory"
_TYPE_COLUMN = "event_type"
_ORDER_COLUMN = "created_at"


def append(entry: dict[str, Any]) -> str:
    """
    Open a new Episode and return its id.

    `entry` is the Event and the User State it arrived with, e.g.
        {"event_type": "notification", "event": {...}, "user_state": {...}}
    What NOVA then did is filled in later by close().
    """
    rows = get_client().table(_TABLE).insert(entry).execute().data
    return rows[0]["id"]


def get(episode_id: str) -> dict[str, Any] | None:
    """
    One Episode by id, or None if there is no such row.

    Needed because a turn is not judged in the request that produced it: the
    user's verdict arrives once they have heard NOVA out, by which point the
    only record of what was done is this row.
    """
    rows = get_client().table(_TABLE).select("*").eq("id", episode_id).limit(1).execute().data
    return rows[0] if rows else None


def recent(event_type: str, limit: int) -> list[dict[str, Any]]:
    """
    The last `limit` Episodes of this type, oldest first.

    What the Intent Surface reads as short-term context. Ordering descending and
    reversing - rather than fetching everything and slicing - keeps the "last N"
    decision inside the store, where it can become a real LIMIT clause instead of
    dragging the whole history across the wire as the log grows.
    """
    rows = (
        get_client()
        .table(_TABLE)
        .select("*")
        .eq(_TYPE_COLUMN, event_type)
        .order(_ORDER_COLUMN, desc=True)
        .limit(limit)
        .execute()
        .data
    )
    return list(reversed(rows))


def all() -> list[dict[str, Any]]:
    """
    Every Episode, oldest first.

    What consolidation reads. Deliberately the whole log and deliberately not
    filtered by type: a habit shows up across types - a voice request, then the
    location change that followed it - and seeing further back than the Intent
    Surface's last-N window is the entire reason that pass exists.
    """
    return get_client().table(_TABLE).select("*").order(_ORDER_COLUMN).execute().data


def close(
    episode_id: str,
    action: dict[str, Any] | None = None,
    outcome: str | None = None,
) -> None:
    """
    Complete an Episode with what happened.

    `action` is the record of every Action taken; `outcome` is the user's verdict
    once it is known. They arrive at different moments - the Actions when the
    turn resolves, the Outcome when the user either lets NOVA finish or talks
    over it - so either can be written on its own without disturbing the other.
    """
    fields = {k: v for k, v in (("action", action), ("outcome", outcome)) if v is not None}
    if not fields:
        return
    get_client().table(_TABLE).update(fields).eq("id", episode_id).execute()
