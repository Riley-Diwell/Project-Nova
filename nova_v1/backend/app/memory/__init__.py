"""
memory/ - Section 5.5: Memory store (episodic)  (Jay)

STATUS: working draft

WHAT THIS FILE IS
The episodic Memory store: an append-only log of what happened, backed by the
`episodic_memory` table in Supabase (see backend/db/schema.sql).

Two generic helpers, both operating on that table:
  - write(record)        append one row; called by the State Estimator when a
                         new event/state is passed in.
  - read(column, value)  pull every row where `column` == `value`; called by the
                         Intent Surface to analyse patterns.

Both are deliberately generic:
  * write() takes a dict of column -> value, so the State Estimator decides what
    to log without this module knowing the event shape.
  * read() takes the column to filter on as an argument - it is NOT fixed - so
    the same helper serves any lookup (event_type today, tool/outcome later).

Values in `record` must be JSON-serialisable for jsonb columns; callers using
Pydantic should pass model_dump(mode="json").

Persona (pgvector) and RAG search are out of scope here - built later.

WHO USES THIS
- Georgia: state_estimator writes entries; intent_surface reads them back
"""
from __future__ import annotations

from typing import Any

from ..db import get_client

# The episodic Memory table (backend/db/schema.sql). read()/write() default here;
# pass `table` only if you need to point the same helpers at another table.
TABLE = "episodic_memory"


def write(record: dict[str, Any], table: str = TABLE) -> str:
    """
    Append one row to the Memory log and return its generated id.

    `record` maps column names to values, e.g.
        {"event_type": "notification", "event": {...}, "user_state": {...}}
    Any columns omitted (action, outcome) stay NULL and can be filled in later.
    """
    rows = get_client().table(table).insert(record).execute().data
    return rows[0]["id"]


def read(column: str, value: Any, table: str = TABLE) -> list[dict[str, Any]]:
    """
    Return every row where `column` == `value`, oldest first.

    The searched column is chosen by the caller, so this one helper covers any
    filter. To pull all notification episodes for pattern analysis:
        read("event_type", "notification")
    """
    return (
        get_client()
        .table(table)
        .select("*")
        .eq(column, value)
        .order("created_at")  # chronological, for pattern analysis
        .execute()
        .data
    )
