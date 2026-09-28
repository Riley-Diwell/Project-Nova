"""Shared fixtures for the notes tests.

A helper module rather than conftest.py so the notes tests don't contend with
other features' fixtures: each notes test module does
`from notes_support import *`.

Everything runs against in-memory stores with FakeEmbedder (deterministic,
not semantic - so tests assert exact/self matches and token matches, never
fuzzy meaning) and a list standing in for episodic_memory.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

import pytest

from app.core.request_user import as_user
from app.store import memory, notes, persona
from app.store.notes import InMemoryNotesStore, NoteIn
from app.store.persona import FakeEmbedder, InMemoryPersonaStore


class FakeMemory:
    """The slice of app.store.memory the notes code and consolidation use -
    per user, like the real one: every call names the account, and reads only
    ever see that account's rows."""

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    def append(self, user_id, entry: dict[str, Any]) -> str:
        row = {"id": str(uuid.uuid4()), "created_at": datetime.now(timezone.utc).isoformat(),
               "action": None, "outcome": None, **entry, "user_id": str(user_id)}
        self.rows.append(row)
        return row["id"]

    def _mine(self, user_id) -> list[dict[str, Any]]:
        return [r for r in self.rows if r["user_id"] == str(user_id)]

    def get(self, user_id, episode_id: str) -> dict[str, Any] | None:
        return next((r for r in self._mine(user_id) if r["id"] == episode_id), None)

    def recent(self, user_id, event_type: str, limit: int) -> list[dict[str, Any]]:
        return [r for r in self._mine(user_id) if r.get("event_type") == event_type][-limit:]

    def recent_all(self, user_id, limit: int, since=None, until=None) -> list[dict[str, Any]]:
        return list(reversed(self._mine(user_id)))[:limit]

    def all(self, user_id) -> list[dict[str, Any]]:
        return self._mine(user_id)

    def close(self, user_id, episode_id: str, action=None, outcome=None) -> None:
        for r in self._mine(user_id):
            if r["id"] == episode_id:
                if action is not None:
                    r["action"] = action
                if outcome is not None:
                    r["outcome"] = outcome


# Two accounts. USER is the one each test acts as unless it says otherwise;
# OTHER exists to prove USER never sees their notes.
USER = UUID("aaaaaaaa-0000-0000-0000-000000000001")
OTHER = UUID("bbbbbbbb-0000-0000-0000-000000000002")


@pytest.fixture
def stores(monkeypatch):
    """Fresh in-memory notes + persona stores and a fake episode log, with
    USER bound as the signed-in user (what main.py's bind_request_user does)."""
    embedder = FakeEmbedder()
    persona_store = InMemoryPersonaStore(embedder)
    notes_store = InMemoryNotesStore(embedder)
    persona.set_store(persona_store)
    persona.set_embedder(embedder)
    notes.set_store(notes_store)
    notes.set_processor(None)

    fake = FakeMemory()
    for name in ("append", "get", "recent", "recent_all", "all", "close"):
        monkeypatch.setattr(memory, name, getattr(fake, name))

    with as_user(USER):
        yield {"notes": notes_store, "persona": persona_store, "memory": fake}

    persona.set_store(None)
    persona.set_embedder(None)
    notes.set_store(None)
    notes.set_processor(None)


def make_note(text: str, **kwargs: Any) -> NoteIn:
    return NoteIn(id=kwargs.pop("id", str(uuid.uuid4())), text=text, **kwargs)


__all__ = ["FakeMemory", "stores", "make_note", "USER", "OTHER"]
