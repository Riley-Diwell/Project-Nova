"""Persona store implementations.

`SupabasePersonaStore` is the real backend (Postgres + pgvector, semantic search
via the `match_persona` SQL function - see backend/db/schema.sql).
`InMemoryPersonaStore` is dependency-free, used for tests without Supabase
credentials or the embedding model.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Protocol, runtime_checkable

from .embeddings import Embedder
from .models import Fact, Match, PersonaQuery, tombstone_key

TABLE = "persona"
MATCH_FN = "match_persona"

# Deletions that have to stick. One row per forgotten pattern, holding its
# identity and nothing else - see models.tombstone_key.
FORGOTTEN_TABLE = "persona_forgotten"


class FactNotFound(KeyError):
    """Raised when a fact id does not exist."""


@runtime_checkable
class PersonaStore(Protocol):
    def upsert(self, fact: Fact) -> str: ...
    def search(self, query: PersonaQuery) -> list[Match]: ...
    def delete(self, fact_id: str) -> None: ...
    def get(self, fact_id: str) -> Fact: ...
    def all_facts(self) -> list[Fact]: ...
    def vectors(self) -> dict[str, list[float]]: ...
    def forgotten(self) -> set[str]: ...


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
        """Forget a belief, and keep having forgotten it.

        User data-agency (Privacy pillar, Section 5.6). The text and its
        embedding really are deleted - this is not a hidden row. What is kept is
        a tombstone naming the pattern, so consolidation does not re-derive the
        belief on its next run and hand the user back the thing they just threw
        away. See docs/adr/0003.
        """
        try:
            key = tombstone_key(self.get(fact_id).metadata)
        except FactNotFound:
            key = None  # already gone; nothing to remember about it

        if key:
            try:
                self._db.table(FORGOTTEN_TABLE).upsert({"key": key}).execute()
            except Exception as e:
                # The deletion still happens. The user asked for this belief to
                # go, and refusing because the tombstone could not be recorded
                # would leave it on screen - the worse of the two failures. What
                # is lost is durability: consolidation may re-derive this pattern
                # on a later run. The usual cause is persona_forgotten not
                # existing yet, so say so rather than degrading quietly.
                print(f"[persona] WARNING: tombstone not recorded for {key!r} "
                      f"({e}). Deleting anyway - this belief may come back on the "
                      f"next consolidation run. Run backend/db/schema.sql.")

        self._db.table(TABLE).delete().eq("id", fact_id).execute()

    def forgotten(self) -> set[str]:
        """Every tombstoned pattern - what consolidation must not write again."""
        res = self._db.table(FORGOTTEN_TABLE).select("key").execute()
        return {r["key"] for r in res.data}

    def get(self, fact_id: str) -> Fact:
        res = self._db.table(TABLE).select("*").eq("id", fact_id).execute()
        if not res.data:
            raise FactNotFound(fact_id)
        return _fact_from_row(res.data[0])

    def all_facts(self) -> list[Fact]:
        """Every belief, newest first. Enumeration, not search.

        search() ranks by similarity and takes a limit, so it can never answer
        "what is in here?" exactly - which is what app/consolidation needs to
        know which episodes it has already extracted, and what the Knowledge
        Map (Section 5.6) needs to render the store back to the user. The
        embedding column is left out: nothing outside the store reads it and it
        is 1024 floats a row.
        """
        res = (
            self._db.table(TABLE)
            .select("id,created_at,updated_at,text,category,confidence,metadata")
            .order("updated_at", desc=True)
            .execute()
        )
        return [_fact_from_row(r) for r in res.data]

    def vectors(self) -> dict[str, list[float]]:
        """Every stored embedding, by fact id.

        The Knowledge Map needs fact-to-fact similarity, which search() cannot
        give: it ranks the store against an outside query, not against itself.
        Pulling the vectors once and comparing in Python beats one round trip
        per fact, at the sizes a single user's Persona reaches.
        """
        res = self._db.table(TABLE).select("id,embedding").execute()
        return {
            r["id"]: vec
            for r in res.data
            if (vec := _as_vector(r.get("embedding")))
        }


def _as_vector(value: Any) -> list[float]:
    """pgvector comes back over PostgREST as a JSON string ('[0.1,0.2,...]')
    rather than an array, so accept either."""
    if isinstance(value, list):
        return [float(x) for x in value]
    if isinstance(value, str) and value.strip().startswith("["):
        try:
            return [float(x) for x in json.loads(value)]
        except (ValueError, TypeError):
            return []
    return []


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
        self._forgotten: set[str] = set()

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
        fact = self._facts.pop(fact_id, None)
        self._vecs.pop(fact_id, None)
        if fact and (key := tombstone_key(fact.metadata)):
            self._forgotten.add(key)

    def forgotten(self) -> set[str]:
        return set(self._forgotten)

    def get(self, fact_id: str) -> Fact:
        if fact_id not in self._facts:
            raise FactNotFound(fact_id)
        return self._facts[fact_id]

    def all_facts(self) -> list[Fact]:
        return sorted(
            self._facts.values(),
            key=lambda f: f.updated_at or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )

    def vectors(self) -> dict[str, list[float]]:
        return dict(self._vecs)


def _under(category: list[str], prefix: list[str]) -> bool:
    """True if `category` sits at or below `prefix` in the ontology hierarchy."""
    return category[: len(prefix)] == prefix
