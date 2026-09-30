"""
api/notes_pipeline.py - the pipeline's own endpoint  (F5)

    POST /notes/{id}/summarise    summarise now (Re-summarise, or a retry after
                                  summary_status="failed")

Separate from api/notes.py so the pipeline owner never edits the store
owner's router.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.core.auth import AuthUser, current_user
from app.store import notes
from app.store.notes import Note, NoteNotFound

router = APIRouter(prefix="/notes", tags=["notes"])


@router.post("/{note_id}/summarise", response_model=Note)
async def summarise_note(note_id: str, user: AuthUser = Depends(current_user)) -> Note:
    try:
        note = notes.get(user.id, note_id)
    except NoteNotFound:
        raise HTTPException(status_code=404, detail=f"unknown note: {note_id!r}")
    except Exception as e:
        print(f"[notes pipeline] store unavailable: {e}")
        raise HTTPException(status_code=503, detail="notes store unavailable")

    processor = notes.get_processor()
    if not hasattr(processor, "summarise"):
        raise HTTPException(status_code=503, detail="summariser not configured")
    return processor.summarise(note)
