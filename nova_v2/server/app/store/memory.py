"""
store/memory.py - Section 5.5: Memory store (episodic)

STATUS: working draft

WHAT THIS FILE IS
The episodic Memory store: an append-only log of Episodes, backed by the
`episodic_memory` table in Supabase (see backend/db/schema.sql).

    append(user_id, entry)             -> episode_id
    get(user_id, episode_id)           -> one Episode, or None
    recent(user_id, event_type, limit) -> the last few Episodes of that type
    recent_all(user_id, limit)         -> the last few Episodes of any type
    all(user_id)                       -> every Episode, oldest first
    close(user_id, episode_id, ...)    -> fill in what happened
    ping()                             -> reach the table (start-up warm-up)
    reset_context(user_id)             -> older Episodes stop being context
    context_start(user_id)             -> when that last happened, or None

Every Episode belongs to one account, and every function takes that account
first: a read only ever sees the caller's rows, and
an id from another account behaves exactly like one that doesn't exist. The
user comes from the verified token (core/auth.py), never from a request body.

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
app/store/persona (Section 5.4).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Union
from uuid import UUID

from app.core.db import get_client

# Everything below is private to this module by intention - see the seam note
# above. Nothing outside it should ever mention these strings.
_TABLE = "episodic_memory"
_TYPE_COLUMN = "event_type"
_ORDER_COLUMN = "created_at"
_USER_COLUMN = "user_id"
# At or below PostgREST's max-rows, so a short page always means the end.
_PAGE_SIZE = 1000
_RESET_TABLE = "context_resets"

UserId = Union[UUID, str]


def _mine(user_id: UserId):
    """A query on the table, already narrowed to this user's rows."""
    return get_client().table(_TABLE).select("*").eq(_USER_COLUMN, str(user_id))


def append(user_id: UserId, entry: dict[str, Any]) -> str:
    """
    Open a new Episode for this user and return its id.

    `entry` is the Event and the User State it arrived with, e.g.
        {"event_type": "notification", "event": {...}, "user_state": {...}}
    What NOVA then did is filled in later by close(). Any user_id inside
    `entry` is overwritten: the owner is always the caller.
    """
    row = {**entry, _USER_COLUMN: str(user_id)}
    rows = get_client().table(_TABLE).insert(row).execute().data
    return rows[0]["id"]


def get(user_id: UserId, episode_id: str) -> dict[str, Any] | None:
    """
    One of this user's Episodes by id, or None if they have no such row -
    including when the id is someone else's.

    Needed because a turn is not judged in the request that produced it: the
    user's verdict arrives once they have heard NOVA out, by which point the
    only record of what was done is this row.
    """
    rows = _mine(user_id).eq("id", episode_id).limit(1).execute().data
    return rows[0] if rows else None


def reset_context(user_id: UserId) -> None:
    """From now on, this user's earlier Episodes are history but not context.

    Set when the user edits or deletes something NOVA knows (the Knowledge
    Map). The log keeps every row - nothing here is ever removed - but the
    Intent Surface stops showing the model conversation from before this
    moment, so what was changed can't be picked back up from it.
    """
    now = datetime.now(timezone.utc).isoformat()
    get_client().table(_RESET_TABLE).upsert(
        {_USER_COLUMN: str(user_id), "reset_at": now}, on_conflict=_USER_COLUMN,
    ).execute()


def context_start(user_id: UserId) -> str | None:
    """When reset_context() last ran for this user (ISO 8601), or None."""
    rows = (get_client().table(_RESET_TABLE).select("reset_at")
            .eq(_USER_COLUMN, str(user_id)).limit(1).execute().data)
    return rows[0]["reset_at"] if rows else None


def recent(user_id: UserId, event_type: str, limit: int) -> list[dict[str, Any]]:
    """
    This user's last `limit` Episodes of this type, oldest first.

    What the Intent Surface reads as short-term context. Ordering descending and
    reversing - rather than fetching everything and slicing - keeps the "last N"
    decision inside the store, where it can become a real LIMIT clause instead of
    dragging the whole history across the wire as the log grows.
    """
    rows = (
        _mine(user_id)
        .eq(_TYPE_COLUMN, event_type)
        .order(_ORDER_COLUMN, desc=True)
        .limit(limit)
        .execute()
        .data
    )
    return list(reversed(rows))


def recent_all(
    user_id: UserId, limit: int, since: str | None = None, until: str | None = None
) -> list[dict[str, Any]]:
    """
    This user's last `limit` Episodes of any type, newest first, optionally
    bounded to [since, until] (ISO datetime strings, either end optional).

    What the audit log reads - unlike recent(), not filtered by event_type,
    since a user reviewing what NOVA has done wants everything in one
    chronological list, not one type at a time. Date bounds are pushed down as
    a real query constraint for the same reason `limit` is: a growing log
    should not mean dragging the whole history across the wire to filter it
    in Python.
    """
    query = _mine(user_id)
    if since:
        query = query.gte(_ORDER_COLUMN, since)
    if until:
        query = query.lte(_ORDER_COLUMN, until)
    return query.order(_ORDER_COLUMN, desc=True).limit(limit).execute().data


def all(user_id: UserId) -> list[dict[str, Any]]:
    """
    Every one of this user's Episodes, oldest first.

    What consolidation reads. Deliberately the whole log and deliberately not
    filtered by type: a habit shows up across types - a voice request, then the
    location change that followed it - and seeing further back than the Intent
    Surface's last-N window is the entire reason that pass exists.

    Fetched in pages: PostgREST caps a single response (1000 rows by default on
    Supabase Cloud), so one unbounded select silently stops there. Ordered by id
    as well as time so rows sharing a timestamp can't straddle a page boundary
    and be skipped or repeated.
    """
    rows: list[dict[str, Any]] = []
    while True:
        page = (
            _mine(user_id)
            .order(_ORDER_COLUMN)
            .order("id")
            .range(len(rows), len(rows) + _PAGE_SIZE - 1)
            .execute()
            .data
        )
        rows.extend(page)
        if len(page) < _PAGE_SIZE:
            return rows


def since(
    user_id: UserId,
    after: str | None,
    after_ids: set[str],
    until: str,
    limit: int,
) -> list[dict[str, Any]]:
    """
    Up to `limit` of this user's Episodes from `after` up to `until`, oldest first.

    What incremental consolidation reads instead of all(): only what arrived
    since its watermark. `after` is inclusive - several episodes can share a
    timestamp, and one of them may not have been read yet - so the ids at
    exactly `after` that were already read are passed in `after_ids` and
    dropped here. `after=None` starts from the beginning of the log.
    """
    rows: list[dict[str, Any]] = []
    offset = 0
    while len(rows) < limit:
        query = _mine(user_id).lte(_ORDER_COLUMN, until)
        if after:
            query = query.gte(_ORDER_COLUMN, after)
        page = (
            query.order(_ORDER_COLUMN).order("id")
            .range(offset, offset + _PAGE_SIZE - 1)
            .execute()
            .data
        )
        offset += len(page)
        rows.extend(r for r in page if not (r.get(_ORDER_COLUMN) == after and str(r.get("id")) in after_ids))
        if len(page) < _PAGE_SIZE:
            break
    return rows[:limit]


def count_since(user_id: UserId, after: str | None, until: str, cap: int) -> int:
    """
    How many of this user's Episodes arrived after `after` (exclusive) and by
    `until`, counting no higher than `cap`. The cheap question behind "is
    consolidation due?" - it never needs the exact number past the threshold.
    """
    query = get_client().table(_TABLE).select("id").eq(_USER_COLUMN, str(user_id)).lte(_ORDER_COLUMN, until)
    if after:
        query = query.gt(_ORDER_COLUMN, after)
    return len(query.limit(cap).execute().data)


def close(
    user_id: UserId,
    episode_id: str,
    action: dict[str, Any] | None = None,
    outcome: str | None = None,
) -> None:
    """
    Complete one of this user's Episodes with what happened. Does nothing to an
    id that isn't theirs.

    `action` is the record of every Action taken; `outcome` is the user's verdict
    once it is known. They arrive at different moments - the Actions when the
    turn resolves, the Outcome when the user either lets NOVA finish or talks
    over it - so either can be written on its own without disturbing the other.
    """
    fields = {k: v for k, v in (("action", action), ("outcome", outcome)) if v is not None}
    if not fields:
        return
    (
        get_client().table(_TABLE).update(fields)
        .eq("id", episode_id).eq(_USER_COLUMN, str(user_id))
        .execute()
    )


def ping() -> None:
    """Reach the table without reading anyone's rows - the start-up warm-up
    (main.py), which runs before there is a user to ask on behalf of."""
    get_client().table(_TABLE).select("id").limit(0).execute()
