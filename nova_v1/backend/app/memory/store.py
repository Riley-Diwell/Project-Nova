"""Memory store implementations.

`SupabaseMemoryStore` is the real backend. `InMemoryMemoryStore` is
dependency-free used for tests without Supabase credentials.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Protocol, runtime_checkable

from .models import MemoryEntry, MemoryFilter, Outcome

TABLE = "memory"


class MemoryNotFound(KeyError):
    """Raised when an entry id does not exist."""


@runtime_checkable
class MemoryStore(Protocol):
    def append(self, entry: MemoryEntry) -> str: ...
    def query(self, filter: MemoryFilter) -> list[MemoryEntry]: ...
    def update_outcome(self, entry_id: str, outcome: Outcome) -> MemoryEntry: ...
    def delete(self, entry_id: str) -> None: ...


def _row_from_entry(entry: MemoryEntry) -> dict:
    """Serialise an entry to a Supabase row (jsonb columns + denormalised tool)."""
    return {
        "event": entry.event,
        "user_state": entry.user_state,
        "action": entry.action.model_dump(),
        "outcome": entry.outcome.model_dump() if entry.outcome else None,
        "tool": entry.action.tool,
    }


def _entry_from_row(row: dict) -> MemoryEntry:
    return MemoryEntry(
        id=row["id"],
        created_at=row.get("created_at"),
        event=row["event"],
        user_state=row.get("user_state") or {},
        action=row["action"],
        outcome=row.get("outcome"),
    )


class SupabaseMemoryStore:
    """Append-only episodic log backed by the `memory` table."""

    def __init__(self, client) -> None:
        self._db = client

    def append(self, entry: MemoryEntry) -> str:
        res = self._db.table(TABLE).insert(_row_from_entry(entry)).execute()
        return res.data[0]["id"]

    def query(self, filter: MemoryFilter) -> list[MemoryEntry]:
        q = self._db.table(TABLE).select("*")
        if filter.tool is not None:
            q = q.eq("tool", filter.tool)
        if filter.status is not None:
            q = q.filter("outcome->>status", "eq", filter.status)
        if filter.since is not None:
            q = q.gte("created_at", filter.since.isoformat())
        if filter.until is not None:
            q = q.lte("created_at", filter.until.isoformat())
        q = q.order("created_at", desc=True).order("id", desc=True).limit(filter.limit)
        res = q.execute()
        return [_entry_from_row(r) for r in res.data]

    def update_outcome(self, entry_id: str, outcome: Outcome) -> MemoryEntry:
        res = (
            self._db.table(TABLE)
            .update({"outcome": outcome.model_dump()})
            .eq("id", entry_id)
            .execute()
        )
        if not res.data:
            raise MemoryNotFound(entry_id)
        return _entry_from_row(res.data[0])

    def delete(self, entry_id: str) -> None:
        """User data-agency: delete an episodic entry (Privacy pillar, section 5.6)."""
        self._db.table(TABLE).delete().eq("id", entry_id).execute()


class InMemoryMemoryStore:
    """Process-local fake with the same behaviour. No Supabase required."""

    def __init__(self) -> None:
        self._rows: dict[str, MemoryEntry] = {}
        self._seq: dict[str, int] = {}  # insertion order, tiebreaks equal timestamps
        self._next = 0

    def append(self, entry: MemoryEntry) -> str:
        entry_id = str(uuid.uuid4())
        stored = entry.model_copy(
            update={"id": entry_id, "created_at": datetime.now(timezone.utc)}
        )
        self._rows[entry_id] = stored
        self._seq[entry_id] = self._next
        self._next += 1
        return entry_id

    def query(self, filter: MemoryFilter) -> list[MemoryEntry]:
        rows = list(self._rows.values())
        if filter.tool is not None:
            rows = [r for r in rows if r.action.tool == filter.tool]
        if filter.status is not None:
            rows = [r for r in rows if r.outcome and r.outcome.status == filter.status]
        if filter.since is not None:
            rows = [r for r in rows if r.created_at and r.created_at >= filter.since]
        if filter.until is not None:
            rows = [r for r in rows if r.created_at and r.created_at <= filter.until]
        rows.sort(key=lambda r: (r.created_at, self._seq[r.id]), reverse=True)
        return rows[: filter.limit]

    def update_outcome(self, entry_id: str, outcome: Outcome) -> MemoryEntry:
        if entry_id not in self._rows:
            raise MemoryNotFound(entry_id)
        updated = self._rows[entry_id].model_copy(update={"outcome": outcome})
        self._rows[entry_id] = updated
        return updated

    def delete(self, entry_id: str) -> None:
        self._rows.pop(entry_id, None)
        self._seq.pop(entry_id, None)
