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
    fact_id = upsert(Fact(
        text="likes bagels",
        category=["opinions", "likes", "food"],
    ))

    # semantic recall
    for m in search(PersonaQuery(text="what food does the user like", limit=5)):
        print(m.similarity, m.fact.text)

    # correction loop (Section 5.5 done-when (b)): a contradicting episode
    # finds the belief and updates it in place by re-upserting its id.
    upsert(Fact(id=fact_id, text="dislikes bagels",
                category=["opinions", "likes", "food"]))

By default this talks to Supabase and embeds locally with fastembed. Tests (or
a run before the model is downloaded) can swap the backend with
`set_store(InMemoryPersonaStore(FakeEmbedder()))`.

WHO USES THIS
- Jay: tools/memory_tool.py promotes saved notes here and recalls from here
- Knowledge Map (Section 5.6): reads/edits/deletes beliefs through search/get/delete
"""
from __future__ import annotations

from typing import Optional

from .embeddings import Embedder, FakeEmbedder, LocalEmbedder
from .graph import (
    DEFAULT_MAX_LINKS,
    DEFAULT_MIN_SIMILARITY,
    GraphEdge,
    GraphNode,
    KnowledgeGraph,
    build_graph,
)
from .models import Fact, Match, PersonaQuery, tombstone_key
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
    "all_facts",
    "forgotten",
    "tombstone_key",
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


def get_store() -> PersonaStore:
    """Return the active store, defaulting to Supabase + local embeddings.

    Constructing LocalEmbedder downloads the model on first use, so this stays
    lazy: nothing pays for Persona until something actually reads or writes it.
    """
    global _store
    if _store is None:
        # Importable both as `app.persona` (scripts/tests) and bare `persona`
        # (the server, launched as uvicorn main:app from backend/app/).
        try:
            from ..db import get_client      # app.persona
        except ImportError:                  # pragma: no cover
            from db import get_client        # persona (cwd = backend/app)

        _store = SupabasePersonaStore(get_client(), LocalEmbedder())
    return _store


def set_store(store: PersonaStore) -> None:
    """Swap the backend (tests, or a local store in V2)."""
    global _store
    _store = store


def upsert(fact: Fact) -> str:
    """Store or update a durable belief. Returns the fact id."""
    return get_store().upsert(fact)


def search(query: PersonaQuery) -> list[Match]:
    """Semantic search over the Persona, most-similar first."""
    return get_store().search(query)


def delete(fact_id: str) -> None:
    """Forget one belief, permanently (user data-agency / Privacy pillar).

    The text and embedding go; a tombstone naming the pattern stays, so
    consolidation cannot quietly re-derive what the user deleted.
    """
    get_store().delete(fact_id)


def forgotten() -> set[str]:
    """Every tombstoned pattern - consolidation checks this before it writes."""
    return get_store().forgotten()


def get(fact_id: str) -> Fact:
    """Fetch one belief by id."""
    return get_store().get(fact_id)


def all_facts() -> list[Fact]:
    """Every belief, newest first - enumeration rather than ranked search.

    What the Knowledge Map (Section 5.6) renders, and what consolidation reads
    to know which episodes it has already extracted.
    """
    return get_store().all_facts()


def vectors() -> dict[str, list[float]]:
    """Every stored embedding, by fact id - for fact-to-fact comparison."""
    return get_store().vectors()


def knowledge_graph(
    min_similarity: float = DEFAULT_MIN_SIMILARITY,
    max_links: int = DEFAULT_MAX_LINKS,
) -> KnowledgeGraph:
    """Persona as a navigable graph for the Knowledge Map (Section 5.6)."""
    store = get_store()
    return build_graph(
        store.all_facts(), store.vectors(),
        min_similarity=min_similarity, max_links=max_links,
    )
