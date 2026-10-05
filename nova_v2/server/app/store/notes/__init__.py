"""
store/notes/ - the user's notes  (Jay, F2)

WHAT THIS IS
The third store, next to episodic memory and Persona, and deliberately not
either of them:

  - episodic_memory is what happened, append-only, read by consolidation.
  - persona is who the user is, durable, searched by meaning.
  - notes are what the user wrote down. They edit them, delete them, and
    decide what they are for. Consolidation never reads them; a note reaches
    Persona only when the user explicitly promotes it (promote() below, or the
    memory tool when the model files a note under a category).

USAGE
    from app.store import notes
    from app.store.notes import NoteIn, NoteQuery

    note = notes.create(user_id, NoteIn(id=str(uuid4()), text="ask tutor about Q3",
                                        source="device_voice", kind="quick"))
    for m in notes.search(user_id, NoteQuery(text="tutor", limit=5)):
        print(m.score, m.note.display_title(), m.snippet)
    notes.delete(user_id, note.id)   # and every Persona fact promoted from it

By default this talks to Supabase and shares Persona's embedder (one bge-large
per process, not two). Tests swap both with set_store(InMemoryNotesStore(...)).

WHO USES THIS
- api/notes.py: the REST surface the Android Notes tab talks to
- tools/functions/memory_tool.py: save and recall
- notes_pipeline/: chunks, embeds and summarises long notes (NoteProcessor)
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional, Protocol, runtime_checkable

from app.store.notes.models import (
    FlaggedMoment,
    Note,
    NoteChunkIn,
    NoteContext,
    NoteIn,
    NoteMatch,
    NotePatch,
    NoteQuery,
    NoteSegment,
    NoteStt,
    NoteSummary,
)
from app.store.notes.store import (
    InMemoryNotesStore,
    NoteIdConflict,
    NoteNotFound,
    NotesStore,
    SupabaseNotesStore,
    UserId,
    new_note_id,
)

__all__ = [
    "create", "get", "list_notes", "all_notes", "update", "search", "delete",
    "delete_all", "set_summary", "set_interpretation", "replace_chunks", "promote",
    "get_store", "set_store", "get_processor", "set_processor",
    "NoteProcessor", "NotesStore", "InMemoryNotesStore", "SupabaseNotesStore",
    "NoteNotFound", "NoteIdConflict", "set_promoted", "Note", "NoteIn", "NotePatch", "NoteQuery", "NoteMatch",
    "NoteSummary", "NoteSegment", "NoteContext", "NoteStt", "NoteChunkIn",
    "FlaggedMoment", "new_note_id",
]


# --- C3: the processing seam ---------------------------------------------------

@runtime_checkable
class NoteProcessor(Protocol):
    """What happens to a note after it is stored.

    The router calls this; notes_pipeline/processor.py implements it (chunk,
    embed, summarise). Kept as a seam so the store never imports the pipeline
    and the pipeline never edits the store's code - only calls its API.
    """

    def after_create(self, note: Note) -> Note: ...
    def after_edit(self, note: Note) -> Note: ...


class _NoOpProcessor:
    def after_create(self, note: Note) -> Note:
        return note

    def after_edit(self, note: Note) -> Note:
        return note


_store: Optional[NotesStore] = None
_processor: Optional[NoteProcessor] = None


def get_store() -> NotesStore:
    global _store
    if _store is None:
        from app.core.config import settings
        from app.core.db import get_client
        from app.store import persona

        if not settings().supabase_configured:
            # Local dev with no Supabase (the NOVA_MOCK_LLM loop). Loud, because
            # on a real deployment this would mean notes vanish on restart.
            from app.store.persona.embeddings import FakeEmbedder

            print("[notes] WARNING: Supabase not configured - notes are kept IN MEMORY "
                  "and lost on restart. Fine for local testing only.")
            _store = InMemoryNotesStore(FakeEmbedder())
        else:
            _store = SupabaseNotesStore(get_client(), persona.get_embedder())
    return _store


def set_store(store: NotesStore) -> None:
    global _store
    _store = store


def get_processor() -> NoteProcessor:
    """The pipeline if it has been wired (main.py does), otherwise a no-op, so
    nothing that stores a note depends on the pipeline being importable."""
    return _processor or _NoOpProcessor()


def set_processor(processor: Optional[NoteProcessor]) -> None:
    global _processor
    _processor = processor


# --- reads and writes ----------------------------------------------------------
# Every call is one signed-in user's (see store.py): `user_id` comes from the
# verified token - api/notes.py's Depends(current_user), or
# core/request_user.py for the memory tool - never from a request body.

def create(user_id: UserId, note: NoteIn) -> Note:
    return get_store().create(user_id, note)


def get(user_id: UserId, note_id: str) -> Note:
    return get_store().get(user_id, note_id)


def list_notes(user_id: UserId, *, limit: int = 50, before=None, kind: Optional[str] = None) -> list[Note]:
    return get_store().list(user_id, limit=limit, before=before, kind=kind)


def all_notes(user_id: UserId) -> list[Note]:
    """Every one of the user's notes, newest first - export reads this."""
    return get_store().all_notes(user_id)


def update(user_id: UserId, note_id: str, patch: NotePatch) -> Note:
    return get_store().update(user_id, note_id, patch)


def search(user_id: UserId, query: NoteQuery) -> list[NoteMatch]:
    return get_store().search(user_id, query)


def set_summary(user_id: UserId, note_id: str, summary: Optional[NoteSummary], status: str) -> None:
    get_store().set_summary(user_id, note_id, summary, status)


def set_interpretation(user_id: UserId, note_id: str, text: Optional[str]) -> None:
    get_store().set_interpretation(user_id, note_id, text)


def replace_chunks(user_id: UserId, note_id: str, chunks: list[NoteChunkIn]) -> None:
    get_store().replace_chunks(user_id, note_id, chunks)


def set_promoted(user_id: UserId, note_id: str, fact_ids: list[str]) -> None:
    get_store().set_promoted(user_id, note_id, fact_ids)


# --- deletion: the part that crosses stores ------------------------------------

def delete(user_id: UserId, note_id: str) -> None:
    """Forget a note entirely: after this its words exist nowhere Nova keeps
    them, and only content-free tombstones remain so it isn't re-learned
    (docs/plans/notes-hard-delete-plan.md - "deleted notes are entirely gone").

    In order:
      1. every Persona fact promoted from it is deleted - persona.delete()
         tombstones each one, so consolidation cannot re-derive it. Only a
         fact that is still this note's own: one the promotion was merged
         into, or that a newer belief has since overwritten in place, stands
         on something else too, and just loses this note from its provenance;
      2. the episodes holding its words are deleted: the voice turn that saved
         it (origin_episode_id), and any later turn whose event or action
         names it (read back, a reminder made from it) - by id, never by text;
      3. facts the statement pass derived from those episodes go the same way
         as step 1;
      4. `note:<id>` and `episode:<id>` for each of them are tombstoned, which
         covers a fact that was already deleted from the Knowledge Map before
         the note was, and stops anything re-reading the episodes;
      5. anything this user has pending in process memory (a reminder offer,
         a question's thread) is dropped - it may quote the note;
      6. the note and its chunks go;
      7. every row removed or rewritten above is written to the deletion
         journal (ids only), so deploy/scrub-backups.sh removes it from the
         nightly backups too.

    Persona and episode failures are logged and do not stop the delete: the
    user asked for the note to go, and leaving it on screen because a tombstone
    could not be written is the worse failure (same stance as persona.delete
    itself).
    """
    from app.store import memory, persona

    store = get_store()
    note = store.get(user_id, note_id)  # NoteNotFound for someone else's note -> 404
    words = {t for t in (note.text, note.interpreted_text) if t}

    # For the backups (step 7): Persona rows deleted, and rows rewritten
    # without this note.
    facts_deleted: list[str] = []
    facts_rewritten: list[str] = []

    for fact_id in note.promoted_fact_ids:
        try:
            outcome = _unpromote(user_id, note_id, fact_id, words)
            if outcome == "deleted":
                facts_deleted.append(fact_id)
            elif outcome == "rewritten":
                facts_rewritten.append(fact_id)
        except Exception as e:
            print(f"[notes] promoted fact {fact_id} not deleted: {e}")

    episode_ids: list[str] = []
    if note.origin_episode_id:
        try:
            memory.delete_episode(user_id, note.origin_episode_id)
        except Exception as e:
            print(f"[notes] origin episode of {note_id} not deleted: {e}")
        episode_ids.append(note.origin_episode_id)
    try:
        episode_ids += [e for e in memory.delete_referencing(user_id, note_id) if e not in episode_ids]
    except Exception as e:
        print(f"[notes] episodes referencing {note_id} not deleted: {e}")

    if episode_ids:
        try:
            deleted, rewritten = _forget_derived(user_id, set(episode_ids))
            facts_deleted += deleted
            facts_rewritten += rewritten
        except Exception as e:
            print(f"[notes] facts derived from {note_id}'s episodes not deleted: {e}")

    try:
        persona.forget(user_id, persona.note_key(note_id))
        for episode_id in episode_ids:
            persona.forget(user_id, f"episode:{episode_id}")
    except Exception as e:
        print(f"[notes] tombstone for note {note_id} not recorded: {e}")

    try:
        from app import intent_surface

        intent_surface.forget_pending_for_user(user_id)
    except Exception as e:
        print(f"[notes] pending turn state not cleared: {e}")

    try:
        store.delete(user_id, note_id)
    finally:
        # Even if the note itself couldn't be deleted, what already went must
        # leave the backups too.
        _journal(user_id, note_id, episode_ids, facts_deleted, facts_rewritten,
                 note_deleted=_is_gone(store, user_id, note_id))
    print(f"[notes] deleted {note_id} ({len(facts_deleted)} fact(s) deleted, "
          f"{len(facts_rewritten)} rewritten, {len(episode_ids)} episode(s))")


def _is_gone(store, user_id: UserId, note_id: str) -> bool:
    try:
        store.get(user_id, note_id)
        return False
    except NoteNotFound:
        return True
    except Exception:
        return True  # unknown: journalling a row that still exists only scrubs it from backups


def _journal(
    user_id: UserId, note_id: str, episode_ids: list[str],
    facts_deleted: list[str], facts_rewritten: list[str], note_deleted: bool,
) -> None:
    """Step 7 of delete(). Logged loudly rather than raised: the live delete
    has already happened, and failing it now would leave the user looking at a
    note that is half gone."""
    from app.store import deletion_journal

    try:
        if note_deleted:
            deletion_journal.record(user_id, "notes", [note_id])  # chunks go by cascade
        deletion_journal.record(user_id, "episodic_memory", episode_ids)
        deletion_journal.record(user_id, "persona", facts_deleted)
        deletion_journal.record(user_id, "persona", [f for f in facts_rewritten if f not in facts_deleted],
                                kind="overwrite")
    except Exception as e:
        print(f"[notes] WARNING: deletion of {note_id} not journalled, so backups will keep it "
              f"until they rotate out: {e}")


def _unpromote(
    user_id: UserId, note_id: str, fact_id: str, words: set[str] = frozenset(),
) -> Optional[str]:
    """Take a deleted note's contribution out of one Persona fact: the whole
    fact if the note is what it is, otherwise just the note's key - and any
    History entry that quotes the note word for word. Returns "deleted",
    "rewritten" or None (nothing to do)."""
    from app.store import persona

    try:
        fact = persona.get(user_id, fact_id)
    except persona.FactNotFound:
        return None
    meta = fact.metadata or {}
    if meta.get("note_id") == note_id:
        persona.delete(user_id, fact_id)
        return "deleted"
    key = persona.note_key(note_id)
    if key in (meta.get("also_keys") or []):
        kept = [k for k in meta["also_keys"] if k != key]
        persona.upsert(user_id, fact.model_copy(update={"metadata": {
            **meta, "also_keys": kept, **_history_without(meta, words),
        }}))
        return "rewritten"
    return None


def _forget_derived(user_id: UserId, episode_ids: set[str]) -> tuple[list[str], list[str]]:
    """Facts the statement pass read out of deleted episodes. A fact standing
    only on them is deleted (and tombstoned, by persona.delete); one that
    other surviving episodes said too keeps those, loses the deleted ones, and
    drops its `quote` - the user's own words from the deleted turn. Returns
    the ids (deleted, rewritten)."""
    from app.store import persona

    deleted: list[str] = []
    rewritten: list[str] = []
    for fact in persona.all_facts(user_id):
        meta = fact.metadata or {}
        own = meta.get("episode_id")
        also = [e for e in (meta.get("also_from") or []) if e]
        if own not in episode_ids and not episode_ids.intersection(also):
            continue
        surviving = [e for e in ([own] if own else []) + also if e not in episode_ids]
        if not surviving and not meta.get("note_id") and not meta.get("also_keys") \
                and not (meta.get("signal") and meta.get("value")):
            persona.delete(user_id, fact.id)
            deleted.append(fact.id)
            continue
        patch = {**meta, "also_from": [e for e in also if e not in episode_ids]}
        if own in episode_ids:
            patch["episode_id"] = surviving[0] if surviving else None
            patch["quote"] = ""
            patch["also_from"] = [e for e in patch["also_from"] if e != patch["episode_id"]]
        persona.upsert(user_id, fact.model_copy(update={"metadata": patch}))
        rewritten.append(fact.id)
    return deleted, rewritten


def _history_without(meta: dict, words: set[str]) -> dict:
    """`history` minus the entries that quote one of `words` exactly."""
    history = meta.get("history")
    if not history or not words:
        return {}
    return {"history": [h for h in history if h.get("from") not in words and h.get("to") not in words]}


def delete_all(user_id: UserId) -> int:
    """Settings -> Notes -> Delete all notes. Same cascade, every one of the
    user's notes - and only theirs."""
    notes = get_store().all_notes(user_id)
    for note in notes:
        delete(user_id, note.id)
    return len(notes)


# --- promotion: the only way note content reaches Persona ----------------------

def promote(
    user_id: UserId,
    note_id: str,
    category: list[str],
    text: Optional[str] = None,
    replaces_fact_id: Optional[str] = None,
) -> str:
    """File a note (or a statement drawn from it) as a belief about the user.

    Explicit only - "Add to what Nova knows" in the app, or the memory tool
    when the model files a note under a category. The fact carries `note_id`
    and origin "note", which is what gives it the `note:<id>` tombstone key
    (persona.tombstone_keys) and what the Knowledge Map follows back to the
    note.

    `replaces_fact_id` updates an existing belief in place instead of adding a
    second one.

    Written through persona.remember(), dated now - promoting is the user
    asserting it now, however old the note. So a promotion that says what
    Nova already knows merges into that belief (whose id is returned), and
    one that contradicts it replaces it.

    The fact goes into this user's Persona. `replaces_fact_id` must be one of
    their facts (FactNotFound otherwise). `user_id` is also kept in the
    metadata, from before Persona was per-user; harmless, and it says whose
    note the belief came from.
    """
    from app.store import persona

    note = get(user_id, note_id)
    result = persona.remember(user_id, persona.Fact(
        id=replaces_fact_id,
        text=(text or note.text).strip(),
        category=category or ["notes"],
        metadata={
            "source": "stated",
            "origin": "note",
            "note_id": note_id,
            "user_id": str(user_id),
            "tags": note.tags,
        },
    ), stated_at=datetime.now(timezone.utc))
    fact_id = result.fact_id
    if fact_id and fact_id not in note.promoted_fact_ids:
        get_store().set_promoted(user_id, note_id, [*note.promoted_fact_ids, fact_id])
    return fact_id
