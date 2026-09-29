"""Persona store implementations.

`SupabasePersonaStore` is the real backend (Postgres + pgvector + full-text,
hybrid search via the `match_persona_hybrid` SQL function - see
db/schema.sql). `InMemoryPersonaStore` is dependency-free, used for tests
without Supabase credentials or the embedding model. Both rank with
ranking.rank(), era-memory's fusion.

These are the raw primitives. Writing a belief goes through
persona.remember() (reconcile.py), which uses them to keep the store free of
duplicates and contradictions; upsert() on its own checks neither.

Every method takes the account first: each user has
their own Persona, their own forgotten list, and an id that belongs to someone
else behaves exactly like one that doesn't exist (FactNotFound, so a 404).
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, ContextManager, Iterable, Iterator, Optional, Protocol, Union, runtime_checkable
from uuid import UUID

from app.store.persona import ranking
from app.store.persona.embeddings import Embedder
from app.store.persona.models import (
    Fact,
    Match,
    PersonaQuery,
    content_hash,
    source_keys,
    tombstone_keys,
)

TABLE = "persona"
MATCH_FN = "match_persona_hybrid"

# Deletions that have to stick. One row per forgotten pattern, holding its
# identity and nothing else - see models.tombstone_key.
FORGOTTEN_TABLE = "persona_forgotten"

# Supersession watermarks: "a newer belief won over this pattern as of T".
# Not a tombstone - a trend that loses can win back with newer evidence. See
# reconcile.py.
SUPERSEDED_TABLE = "persona_superseded"

# Cross-instance per-user write lease, taken around remember()'s
# read -> judge -> write. Cloud Run runs several instances, and PostgREST gives
# no session to hold an advisory lock in. The TTL outlives the judge's batch
# timeout plus the writes, so a lease can't lapse mid-write.
LOCK_FN = "persona_lock"
UNLOCK_FN = "persona_unlock"
LOCK_WAIT_S = 3.0
LOCK_TTL_MS = 15_000

# How many rows each half of a hybrid search contributes before fusion.
SEARCH_POOL = 20

_FIELDS = "id,created_at,updated_at,stated_at,text,category,confidence,metadata"

UserId = Union[UUID, str]


class FactNotFound(KeyError):
    """Raised when a fact id does not exist."""


class DuplicateHash(ValueError):
    """A write collided with the (user_id, content_hash) unique index - a
    concurrent write of the same belief. reconcile retries it as a merge."""

    def __init__(self, existing_id: Optional[str]) -> None:
        super().__init__(existing_id)
        self.existing_id = existing_id


@runtime_checkable
class PersonaStore(Protocol):
    def upsert(self, user_id: UserId, fact: Fact, *, embedding: Optional[list[float]] = None) -> str: ...
    def embed(self, text: str) -> list[float]: ...
    def search(self, user_id: UserId, query: PersonaQuery) -> list[Match]: ...
    def candidates(self, user_id: UserId, text: str, embedding: list[float], *,
                   limit: int, exclude_ids: Iterable[str] = ()) -> list[Match]: ...
    def find_by_hash(self, user_id: UserId, digest: str) -> list[Fact]: ...
    def find_by_source_key(self, user_id: UserId, key: str) -> list[Fact]: ...
    def delete(self, user_id: UserId, fact_id: str) -> None: ...
    def remove(self, user_id: UserId, fact_id: str) -> None: ...
    def get(self, user_id: UserId, fact_id: str) -> Fact: ...
    def all_facts(self, user_id: UserId) -> list[Fact]: ...
    def vectors(self, user_id: UserId) -> dict[str, list[float]]: ...
    def versions(self, user_id: UserId) -> dict[str, str]: ...
    def forgotten(self, user_id: UserId) -> set[str]: ...
    def forget(self, user_id: UserId, key: str) -> None: ...
    def supersede(self, user_id: UserId, key: str, at: datetime, by_fact_id: Optional[str]) -> None: ...
    def superseded(self, user_id: UserId) -> dict[str, datetime]: ...
    def lock(self, user_id: UserId) -> ContextManager[bool]: ...
    def backfill_keys(self, user_id: UserId) -> int: ...


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    return max(0.0, min(1.0, dot))  # inputs are unit vectors; clamp fp drift


class SupabasePersonaStore:
    """Durable, hybrid-searchable Persona backed by the `persona` table."""

    def __init__(self, client, embedder: Embedder) -> None:
        self._db = client
        self._embed = embedder
        self._local_locks: dict[str, threading.Lock] = {}
        self._local_locks_guard = threading.Lock()

    def embed(self, text: str) -> list[float]:
        return self._embed.embed([text], input_type="document")[0]

    def upsert(self, user_id: UserId, fact: Fact, *, embedding: Optional[list[float]] = None) -> str:
        """Insert a new belief, or rewrite one of this user's by id.

        Never an upsert keyed by the id it was handed: the id can come
        from a client (PATCH /persona/{id}), and an upsert with the service key
        would overwrite - and take over - whoever's row that is. An id that
        isn't one of this user's raises FactNotFound instead.

        `embedding` skips re-embedding when the caller already has it
        (remember() embeds once for both the candidate search and the write).
        """
        now = datetime.now(timezone.utc)
        row = {
            "text": fact.text,
            "category": fact.category,
            "confidence": fact.confidence,
            "metadata": fact.metadata,
            "embedding": embedding if embedding is not None else self.embed(fact.text),
            "content_hash": content_hash(fact.text),
            "source_keys": source_keys(fact.metadata or {}),
            "updated_at": now.isoformat(),
        }
        if fact.stated_at is not None:
            row["stated_at"] = _iso(fact.stated_at)
        if fact.id:
            res = self._attempt(lambda: (
                self._db.table(TABLE).update(row)
                .eq("id", fact.id).eq("user_id", str(user_id))
                .execute()
            ), user_id, row["content_hash"])
            if not res.data:
                raise FactNotFound(fact.id)
            return res.data[0]["id"]
        row.setdefault("stated_at", now.isoformat())
        res = self._attempt(
            lambda: self._db.table(TABLE).insert({**row, "user_id": str(user_id)}).execute(),
            user_id, row["content_hash"],
        )
        return res.data[0]["id"]

    def _attempt(self, write, user_id: UserId, digest: str):
        """Run a write, turning a content_hash unique violation into
        DuplicateHash naming the row that already holds it."""
        try:
            return write()
        except Exception as e:
            if getattr(e, "code", None) != "23505" and "persona_user_hash_uidx" not in str(e):
                raise
            held = self.find_by_hash(user_id, digest)
            raise DuplicateHash(held[0].id if held else None) from e

    def _hybrid(self, user_id: UserId, text: str, embedding: list[float], *, match_count: int,
                min_similarity: float, category: Optional[list[str]], lexical_any: bool,
                exclude_ids: Iterable[str] = ()) -> list[Match]:
        res = self._db.rpc(
            MATCH_FN,
            {
                "filter_user": str(user_id),
                "query_embedding": embedding,
                "query_text": text,
                "match_count": match_count,
                "min_similarity": min_similarity,
                "filter_category": category,
                "lexical_any": lexical_any,
                "exclude_ids": [str(i) for i in exclude_ids],
            },
        ).execute()
        return [
            Match(fact=_fact_from_row(r), similarity=float(r.get("similarity") or 0.0),
                  lexical=float(r.get("lexical") or 0.0))
            for r in res.data
        ]

    def search(self, user_id: UserId, query: PersonaQuery) -> list[Match]:
        """Hybrid search: pgvector nearest neighbours and Postgres full-text,
        fused by ranking.rank(). `similarity` on each result is still the
        cosine; the order is the fused score."""
        embedding = self._embed.embed([query.text], input_type="query")[0]
        rows = self._hybrid(
            user_id, query.text, embedding,
            match_count=max(SEARCH_POOL, query.limit * 2),
            min_similarity=query.min_similarity, category=query.category, lexical_any=False,
        )
        return ranking.rank(rows, min_similarity=query.min_similarity, limit=query.limit)

    def candidates(self, user_id: UserId, text: str, embedding: list[float], *,
                   limit: int, exclude_ids: Iterable[str] = ()) -> list[Match]:
        """Beliefs a new statement might duplicate or contradict: every
        category, keyword terms ORed, nothing thresholded (reconcile filters).
        `embedding` is the statement's document embedding - fact vs fact."""
        return self._hybrid(
            user_id, text, embedding, match_count=limit, min_similarity=0.0,
            category=None, lexical_any=True, exclude_ids=exclude_ids,
        )

    def find_by_hash(self, user_id: UserId, digest: str) -> list[Fact]:
        res = (
            self._db.table(TABLE).select(_FIELDS)
            .eq("user_id", str(user_id)).eq("content_hash", digest)
            .execute()
        )
        return [_fact_from_row(r) for r in res.data]

    def find_by_source_key(self, user_id: UserId, key: str) -> list[Fact]:
        res = (
            self._db.table(TABLE).select(_FIELDS)
            .eq("user_id", str(user_id)).contains("source_keys", [key])
            .execute()
        )
        return [_fact_from_row(r) for r in res.data]

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

        self.remove(user_id, fact_id)

    def remove(self, user_id: UserId, fact_id: str) -> None:
        """Delete a row with no tombstone - a belief superseded or merged
        away by remember(), not one the user asked to forget."""
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

    def supersede(self, user_id: UserId, key: str, at: datetime, by_fact_id: Optional[str]) -> None:
        """Record that a newer belief won over `key` as of `at`, keeping the
        later watermark if one is already there."""
        current = self.superseded(user_id).get(key)
        if current is not None and current >= _aware(at):
            return
        self._db.table(SUPERSEDED_TABLE).upsert(
            {"user_id": str(user_id), "key": key, "superseded_at": _iso(at),
             "by_fact_id": by_fact_id},
            on_conflict="user_id,key",
        ).execute()

    def superseded(self, user_id: UserId) -> dict[str, datetime]:
        res = (
            self._db.table(SUPERSEDED_TABLE).select("key,superseded_at")
            .eq("user_id", str(user_id)).execute()
        )
        return {r["key"]: _parse_time(r["superseded_at"]) for r in res.data}

    @contextmanager
    def lock(self, user_id: UserId) -> Iterator[bool]:
        """Hold this user's Persona for one read -> judge -> write.

        A per-process lock first, so one instance doesn't contend with itself,
        then the database lease so two instances don't interleave. Yields
        False if the lease could not be had in LOCK_WAIT_S - the caller writes
        anyway, marked unreconciled, rather than drop what the user said. If
        the lease function isn't there yet (schema.sql not re-run), degrades
        to the process lock with a warning.
        """
        local = self._local_lock(user_id)
        if not local.acquire(timeout=LOCK_WAIT_S):
            yield False
            return
        holder = uuid.uuid4().hex
        leased = False
        try:
            deadline = time.monotonic() + LOCK_WAIT_S
            degraded = False
            while True:
                try:
                    res = self._db.rpc(LOCK_FN, {"p_user": str(user_id), "p_holder": holder,
                                                  "p_ttl_ms": LOCK_TTL_MS}).execute()
                    leased = bool(res.data)
                except Exception as e:
                    print(f"[persona] WARNING: write lease unavailable ({e}); "
                          f"process lock only. Run db/schema.sql.")
                    degraded = True
                    break
                if leased or time.monotonic() >= deadline:
                    break
                time.sleep(0.05)
            yield leased or degraded
        finally:
            if leased:
                try:
                    self._db.rpc(UNLOCK_FN, {"p_user": str(user_id), "p_holder": holder}).execute()
                except Exception as e:
                    print(f"[persona] lease release failed (it expires on its own): {e}")
            local.release()

    def _local_lock(self, user_id: UserId) -> threading.Lock:
        with self._local_locks_guard:
            return self._local_locks.setdefault(str(user_id), threading.Lock())

    def backfill_keys(self, user_id: UserId) -> int:
        """Fill content_hash and source_keys on rows written before they
        existed. Done in Python so the normalisation is exactly content_hash()'s."""
        res = (
            self._db.table(TABLE).select("id,text,metadata")
            .eq("user_id", str(user_id)).is_("content_hash", "null").execute()
        )
        for r in res.data:
            self._db.table(TABLE).update({
                "content_hash": content_hash(r["text"]),
                "source_keys": source_keys(r.get("metadata") or {}),
            }).eq("id", r["id"]).eq("user_id", str(user_id)).execute()
        return len(res.data)

    def get(self, user_id: UserId, fact_id: str) -> Fact:
        res = (
            self._db.table(TABLE)
            .select(_FIELDS)
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
            .select(_FIELDS)
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
        stated_at=row.get("stated_at"),
        created_at=row.get("created_at"),
        updated_at=row.get("updated_at"),
    )


def _aware(when: datetime) -> datetime:
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


def _iso(when: datetime) -> str:
    return _aware(when).isoformat()


def _parse_time(value: Any) -> datetime:
    if isinstance(value, datetime):
        return _aware(value)
    return _aware(datetime.fromisoformat(str(value).replace("Z", "+00:00")))


class InMemoryPersonaStore:
    """Process-local fake with the same behaviour, one Persona per user. No
    Supabase/model required."""

    # Mirrors the persona_user_hash_uidx index, which only exists once the
    # sweep has cleaned a store. Off by default so tests can seed duplicates
    # the way pre-sweep data holds them.
    unique_hash = False

    def __init__(self, embedder: Embedder) -> None:
        self._embed = embedder
        self._facts: dict[str, Fact] = {}
        self._owner: dict[str, str] = {}
        self._vecs: dict[str, list[float]] = {}
        self._forgotten: dict[str, set[str]] = {}
        self._superseded: dict[str, dict[str, datetime]] = {}
        self._locks: dict[str, threading.RLock] = {}
        self._guard = threading.RLock()

    def _mine(self, user_id: UserId) -> dict[str, Fact]:
        uid = str(user_id)
        return {fid: f for fid, f in self._facts.items() if self._owner[fid] == uid}

    def embed(self, text: str) -> list[float]:
        return self._embed.embed([text], input_type="document")[0]

    def upsert(self, user_id: UserId, fact: Fact, *, embedding: Optional[list[float]] = None) -> str:
        with self._guard:
            if fact.id and fact.id not in self._mine(user_id):
                raise FactNotFound(fact.id)  # someone else's, or no such fact - as SupabasePersonaStore
            if self.unique_hash:
                clash = [f for f in self.find_by_hash(user_id, content_hash(fact.text)) if f.id != fact.id]
                if clash:
                    raise DuplicateHash(clash[0].id)
            fact_id = fact.id or str(uuid.uuid4())
            now = datetime.now(timezone.utc)
            previous = self._facts.get(fact_id)
            if previous and previous.updated_at and now <= previous.updated_at:
                # A coarse clock can hand two quick writes the same instant; versions() must still differ.
                now = previous.updated_at + timedelta(microseconds=1)
            created = previous.created_at if previous else now
            stated = fact.stated_at or (previous.stated_at if previous else None) or now
            stored = fact.model_copy(
                update={"id": fact_id, "created_at": created, "updated_at": now,
                        "stated_at": _aware(stated)}
            )
            self._facts[fact_id] = stored
            self._owner[fact_id] = str(user_id)
            self._vecs[fact_id] = embedding if embedding is not None else self.embed(fact.text)
            return fact_id

    def _raw(self, user_id: UserId, text: str, qvec: list[float], category: Optional[list[str]],
             any_terms: bool, exclude_ids: Iterable[str] = ()) -> list[Match]:
        excluded = {str(i) for i in exclude_ids}
        out: list[Match] = []
        for fid, fact in self._mine(user_id).items():
            if fid in excluded:
                continue
            if category is not None and not _under(fact.category, category):
                continue
            out.append(Match(
                fact=fact,
                similarity=_cosine(qvec, self._vecs[fid]),
                lexical=ranking.lexical_score(text, fact.text, any_terms=any_terms),
            ))
        return out

    def search(self, user_id: UserId, query: PersonaQuery) -> list[Match]:
        qvec = self._embed.embed([query.text], input_type="query")[0]
        rows = self._raw(user_id, query.text, qvec, query.category, any_terms=False)
        return ranking.rank(rows, min_similarity=query.min_similarity, limit=query.limit)

    def candidates(self, user_id: UserId, text: str, embedding: list[float], *,
                   limit: int, exclude_ids: Iterable[str] = ()) -> list[Match]:
        rows = self._raw(user_id, text, embedding, None, any_terms=True, exclude_ids=exclude_ids)
        by_sim = sorted(rows, key=lambda m: m.similarity, reverse=True)[:limit]
        by_lex = sorted((m for m in rows if m.lexical > 0), key=lambda m: m.lexical, reverse=True)[:limit]
        union: dict[str, Match] = {}
        for m in [*by_sim, *by_lex]:
            union.setdefault(m.fact.id, m)
        return list(union.values())

    def find_by_hash(self, user_id: UserId, digest: str) -> list[Fact]:
        return [f for f in self._mine(user_id).values() if content_hash(f.text) == digest]

    def find_by_source_key(self, user_id: UserId, key: str) -> list[Fact]:
        return [f for f in self._mine(user_id).values() if key in source_keys(f.metadata or {})]

    def delete(self, user_id: UserId, fact_id: str) -> None:
        if fact_id not in self._mine(user_id):
            return
        fact = self._facts[fact_id]
        self.remove(user_id, fact_id)
        for key in tombstone_keys(fact.metadata):
            self.forget(user_id, key)

    def remove(self, user_id: UserId, fact_id: str) -> None:
        if fact_id not in self._mine(user_id):
            return
        self._facts.pop(fact_id)
        self._owner.pop(fact_id)
        self._vecs.pop(fact_id, None)

    def forget(self, user_id: UserId, key: str) -> None:
        self._forgotten.setdefault(str(user_id), set()).add(key)

    def forgotten(self, user_id: UserId) -> set[str]:
        return set(self._forgotten.get(str(user_id), set()))

    def supersede(self, user_id: UserId, key: str, at: datetime, by_fact_id: Optional[str]) -> None:
        marks = self._superseded.setdefault(str(user_id), {})
        at = _aware(at)
        if key not in marks or marks[key] < at:
            marks[key] = at

    def superseded(self, user_id: UserId) -> dict[str, datetime]:
        return dict(self._superseded.get(str(user_id), {}))

    @contextmanager
    def lock(self, user_id: UserId) -> Iterator[bool]:
        with self._guard:
            lock = self._locks.setdefault(str(user_id), threading.RLock())
        with lock:
            yield True

    def backfill_keys(self, user_id: UserId) -> int:
        return 0  # hashes and keys are computed on read here

    def load(self, user_id: UserId, facts: list[Fact], vectors: dict[str, list[float]],
             superseded: Optional[dict[str, datetime]] = None) -> None:
        """Seed from another store's contents, ids and times preserved - how
        reconcile_all(dry_run=True) simulates a sweep without touching the real one."""
        for fact in facts:
            self._facts[fact.id] = fact
            self._owner[fact.id] = str(user_id)
            self._vecs[fact.id] = vectors.get(fact.id) or self.embed(fact.text)
        if superseded:
            self._superseded[str(user_id)] = dict(superseded)

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
