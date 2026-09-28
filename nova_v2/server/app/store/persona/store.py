"""Persona store implementations.

`SupabasePersonaStore` is the real backend (Postgres + pgvector, semantic search
via the `match_persona` SQL function - see backend/db/schema.sql).
`InMemoryPersonaStore` is dependency-free, used for tests without Supabase
credentials or the embedding model.

Every method takes the account first: each user has
their own Persona, their own forgotten list, and an id that belongs to someone
else behaves exactly like one that doesn't exist (FactNotFound, so a 404).
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol, Union, runtime_checkable
from uuid import UUID

from app.store.persona.embeddings import Embedder
from app.store.persona.models import Fact, Match, PersonaQuery, tombstone_keys

TABLE = "persona"
MATCH_FN = "match_persona"

# Deletions that have to stick. One row per forgotten pattern, holding its
# identity and nothing else - see models.tombstone_key.
FORGOTTEN_TABLE = "persona_forgotten"

UserId = Union[UUID, str]


class FactNotFound(KeyError):
    """Raised when a fact id does not exist."""


@runtime_checkable
class PersonaStore(Protocol):
    def upsert(self, user_id: UserId, fact: Fact) -> str: ...
    def search(self, user_id: UserId, query: PersonaQuery) -> list[Match]: ...
    def delete(self, user_id: UserId, fact_id: str) -> None: ...
    def get(self, user_id: UserId, fact_id: str) -> Fact: ...
    def all_facts(self, user_id: UserId) -> list[Fact]: ...
    def vectors(self, user_id: UserId) -> dict[str, list[float]]: ...
    def versions(self, user_id: UserId) -> dict[str, str]: ...
    def forgotten(self, user_id: UserId) -> set[str]: ...
    def forget(self, user_id: UserId, key: str) -> None: ...


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    return max(0.0, min(1.0, dot))  # inputs are unit vectors; clamp fp drift


class SupabasePersonaStore:
    """Durable, vector-searchable Persona backed by the `persona` table."""

    def __init__(self, client, embedder: Embedder) -> None:
        self._db = client
        self._embed = embedder

    def upsert(self, user_id: UserId, fact: Fact) -> str:
        """Insert a new belief, or rewrite one of this user's by id.

        Never an upsert keyed by the id it was handed: the id can come
        from a client (PATCH /persona/{id}), and an upsert with the service key
        would overwrite - and take over - whoever's row that is. An id that
        isn't one of this user's raises FactNotFound instead.
        """
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
            res = (
                self._db.table(TABLE).update(row)
                .eq("id", fact.id).eq("user_id", str(user_id))
                .execute()
            )
            if not res.data:
                raise FactNotFound(fact.id)
            return res.data[0]["id"]
        res = self._db.table(TABLE).insert({**row, "user_id": str(user_id)}).execute()
        return res.data[0]["id"]

    def search(self, user_id: UserId, query: PersonaQuery) -> list[Match]:
        embedding = self._embed.embed([query.text], input_type="query")[0]
        res = self._db.rpc(
            MATCH_FN,
            {
                "filter_user": str(user_id),
                "query_embedding": embedding,
                "match_count": query.limit,
                "min_similarity": query.min_similarity,
                "filter_category": query.category,
            },
        ).execute()
        return [
            Match(fact=_fact_from_row(r), similarity=r["similarity"]) for r in res.data
        ]

    def delete(self, user_id: UserId, fact_id: str) -> None:
        """Forget a belief, and keep having forgotten it.

        User data-agency (Privacy pillar, Section 5.6). The text and its
        embedding really are deleted - this is not a hidden row. What is kept is
        a tombstone naming the pattern, so consolidation does not re-derive the
        belief on its next run and hand the user back the thing they just threw
        away. See docs/adr/0003.
        """
        try:
            keys = tombstone_keys(self.get(user_id, fact_id).metadata)
        except FactNotFound:
            # Already gone, or never this user's: nothing to remember, and
            # nothing of theirs to delete.
            return

        for key in keys:
            self.forget(user_id, key)

        self._db.table(TABLE).delete().eq("id", fact_id).eq("user_id", str(user_id)).execute()

    def forget(self, user_id: UserId, key: str) -> None:
        """Record one tombstone. Best-effort - see the comment inside."""
        try:
            self._db.table(FORGOTTEN_TABLE).upsert(
                {"user_id": str(user_id), "key": key}, on_conflict="user_id,key"
            ).execute()
        except Exception as e:
            # The deletion still happens. The user asked for this belief to
            # go, and refusing because the tombstone could not be recorded
            # would leave it on screen - the worse of the two failures. What
            # is lost is durability: consolidation may re-derive this pattern
            # on a later run. The usual cause is persona_forgotten not
            # existing yet, so say so rather than degrading quietly.
            print(f"[persona] WARNING: tombstone not recorded for {key!r} "
                  f"({e}). Deleting anyway - this belief may come back on the "
                  f"next consolidation run. Run db/schema.sql.")

    def forgotten(self, user_id: UserId) -> set[str]:
        """Every pattern this user tombstoned - what consolidation must not write again."""
        res = self._db.table(FORGOTTEN_TABLE).select("key").eq("user_id", str(user_id)).execute()
        return {r["key"] for r in res.data}

    def get(self, user_id: UserId, fact_id: str) -> Fact:
        res = (
            self._db.table(TABLE)
            .select("id,created_at,updated_at,text,category,confidence,metadata")
            .eq("id", fact_id).eq("user_id", str(user_id))
            .execute()
        )
        if not res.data:
            raise FactNotFound(fact_id)
        return _fact_from_row(res.data[0])

    def all_facts(self, user_id: UserId) -> list[Fact]:
        """Every one of this user's beliefs, newest first. Enumeration, not search.

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
            .eq("user_id", str(user_id))
            .order("updated_at", desc=True)
            .execute()
        )
        return [_fact_from_row(r) for r in res.data]

    def vectors(self, user_id: UserId) -> dict[str, list[float]]:
        """Every one of this user's stored embeddings, by fact id.

        The Knowledge Map needs fact-to-fact similarity, which search() cannot
        give: it ranks the store against an outside query, not against itself.
        Pulling the vectors once and comparing in Python beats one round trip
        per fact, at the sizes a single user's Persona reaches.
        """
        res = self._db.table(TABLE).select("id,embedding").eq("user_id", str(user_id)).execute()
        return {
            r["id"]: vec
            for r in res.data
            if (vec := _as_vector(r.get("embedding")))
        }


    def versions(self, user_id: UserId) -> dict[str, str]:
        """Every one of this user's fact ids with when it last changed - and nothing else.

        What the Knowledge Map's ETag is made from (persona.graph_etag): every
        write goes through upsert(), which stamps updated_at, and a delete
        removes the row, so this changes whenever the graph could. Two short
        columns a row, where the graph itself needs every embedding.
        """
        res = self._db.table(TABLE).select("id,updated_at").eq("user_id", str(user_id)).execute()
        return {r["id"]: str(r.get("updated_at")) for r in res.data}


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
    """Process-local fake with the same behaviour, one Persona per user. No
    Supabase/model required."""

    def __init__(self, embedder: Embedder) -> None:
        self._embed = embedder
        self._facts: dict[str, Fact] = {}
        self._owner: dict[str, str] = {}
        self._vecs: dict[str, list[float]] = {}
        self._forgotten: dict[str, set[str]] = {}

    def _mine(self, user_id: UserId) -> dict[str, Fact]:
        uid = str(user_id)
        return {fid: f for fid, f in self._facts.items() if self._owner[fid] == uid}

    def upsert(self, user_id: UserId, fact: Fact) -> str:
        if fact.id and fact.id not in self._mine(user_id):
            raise FactNotFound(fact.id)  # someone else's, or no such fact - as SupabasePersonaStore
        fact_id = fact.id or str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        previous = self._facts.get(fact_id)
        if previous and previous.updated_at and now <= previous.updated_at:
            # A coarse clock can hand two quick writes the same instant; versions() must still differ.
            now = previous.updated_at + timedelta(microseconds=1)
        created = previous.created_at if previous else now
        stored = fact.model_copy(
            update={"id": fact_id, "created_at": created, "updated_at": now}
        )
        self._facts[fact_id] = stored
        self._owner[fact_id] = str(user_id)
        self._vecs[fact_id] = self._embed.embed([fact.text], input_type="document")[0]
        return fact_id

    def search(self, user_id: UserId, query: PersonaQuery) -> list[Match]:
        qvec = self._embed.embed([query.text], input_type="query")[0]
        matches: list[Match] = []
        for fid, fact in self._mine(user_id).items():
            if query.category is not None and not _under(fact.category, query.category):
                continue
            sim = _cosine(qvec, self._vecs[fid])
            if sim >= query.min_similarity:
                matches.append(Match(fact=fact, similarity=sim))
        matches.sort(key=lambda m: m.similarity, reverse=True)
        return matches[: query.limit]

    def delete(self, user_id: UserId, fact_id: str) -> None:
        if fact_id not in self._mine(user_id):
            return
        fact = self._facts.pop(fact_id)
        self._owner.pop(fact_id)
        self._vecs.pop(fact_id, None)
        for key in tombstone_keys(fact.metadata):
            self.forget(user_id, key)

    def forget(self, user_id: UserId, key: str) -> None:
        self._forgotten.setdefault(str(user_id), set()).add(key)

    def forgotten(self, user_id: UserId) -> set[str]:
        return set(self._forgotten.get(str(user_id), set()))

    def get(self, user_id: UserId, fact_id: str) -> Fact:
        mine = self._mine(user_id)
        if fact_id not in mine:
            raise FactNotFound(fact_id)
        return mine[fact_id]

    def all_facts(self, user_id: UserId) -> list[Fact]:
        return sorted(
            self._mine(user_id).values(),
            key=lambda f: f.updated_at or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )

    def vectors(self, user_id: UserId) -> dict[str, list[float]]:
        return {fid: self._vecs[fid] for fid in self._mine(user_id)}

    def versions(self, user_id: UserId) -> dict[str, str]:
        return {fid: str(f.updated_at) for fid, f in self._mine(user_id).items()}


def _under(category: list[str], prefix: list[str]) -> bool:
    """True if `category` sits at or below `prefix` in the ontology hierarchy."""
    return category[: len(prefix)] == prefix
