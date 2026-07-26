"""Persona store implementations.

`SupabasePersonaStore` is the real backend (Postgres + pgvector, semantic search
via the `match_persona` SQL function). `InMemoryPersonaStore`
is dependency-free used for tests without Supabase credentials.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Protocol, runtime_checkable

from .embeddings import Embedder
from .models import Fact, Match, PersonaQuery

TABLE = "persona"
MATCH_FN = "match_persona"


class FactNotFound(KeyError):
    """Raised when a fact id does not exist."""


@runtime_checkable
class PersonaStore(Protocol):
    def upsert(self, fact: Fact) -> str: ...
    def search(self, query: PersonaQuery) -> list[Match]: ...
    def delete(self, fact_id: str) -> None: ...
    def get(self, fact_id: str) -> Fact: ...


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    return max(0.0, min(1.0, dot))  # inputs are unit vectors; clamp fp drift


class SupabasePersonaStore:
    """Durable, vector-searchable Persona backed by the `persona` table."""

    def __init__(self, client, embedder: Embedder) -> None:
        self._db = client
        self._embed = embedder

    def upsert(self, fact: Fact) -> str:
        embedding = self._embed.embed([fact.text], input_type="document")[0]
        row = {
            "text": fact.text,
            "category": fact.category,
            "confidence": fact.confidence,
            "metadata": fact.metadata,
            "embedding": embedding,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        if fact.id:
            row["id"] = fact.id
        res = self._db.table(TABLE).upsert(row).execute()
        return res.data[0]["id"]

    def search(self, query: PersonaQuery) -> list[Match]:
        embedding = self._embed.embed([query.text], input_type="query")[0]
        res = self._db.rpc(
            MATCH_FN,
            {
                "query_embedding": embedding,
                "match_count": query.limit,
                "min_similarity": query.min_similarity,
                "filter_category": query.category,
            },
        ).execute()
        return [
            Match(fact=_fact_from_row(r), similarity=r["similarity"]) for r in res.data
        ]

    def delete(self, fact_id: str) -> None:
        """User data-agency: delete a stored belief (Privacy pillar, section 5.6)."""
        self._db.table(TABLE).delete().eq("id", fact_id).execute()

    def get(self, fact_id: str) -> Fact:
        res = self._db.table(TABLE).select("*").eq("id", fact_id).execute()
        if not res.data:
            raise FactNotFound(fact_id)
        return _fact_from_row(res.data[0])


def _fact_from_row(row: dict) -> Fact:
    return Fact(
        id=row["id"],
        text=row["text"],
        category=row.get("category") or [],
        confidence=row.get("confidence", 1.0),
        metadata=row.get("metadata") or {},
        created_at=row.get("created_at"),
        updated_at=row.get("updated_at"),
    )


class InMemoryPersonaStore:
    """Process-local fake with the same behaviour. No Supabase/model required."""

    def __init__(self, embedder: Embedder) -> None:
        self._embed = embedder
        self._facts: dict[str, Fact] = {}
        self._vecs: dict[str, list[float]] = {}

    def upsert(self, fact: Fact) -> str:
        fact_id = fact.id or str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        created = self._facts[fact_id].created_at if fact_id in self._facts else now
        stored = fact.model_copy(
            update={"id": fact_id, "created_at": created, "updated_at": now}
        )
        self._facts[fact_id] = stored
        self._vecs[fact_id] = self._embed.embed([fact.text], input_type="document")[0]
        return fact_id

    def search(self, query: PersonaQuery) -> list[Match]:
        qvec = self._embed.embed([query.text], input_type="query")[0]
        matches: list[Match] = []
        for fid, fact in self._facts.items():
            if query.category is not None and not _under(fact.category, query.category):
                continue
            sim = _cosine(qvec, self._vecs[fid])
            if sim >= query.min_similarity:
                matches.append(Match(fact=fact, similarity=sim))
        matches.sort(key=lambda m: m.similarity, reverse=True)
        return matches[: query.limit]

    def delete(self, fact_id: str) -> None:
        self._facts.pop(fact_id, None)
        self._vecs.pop(fact_id, None)

    def get(self, fact_id: str) -> Fact:
        if fact_id not in self._facts:
            raise FactNotFound(fact_id)
        return self._facts[fact_id]


def _under(category: list[str], prefix: list[str]) -> bool:
    """True if `category` sits at or below `prefix` in the ontology hierarchy."""
    return category[: len(prefix)] == prefix
