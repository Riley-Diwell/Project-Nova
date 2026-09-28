"""
persona/ - Section 5.4: Persona store (durable, vector-searchable)  (Jay)

STATUS: working draft

WHAT THIS FILE IS
The Persona store: the durable, slow-changing model of *who the user is*,
backed by the `persona` table in Supabase (pgvector) and searched by meaning
rather than by keyword. Contains NO raw sensor data.

EVERYTHING HERE IS DURABLE - situational detail stays in episodic memory.
Entries arrive three ways, all embedded the same way so one search reaches
them all, and all distinguished by metadata["source"]:

  - "stated"  - the user said it. Either promoted at save time by
    tools/memory_tool.py, or pulled out of the log afterwards by
    app/consolidation's statement pass. Confidence 1.0: they said it.
  - "derived" - app/consolidation counted it out of repeated behaviour, e.g.
    five navigation requests that resolved to the same bagel shop. Confidence
    scales with how many episodes back it.

Where the two disagree, stated wins - which only works because the provenance
is stored rather than guessed.

USAGE

    from app.persona import search, upsert, delete
    from app.persona import Fact, PersonaQuery

    # store a grounded preference (indexicals already dereferenced)
    fact_id = upsert(user_id, Fact(
        text="likes bagels",
        category=["opinions", "likes", "food"],
    ))

    # semantic recall
    for m in search(user_id, PersonaQuery(text="what food does the user like", limit=5)):
        print(m.similarity, m.fact.text)

    # correction loop (Section 5.5 done-when (b)): a contradicting episode
    # finds the belief and updates it in place by re-upserting its id.
    upsert(user_id, Fact(id=fact_id, text="dislikes bagels",
                category=["opinions", "likes", "food"]))

By default this talks to Supabase and embeds locally with fastembed. Tests (or
a run before the model is downloaded) can swap the backend with
`set_store(InMemoryPersonaStore(FakeEmbedder()))`.

WHO USES THIS
- Jay: tools/memory_tool.py promotes saved notes here and recalls from here
- Knowledge Map (Section 5.6): reads/edits/deletes beliefs through search/get/delete
"""
from __future__ import annotations

import hashlib
import threading
from typing import Optional

from app.store.persona.embeddings import Embedder, FakeEmbedder, LocalEmbedder
from app.store.persona.graph import (
    DEFAULT_MAX_LINKS,
    DEFAULT_MIN_SIMILARITY,
    GraphEdge,
    GraphNode,
    KnowledgeGraph,
    build_graph,
)
from app.store.persona.models import (
    Fact,
    Match,
    PersonaQuery,
    note_key,
    onboarding_key,
    tombstone_key,
    tombstone_keys,
)
from app.store.persona.store import (
    FactNotFound,
    InMemoryPersonaStore,
    PersonaStore,
    SupabasePersonaStore,
    UserId,
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
    "all_facts",
    "forgotten",
    "tombstone_key",
    "tombstone_keys",
    "note_key",
    "onboarding_key",
    "forget",
    "get_embedder",
    "set_embedder",
    "vectors",
    "knowledge_graph",
    "KnowledgeGraph",
    "GraphNode",
    "GraphEdge",
    "build_graph",
    "DEFAULT_MIN_SIMILARITY",
    "DEFAULT_MAX_LINKS",
]

_store: Optional[PersonaStore] = None
_embedder: Optional[Embedder] = None
# main.py warms these up on a background thread while requests are already being
# served, so construction must happen exactly once: two LocalEmbedders would load
# ~1.2 GB twice. Reentrant because get_store() calls get_embedder().
_init_lock = threading.RLock()


def get_embedder() -> Embedder:
    """The one embedding model for the process.

    Shared with store/notes on purpose: bge-large is ~1.2 GB resident, and a
    second LocalEmbedder would load it twice on a Cloud Run instance that only
    just fits one. Lazy for the same reason get_store() is.
    """
    global _embedder
    if _embedder is None:
        with _init_lock:
            if _embedder is None:
                _embedder = LocalEmbedder()
    return _embedder


def set_embedder(embedder: Embedder) -> None:
    """Swap the embedder (tests)."""
    global _embedder
    _embedder = embedder


def get_store() -> PersonaStore:
    """Return the active store, defaulting to Supabase + local embeddings.

    Constructing LocalEmbedder downloads the model on first use, so this stays
    lazy: nothing pays for Persona until something actually reads or writes it.
    """
    global _store
    if _store is None:
        with _init_lock:
            if _store is None:
                from app.core.db import get_client

                _store = SupabasePersonaStore(get_client(), get_embedder())
    return _store


def set_store(store: PersonaStore) -> None:
    """Swap the backend (tests, or a local store in V2)."""
    global _store
    _store = store


# Every function below is one user's: `user_id`
# comes from the verified token (core/auth.py, or core/request_user.py inside a
# tool), never from a request body. It is the first argument so forgetting it is
# a TypeError rather than a read of someone else's Persona.


def upsert(user_id: UserId, fact: Fact) -> str:
    """Store a new belief, or update one of this user's by id (FactNotFound if
    the id isn't theirs). Returns the fact id."""
    return get_store().upsert(user_id, fact)


def search(user_id: UserId, query: PersonaQuery) -> list[Match]:
    """Semantic search over this user's Persona, most-similar first."""
    return get_store().search(user_id, query)


def delete(user_id: UserId, fact_id: str) -> None:
    """Forget one belief, permanently (user data-agency / Privacy pillar).

    The text and embedding go; a tombstone naming the pattern stays, so
    consolidation cannot quietly re-derive what the user deleted. An id that
    isn't this user's is left alone.
    """
    get_store().delete(user_id, fact_id)


def forget(user_id: UserId, key: str) -> None:
    """Record a tombstone without deleting a fact - for when the thing that
    could regenerate a belief goes away before any belief does (a deleted
    note, say). See models.tombstone_keys."""
    get_store().forget(user_id, key)


def forgotten(user_id: UserId) -> set[str]:
    """Every pattern this user tombstoned - consolidation checks this before it writes."""
    return get_store().forgotten(user_id)


def get(user_id: UserId, fact_id: str) -> Fact:
    """Fetch one of this user's beliefs by id (FactNotFound otherwise)."""
    return get_store().get(user_id, fact_id)


def all_facts(user_id: UserId) -> list[Fact]:
    """Every one of this user's beliefs, newest first - enumeration rather than
    ranked search.

    What the Knowledge Map (Section 5.6) renders, and what consolidation reads
    to know which episodes it has already extracted.
    """
    return get_store().all_facts(user_id)


def vectors(user_id: UserId) -> dict[str, list[float]]:
    """Every one of this user's embeddings, by fact id - for fact-to-fact comparison."""
    return get_store().vectors(user_id)


def knowledge_graph(
    user_id: UserId,
    min_similarity: float = DEFAULT_MIN_SIMILARITY,
    max_links: int = DEFAULT_MAX_LINKS,
) -> KnowledgeGraph:
    """This user's Persona as a navigable graph for the Knowledge Map (Section 5.6)."""
    store = get_store()
    return build_graph(
        store.all_facts(user_id), store.vectors(user_id),
        min_similarity=min_similarity, max_links=max_links,
    )
