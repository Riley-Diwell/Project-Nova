"""Notes store implementations.

`SupabaseNotesStore` is the real backend (the `notes` and `note_chunks` tables
plus match_notes() in db/schema.sql). `InMemoryNotesStore` is dependency-free
and behaves the same, for tests and NOVA_MOCK_LLM runs.

EVERY CALL IS ONE USER'S
Every method takes the signed-in user's id and only ever sees that user's
notes: another user's note is NoteNotFound, exactly as if it did not exist, so
a guessed id reveals nothing. The id comes from the verified token (api/notes.py
or core/request_user.py), never from a request body. The server uses the
service-role key, which bypasses row-level security, so this filtering is the
real boundary; the RLS policies in db/schema.sql are the backstop.

Everything here touches only the notes tables. What a delete does to Persona
(promoted facts, tombstones) crosses stores, so it lives one level up in
store/notes/__init__.py rather than being duplicated in both backends.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional, Protocol, Union, runtime_checkable
from uuid import UUID

from app.store.notes import scoring
from app.store.notes.models import (
    Note,
    NoteChunkIn,
    NoteIn,
    NoteMatch,
    NotePatch,
    NoteQuery,
    NoteSummary,
    note_from_row,
)
from app.store.persona.embeddings import Embedder

TABLE = "notes"
CHUNKS_TABLE = "note_chunks"
MATCH_FN = "match_notes"

UserId = Union[UUID, str]

# Every column but the embedding and the generated fts - nothing outside the
# store reads those, and the embedding is 1024 floats a row.
_COLUMNS = (
    "id,user_id,created_at,updated_at,source,kind,title,text,segments,"
    "duration_s,context,stt,tags,summary,summary_status,origin_episode_id,"
    "promoted_fact_ids"
)

# How many candidates match_notes() hands back before Python re-ranks them
# with the recency decay - wider than any caller's limit so a fresh quick note
# can overtake an older, slightly closer one.
_CANDIDATES = 50


class NoteNotFound(KeyError):
    """No such note for this user (it may exist for someone else - callers
    must not be able to tell)."""


class NoteIdConflict(ValueError):
    """A client-generated id that another user's note already has. Only a
    buggy or hostile client gets here; the API answers 409."""


@runtime_checkable
class NotesStore(Protocol):
    def create(self, user_id: UserId, note: NoteIn) -> Note: ...
    def get(self, user_id: UserId, note_id: str) -> Note: ...
    def list(self, user_id: UserId, *, limit: int = 50, before: Optional[datetime] = None,
             kind: Optional[str] = None) -> list[Note]: ...
    def all_notes(self, user_id: UserId) -> list[Note]: ...
    def update(self, user_id: UserId, note_id: str, patch: NotePatch) -> Note: ...
    def set_summary(self, user_id: UserId, note_id: str, summary: Optional[NoteSummary], status: str) -> None: ...
    def set_promoted(self, user_id: UserId, note_id: str, fact_ids: list[str]) -> None: ...
    def replace_chunks(self, user_id: UserId, note_id: str, chunks: list[NoteChunkIn]) -> None: ...
    def search(self, user_id: UserId, q: NoteQuery) -> list[NoteMatch]: ...
    def delete(self, user_id: UserId, note_id: str) -> None: ...


def _uid(user_id: UserId) -> str:
    return str(UUID(str(user_id)))  # normalised, and a non-UUID fails loudly here


def _embed_text(title: Optional[str], text: str) -> str:
    """What a note's own embedding is taken over. bge truncates at 512 tokens,
    which is why long notes also get chunks (notes_pipeline/chunking.py)."""
    return f"{title}\n{text}" if title else text


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _new_row(user_id: str, note: NoteIn) -> dict[str, Any]:
    now = _now()
    return {
        "id": note.id,
        "user_id": user_id,
        "created_at": (note.created_at or now).isoformat(),
        "updated_at": now.isoformat(),
        "source": note.source,
        "kind": note.kind,
        "title": note.title,
        "text": note.text,
        "segments": [s.model_dump() for s in note.segments],
        "duration_s": note.duration_s,
        "context": note.context.model_dump() if note.context else None,
        "stt": note.stt.model_dump() if note.stt else None,
        "tags": list(note.tags),
        "summary": None,
        "summary_status": "none",
        "origin_episode_id": note.origin_episode_id,
        "promoted_fact_ids": [],
    }


def _patched_fields(current: Note, patch: NotePatch) -> dict[str, Any]:
    """The columns a PATCH changes. Editing the text makes an existing summary
    stale rather than wrong-and-silent - the user re-summarises when they want."""
    fields: dict[str, Any] = {"updated_at": _now().isoformat()}
    if patch.title is not None:
        fields["title"] = patch.title
    if patch.tags is not None:
        fields["tags"] = list(patch.tags)
    if patch.segments is not None:
        fields["segments"] = [s.model_dump() for s in patch.segments]
    if patch.text is not None and patch.text != current.text:
        fields["text"] = patch.text
        if current.summary_status == "done":
            fields["summary_status"] = "stale"
    return fields


def _needs_reembed(fields: dict[str, Any]) -> bool:
    return "text" in fields or "title" in fields


class SupabaseNotesStore:
    """Notes backed by the `notes` / `note_chunks` tables."""

    def __init__(self, client, embedder: Embedder) -> None:
        self._db = client
        self._embed = embedder

    def _mine(self, user_id: str):
        return self._db.table(TABLE).select(_COLUMNS).eq("user_id", user_id)

    def create(self, user_id: UserId, note: NoteIn) -> Note:
        uid = _uid(user_id)
        # Idempotent on the client's id: a retried POST returns what landed the
        # first time rather than a duplicate or a conflict error.
        try:
            return self.get(uid, note.id)
        except NoteNotFound:
            pass
        if self._db.table(TABLE).select("id").eq("id", note.id).limit(1).execute().data:
            raise NoteIdConflict(note.id)
        row = _new_row(uid, note)
        row["embedding"] = self._embed.embed([_embed_text(note.title, note.text)], input_type="document")[0]
        self._db.table(TABLE).insert(row).execute()
        return self.get(uid, note.id)

    def get(self, user_id: UserId, note_id: str) -> Note:
        try:
            UUID(note_id)
        except ValueError:
            raise NoteNotFound(note_id)  # never reaches Postgres as malformed input
        res = self._mine(_uid(user_id)).eq("id", note_id).limit(1).execute()
        if not res.data:
            raise NoteNotFound(note_id)
        return note_from_row(res.data[0])

    def list(self, user_id: UserId, *, limit: int = 50, before: Optional[datetime] = None,
             kind: Optional[str] = None) -> list[Note]:
        query = self._mine(_uid(user_id))
        if before:
            query = query.lt("created_at", before.isoformat())
        if kind:
            query = query.eq("kind", kind)
        res = query.order("created_at", desc=True).limit(limit).execute()
        return [note_from_row(r) for r in res.data]

    def all_notes(self, user_id: UserId) -> list[Note]:
        res = self._mine(_uid(user_id)).order("created_at", desc=True).execute()
        return [note_from_row(r) for r in res.data]

    def update(self, user_id: UserId, note_id: str, patch: NotePatch) -> Note:
        uid = _uid(user_id)
        current = self.get(uid, note_id)
        fields = _patched_fields(current, patch)
        if _needs_reembed(fields):
            fields["embedding"] = self._embed.embed(
                [_embed_text(fields.get("title", current.title), fields.get("text", current.text))],
                input_type="document",
            )[0]
        self._db.table(TABLE).update(fields).eq("user_id", uid).eq("id", note_id).execute()
        return self.get(uid, note_id)

    def set_summary(self, user_id: UserId, note_id: str, summary: Optional[NoteSummary], status: str) -> None:
        self._db.table(TABLE).update({
            "summary": summary.model_dump() if summary else None,
            "summary_status": status,
            "updated_at": _now().isoformat(),
        }).eq("user_id", _uid(user_id)).eq("id", note_id).execute()

    def set_promoted(self, user_id: UserId, note_id: str, fact_ids: list[str]) -> None:
        self._db.table(TABLE).update({"promoted_fact_ids": fact_ids}) \
            .eq("user_id", _uid(user_id)).eq("id", note_id).execute()

    def replace_chunks(self, user_id: UserId, note_id: str, chunks: list[NoteChunkIn]) -> None:
        uid = _uid(user_id)
        self.get(uid, note_id)  # never write chunks against someone else's note
        self._db.table(CHUNKS_TABLE).delete().eq("user_id", uid).eq("note_id", note_id).execute()
        if not chunks:
            return
        vectors = self._embed.embed([c.text for c in chunks], input_type="document")
        self._db.table(CHUNKS_TABLE).insert([
            {
                "note_id": note_id,
                "user_id": uid,
                "idx": c.idx,
                "text": c.text,
                "start_s": c.start_s,
                "end_s": c.end_s,
                "embedding": vec,
            }
            for c, vec in zip(chunks, vectors)
        ]).execute()

    def search(self, user_id: UserId, q: NoteQuery) -> list[NoteMatch]:
        uid = _uid(user_id)
        if not (q.text or "").strip():
            notes = [
                n for n in self.list(uid, limit=max(q.limit * 4, q.limit), kind=q.kind)
                if _in_window(n, q)
            ]
            return [_unranked(n) for n in notes[: q.limit]]

        embedding = self._embed.embed([q.text], input_type="query")[0]
        res = self._db.rpc(MATCH_FN, {
            "filter_user": uid,
            "query_embedding": embedding,
            "query_text": q.text,
            "match_count": _CANDIDATES,
            "min_similarity": scoring.MIN_SIMILARITY,
            "filter_since": q.since.isoformat() if q.since else None,
            "filter_until": q.until.isoformat() if q.until else None,
            "filter_kind": q.kind,
        }).execute()
        hits = {str(r["note_id"]): r for r in res.data}
        if not hits:
            return []
        rows = self._mine(uid).in_("id", list(hits)).execute().data
        matches = []
        for row in rows:
            note = note_from_row(row)
            hit = hits[note.id]
            matches.append(_ranked(
                note, q.text, float(hit.get("similarity") or 0.0),
                float(hit.get("lexical") or 0.0),
                hit.get("chunk_text"), hit.get("chunk_start_s"),
            ))
        return _top(matches, q.limit)

    def delete(self, user_id: UserId, note_id: str) -> None:
        # note_chunks rows go with it: `on delete cascade` (db/schema.sql).
        self._db.table(TABLE).delete().eq("user_id", _uid(user_id)).eq("id", note_id).execute()


class InMemoryNotesStore:
    """Process-local fake with the same behaviour. No Supabase/model required."""

    def __init__(self, embedder: Embedder) -> None:
        self._embed = embedder
        self._rows: dict[str, dict[str, Any]] = {}
        self._vecs: dict[str, list[float]] = {}
        self._chunks: dict[str, list[tuple[NoteChunkIn, list[float]]]] = {}

    def _row(self, user_id: UserId, note_id: str) -> dict[str, Any]:
        row = self._rows.get(note_id)
        if row is None or row["user_id"] != _uid(user_id):
            raise NoteNotFound(note_id)
        return row

    def create(self, user_id: UserId, note: NoteIn) -> Note:
        uid = _uid(user_id)
        if note.id in self._rows:
            if self._rows[note.id]["user_id"] != uid:
                raise NoteIdConflict(note.id)
            return self.get(uid, note.id)
        self._rows[note.id] = _new_row(uid, note)
        self._vecs[note.id] = self._embed.embed([_embed_text(note.title, note.text)], input_type="document")[0]
        return self.get(uid, note.id)

    def get(self, user_id: UserId, note_id: str) -> Note:
        return note_from_row(self._row(user_id, note_id))

    def list(self, user_id: UserId, *, limit: int = 50, before: Optional[datetime] = None,
             kind: Optional[str] = None) -> list[Note]:
        notes = self.all_notes(user_id)
        if before:
            notes = [n for n in notes if n.created_at < before]
        if kind:
            notes = [n for n in notes if n.kind == kind]
        return notes[:limit]

    def all_notes(self, user_id: UserId) -> list[Note]:
        uid = _uid(user_id)
        return sorted(
            (note_from_row(r) for r in self._rows.values() if r["user_id"] == uid),
            key=lambda n: n.created_at, reverse=True,
        )

    def update(self, user_id: UserId, note_id: str, patch: NotePatch) -> Note:
        current = self.get(user_id, note_id)
        fields = _patched_fields(current, patch)
        row = self._row(user_id, note_id)
        row.update(fields)
        if _needs_reembed(fields):
            self._vecs[note_id] = self._embed.embed([_embed_text(row["title"], row["text"])], input_type="document")[0]
        return self.get(user_id, note_id)

    def set_summary(self, user_id: UserId, note_id: str, summary: Optional[NoteSummary], status: str) -> None:
        self._row(user_id, note_id).update({
            "summary": summary.model_dump() if summary else None,
            "summary_status": status,
            "updated_at": _now().isoformat(),
        })

    def set_promoted(self, user_id: UserId, note_id: str, fact_ids: list[str]) -> None:
        self._row(user_id, note_id)["promoted_fact_ids"] = list(fact_ids)

    def replace_chunks(self, user_id: UserId, note_id: str, chunks: list[NoteChunkIn]) -> None:
        self._row(user_id, note_id)
        vectors = self._embed.embed([c.text for c in chunks], input_type="document") if chunks else []
        self._chunks[note_id] = list(zip(chunks, vectors))

    def chunks(self, note_id: str) -> list[NoteChunkIn]:
        """Test hook - what replace_chunks() stored."""
        return [c for c, _ in self._chunks.get(note_id, [])]

    def search(self, user_id: UserId, q: NoteQuery) -> list[NoteMatch]:
        candidates = [n for n in self.all_notes(user_id) if _in_window(n, q) and (not q.kind or n.kind == q.kind)]
        if not (q.text or "").strip():
            return [_unranked(n) for n in candidates[: q.limit]]

        qvec = self._embed.embed([q.text], input_type="query")[0]
        matches = []
        for note in candidates:
            similarity = _cosine(qvec, self._vecs[note.id])
            chunk_text, chunk_start = None, None
            for chunk, vec in self._chunks.get(note.id, []):
                sim = _cosine(qvec, vec)
                if sim > similarity:
                    similarity, chunk_text, chunk_start = sim, chunk.text, chunk.start_s
            lexical = scoring.lexical_score(q.text, _embed_text(note.title, note.text))
            if scoring.is_hit(similarity, lexical):
                matches.append(_ranked(note, q.text, similarity, lexical, chunk_text, chunk_start))
        return _top(matches, q.limit)

    def delete(self, user_id: UserId, note_id: str) -> None:
        try:
            self._row(user_id, note_id)
        except NoteNotFound:
            return
        self._rows.pop(note_id, None)
        self._vecs.pop(note_id, None)
        self._chunks.pop(note_id, None)


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    return max(0.0, min(1.0, dot))  # unit vectors; clamp fp drift


def _in_window(note: Note, q: NoteQuery) -> bool:
    created = note.created_at if note.created_at.tzinfo else note.created_at.replace(tzinfo=timezone.utc)
    if q.since and created < _aware(q.since):
        return False
    if q.until and created > _aware(q.until):
        return False
    return True


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _unranked(note: Note) -> NoteMatch:
    text = note.summary.tldr if note.summary else note.text
    return NoteMatch(note=note, score=0.0, snippet=scoring.snippet(text, None))


def _ranked(note: Note, query: str, similarity: float, lexical: float,
            chunk_text: Optional[str], chunk_start: Optional[float]) -> NoteMatch:
    return NoteMatch(
        note=note,
        score=scoring.combined_score(similarity, lexical, note.kind, note.created_at),
        snippet=scoring.snippet(note.text, query, preferred=chunk_text),
        snippet_start_s=chunk_start,
    )


def _top(matches: list[NoteMatch], limit: int) -> list[NoteMatch]:
    matches.sort(key=lambda m: m.score, reverse=True)
    return matches[:limit]


def new_note_id() -> str:
    """For server-side creators (the memory tool); the phone makes its own."""
    return str(uuid.uuid4())
