"""NOVA V1 Persona store.

When storing persona entries, use this:

    from app.persona import search, upsert, delete
    from app.persona import Fact, PersonaQuery, Match

    # store a grounded preference (indexicals already dereferenced)
    fact_id = upsert(Fact(
        text="likes bagels",
        category=["opinions", "likes", "food"],
    ))

    # semantic recall
    hits = search(PersonaQuery(text="what food does the user like", limit=5))
    for m in hits:
        print(m.similarity, m.fact.text)

    # correction loop: 
    a contradicting episode finds the belief and updates 
    it in place by re-upserting its id. upsert(Fact(id=fact_id, text="dislikes 
    bagels", category=["opinions","likes","food"]))

By default this talks to Supabase and embeds locally with fastembed. Tests (or
a run before the model is downloaded) can swap the backend with
`set_store(InMemoryPersonaStore(FakeEmbedder()))`.
"""
from __future__ import annotations

from typing import Optional

from .embeddings import Embedder, FakeEmbedder, LocalEmbedder
from .models import Fact, Match, PersonaQuery
from .store import (
    FactNotFound,
    InMemoryPersonaStore,
    PersonaStore,
    SupabasePersonaStore,
)

__all__ = [
    "search",
    "upsert",
    "delete",
    "get",
    "get_store",
    "set_store",
    "Fact",
    "Match",
    "PersonaQuery",
    "PersonaStore",
    "InMemoryPersonaStore",
    "SupabasePersonaStore",
    "Embedder",
    "FakeEmbedder",
    "LocalEmbedder",
    "FactNotFound",
]

_store: Optional[PersonaStore] = None


def get_store() -> PersonaStore:
    """Return the active store, defaulting to Supabase + local embeddings."""
    global _store
    if _store is None:
        from ..db import get_client

        _store = SupabasePersonaStore(get_client(), LocalEmbedder())
    return _store


def set_store(store: PersonaStore) -> None:
    """Swap the backend (tests, or a local embedder/store in V2)."""
    global _store
    _store = store


def upsert(fact: Fact) -> str:
    """Store or update a durable belief. Returns the fact id."""
    return get_store().upsert(fact)


def search(query: PersonaQuery) -> list[Match]:
    """Semantic search over the Persona, most-similar first."""
    return get_store().search(query)


def delete(fact_id: str) -> None:
    """Delete one belief (user data-agency / Privacy pillar)."""
    get_store().delete(fact_id)


def get(fact_id: str) -> Fact:
    """Fetch one belief by id."""
    return get_store().get(fact_id)
