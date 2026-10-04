"""
api/notes.py - the notes REST surface  (Jay, F2)

    POST   /notes                 create (idempotent on the client's id)
    GET    /notes                 list or search: ?q&since&until&kind&limit&before
    GET    /notes/export          ?format=json|md
    DELETE /notes?confirm=true    delete every note (Settings -> Privacy)
    GET    /notes/{id}
    PATCH  /notes/{id}            edit title/text/tags/segments; re-embeds
    DELETE /notes/{id}            and every Persona fact promoted from it
    POST   /notes/{id}/promote    "Add to what Nova knows" (explicit only)

The Android Notes tab and the phone's note outbox are the callers. Voice notes
arrive here directly from the phone - never through /event - so a captured
lecture never touches the episode log or the statement pass.

Every route is the signed-in user's (Depends(current_user)): they see, search,
export and delete only their own notes, and another user's note id is a 404,
exactly like one that doesn't exist.

Store failures other than "no such note" surface as 503 rather than a bare
500: the outbox on the phone retries on 5xx, and "try again later" is exactly
what it should do.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from app.core.auth import AuthUser, current_user
from app.store import memory
from app.store import notes
from app.store import persona
from app.store.notes import export as notes_export
from app.store.notes import Note, NoteIdConflict, NoteIn, NoteNotFound, NotePatch, NoteQuery

router = APIRouter(prefix="/notes", tags=["notes"])

# A list row carries this much of the text; the detail view fetches the rest.
PREVIEW_CHARS = 300


class NoteListItem(BaseModel):
    """A list/search row - everything but the transcript body."""

    id: str
    created_at: datetime
    updated_at: datetime
    source: str
    kind: str
    title: str
    preview: str
    snippet: Optional[str] = None
    snippet_start_s: Optional[float] = None
    tldr: Optional[str] = None
    duration_s: Optional[float] = None
    calendar_title: Optional[str] = None
    tags: list[str] = []
    summary_status: str
    promoted: bool = False


class PromoteIn(BaseModel):
    category: list[str] = ["notes"]
    text: Optional[str] = None


class PromoteOut(BaseModel):
    fact_id: str


_MARKUP = re.compile(r"^\s{0,3}(?:#{1,6}\s+|[-*•]\s+|\d+[.)]\s+)|\*\*|__", re.MULTILINE)


def plain_text(text: str) -> str:
    """A note's text without the light Markdown Nova writes notes in (headings,
    bullets, bold), on one line - what a list row has room for."""
    return " ".join(_MARKUP.sub("", text).split())


def _row(note: Note, snippet: Optional[str] = None, snippet_start_s: Optional[float] = None) -> NoteListItem:
    text = plain_text(note.text)
    return NoteListItem(
        id=note.id,
        created_at=note.created_at,
        updated_at=note.updated_at,
        source=note.source,
        kind=note.kind,
        title=note.display_title(),
        preview=text if len(text) <= PREVIEW_CHARS else text[:PREVIEW_CHARS].rstrip() + "…",
        snippet=snippet,
        snippet_start_s=snippet_start_s,
        tldr=note.summary.tldr if note.summary else None,
        duration_s=note.duration_s,
        calendar_title=note.context.calendar_title if note.context else None,
        tags=note.tags,
        summary_status=note.summary_status,
        promoted=bool(note.promoted_fact_ids),
    )


def _get_or_404(user: AuthUser, note_id: str) -> Note:
    try:
        return notes.get(user.id, note_id)
    except NoteNotFound:
        raise HTTPException(status_code=404, detail=f"unknown note: {note_id!r}")
    except Exception as e:
        raise _unavailable(e)


def _unavailable(e: Exception) -> HTTPException:
    print(f"[notes] store unavailable: {e}")
    return HTTPException(status_code=503, detail="notes store unavailable")


def _as_utc(value: Optional[datetime], utc_offset_minutes: int) -> Optional[datetime]:
    """A naive bound is the phone's local time (the recall chips reuse the
    memory tool's local since/until); an aware one is taken as given."""
    if value is None:
        return None
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc)
    return (value - timedelta(minutes=utc_offset_minutes)).replace(tzinfo=timezone.utc)


@router.post("", response_model=Note)
async def create_note(note_in: NoteIn, user: AuthUser = Depends(current_user)) -> Note:
    try:
        try:
            existing = notes.get(user.id, note_in.id)
            print(f"[notes] {note_in.id} already stored - idempotent replay")
            return existing
        except NoteNotFound:
            pass
        note = notes.create(user.id, note_in)
    except NoteIdConflict:
        raise HTTPException(status_code=409, detail="note id already in use")
    except HTTPException:
        raise
    except Exception as e:
        raise _unavailable(e)

    print(f"[notes] created {note.id} ({note.source}/{note.kind}, {len(note.text.split())} words)")
    _log_captured(user, note)
    processor = notes.get_processor()
    try:
        if hasattr(processor, "summarise"):
            return processor.after_create(note, summarise=note_in.summarise == "auto")
        return processor.after_create(note)
    except Exception as e:
        # The note is saved; processing is an enhancement. Report what landed.
        print(f"[notes] processing failed for {note.id}: {e}")
        return notes.get(user.id, note.id)


@router.get("", response_model=list[NoteListItem])
async def list_or_search_notes(
    q: Optional[str] = None,
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
    kind: Optional[Literal["quick", "dictation", "capture"]] = None,
    limit: int = Query(50, ge=1, le=200),
    before: Optional[datetime] = None,
    utc_offset_minutes: int = 0,
    user: AuthUser = Depends(current_user),
) -> list[NoteListItem]:
    since_utc = _as_utc(since, utc_offset_minutes)
    until_utc = _as_utc(until, utc_offset_minutes)
    try:
        if q or since_utc or until_utc:
            matches = notes.search(user.id, NoteQuery(text=q, since=since_utc, until=until_utc, kind=kind, limit=limit))
            return [_row(m.note, m.snippet if q else None, m.snippet_start_s) for m in matches]
        return [_row(n) for n in notes.list_notes(user.id, limit=limit, before=_as_utc(before, 0), kind=kind)]
    except Exception as e:
        raise _unavailable(e)


@router.get("/export")
async def export_notes(
    format: Literal["json", "md"] = "json", user: AuthUser = Depends(current_user),
) -> Any:
    try:
        all_notes = notes.all_notes(user.id)
    except Exception as e:
        raise _unavailable(e)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    if format == "md":
        return PlainTextResponse(
            notes_export.to_markdown(all_notes),
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="nova-notes-{stamp}.md"'},
        )
    return notes_export.to_json(all_notes)


@router.delete("")
async def delete_all_notes(confirm: bool = False, user: AuthUser = Depends(current_user)) -> dict[str, int]:
    """Every note, with the full per-note cascade. `confirm=true` is required
    so a stray DELETE on the collection can't wipe everything."""
    if not confirm:
        raise HTTPException(status_code=400, detail="pass confirm=true to delete every note")
    try:
        deleted = notes.delete_all(user.id)
    except Exception as e:
        raise _unavailable(e)
    print(f"[notes] deleted all ({deleted})")
    return {"deleted": deleted}


@router.get("/{note_id}", response_model=Note)
async def get_note(note_id: str, user: AuthUser = Depends(current_user)) -> Note:
    note = _get_or_404(user, note_id)
    return _prune_dead_promotions(user, note)


@router.get("/{note_id}/markdown", response_class=PlainTextResponse)
async def get_note_markdown(note_id: str, user: AuthUser = Depends(current_user)) -> str:
    """One note as Markdown - the detail view's Share button."""
    return notes_export.note_markdown(_get_or_404(user, note_id))


@router.patch("/{note_id}", response_model=Note)
async def edit_note(note_id: str, patch: NotePatch, user: AuthUser = Depends(current_user)) -> Note:
    current = _get_or_404(user, note_id)
    try:
        note = notes.update(user.id, note_id, patch)
    except Exception as e:
        raise _unavailable(e)
    if note.text != current.text or note.segments != current.segments:
        try:
            note = notes.get_processor().after_edit(note)
        except Exception as e:
            print(f"[notes] re-processing failed for {note_id}: {e}")
    return note


@router.delete("/{note_id}", status_code=204)
async def delete_note(note_id: str, user: AuthUser = Depends(current_user)) -> Response:
    _get_or_404(user, note_id)
    try:
        notes.delete(user.id, note_id)
    except NoteNotFound:
        pass  # deleted concurrently - the outcome the caller wanted
    except Exception as e:
        raise _unavailable(e)
    return Response(status_code=204)


@router.post("/{note_id}/promote", response_model=PromoteOut)
async def promote_note(note_id: str, body: PromoteIn, user: AuthUser = Depends(current_user)) -> PromoteOut:
    _get_or_404(user, note_id)
    try:
        fact_id = notes.promote(user.id, note_id, body.category, text=body.text)
    except Exception as e:
        raise _unavailable(e)
    print(f"[notes] promoted {note_id} -> persona fact {fact_id}")
    return PromoteOut(fact_id=fact_id)


# The content-free trace a voice note leaves in the episode log.
# Consolidation's trend pass counts these by calendar event -
# "captures notes in Tuesday COMP2100 lectures" - without any note text ever
# reaching the log, the statement pass, or Persona. Assistant saves are not
# logged here: their voice turn is already an episode.
NOTE_CAPTURED_EVENT_TYPE = "note_captured"
_CAPTURED_SOURCES = ("device_voice", "phone_voice")


def _log_captured(user: AuthUser, note: Note) -> None:
    if note.source not in _CAPTURED_SOURCES:
        return
    try:
        memory.append(user.id, {
            "event_type": NOTE_CAPTURED_EVENT_TYPE,
            "event": {
                "type": NOTE_CAPTURED_EVENT_TYPE,
                "note_id": note.id,
                "kind": note.kind,
                "duration_s": note.duration_s,
                # Calendar metadata the phone already sends with every event's
                # user_state - not note content.
                "calendar_title": note.context.calendar_title if note.context else None,
            },
        })
    except Exception as e:
        print(f"[notes] note_captured episode skipped: {e}")


def _prune_dead_promotions(user: AuthUser, note: Note) -> Note:
    """Drop promoted_fact_ids whose fact was deleted from the Knowledge Map,
    so the detail view's "In what Nova knows" state is true. Best-effort."""
    if not note.promoted_fact_ids:
        return note
    try:
        live = []
        for fact_id in note.promoted_fact_ids:
            try:
                persona.get(user.id, fact_id)
                live.append(fact_id)
            except persona.FactNotFound:
                pass
        if live != note.promoted_fact_ids:
            notes.set_promoted(user.id, note.id, live)
            return note.model_copy(update={"promoted_fact_ids": live})
    except Exception as e:
        print(f"[notes] promotion check skipped for {note.id}: {e}")
    return note
