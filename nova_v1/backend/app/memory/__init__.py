"""NOVA V1 Memory store - the public seam (section 6, contract #4).

When storing memory entries, use this:

    from app.memory import append, query, update_outcome
    from app.memory import MemoryEntry, MemoryFilter, Action, Outcome

    # log an episodic event / action
    entry_id = append(MemoryEntry(
        event={"type": "user_request", "text": "add lecture at 2pm"},
        user_state={"activity": "walking", "confidence": 0.8},
        action=Action(tool="calendar.add_event", params={"title": "Lecture"}),
    ))

    # later, when the user reacts, patch the outcome
    update_outcome(entry_id, Outcome(status="accepted"))

    # Controller Gain reads recent outcomes for one tool
    recent = query(MemoryFilter(tool="calendar.add_event", limit=20))

By default this talks to Supabase. Tests (or a Supabase-less run) can swap the
backend with `set_store(InMemoryMemoryStore())`.
"""
from __future__ import annotations

from typing import Optional

from .models import Action, MemoryEntry, MemoryFilter, Outcome
from .store import (
    InMemoryMemoryStore,
    MemoryNotFound,
    MemoryStore,
    SupabaseMemoryStore,
)

__all__ = [
    "append",
    "query",
    "update_outcome",
    "delete",
    "get_store",
    "set_store",
    "Action",
    "MemoryEntry",
    "MemoryFilter",
    "Outcome",
    "MemoryStore",
    "InMemoryMemoryStore",
    "SupabaseMemoryStore",
    "MemoryNotFound",
]

_store: Optional[MemoryStore] = None


def get_store() -> MemoryStore:
    """Return the active store, defaulting to Supabase on first use."""
    global _store
    if _store is None:
        from ..db import get_client

        _store = SupabaseMemoryStore(get_client())
    return _store


def set_store(store: MemoryStore) -> None:
    """Swap the backend (tests, or a store)."""
    global _store
    _store = store


def append(entry: MemoryEntry) -> str:
    """Log an interaction. Returns the new entry id."""
    return get_store().append(entry)


def query(filter: MemoryFilter) -> list[MemoryEntry]:
    """Read episodic entries, newest first."""
    return get_store().query(filter)


def update_outcome(entry_id: str, outcome: Outcome) -> MemoryEntry:
    """Patch the outcome of a previously appended entry (two-phase write)."""
    return get_store().update_outcome(entry_id, outcome)


def delete(entry_id: str) -> None:
    """Delete one entry (user data-agency / Privacy)."""
    get_store().delete(entry_id)
