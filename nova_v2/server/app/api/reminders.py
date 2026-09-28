"""
api/reminders.py - reminder sync  (Riley)

    POST /reminders/sync   {since, changes[]} -> {reminders[], cursor}   (Bearer)

One round trip: the phone sends the reminders it changed since its last sync,
and gets back everything that changed on the account since then (including
edits from another phone), with last writer winning. See app/store/reminders.py.

A store failure is 503, so the phone's sync worker retries later with its
changes still queued.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.core.auth import AuthUser, current_user
from app.schemas.reminders import ReminderSyncIn, ReminderSyncItem, ReminderSyncOut
from app.store import reminders

router = APIRouter(prefix="/reminders", tags=["reminders"])


@router.post("/sync", response_model=ReminderSyncOut)
def sync(body: ReminderSyncIn, user: AuthUser = Depends(current_user)) -> ReminderSyncOut:
    try:
        rows, cursor = reminders.sync(
            user.id, [c.model_dump() for c in body.changes], body.since)
    except Exception as e:
        print(f"[reminders] sync failed: {type(e).__name__}")
        raise HTTPException(status_code=503, detail="reminders store unavailable")
    return ReminderSyncOut(reminders=[ReminderSyncItem(**r) for r in rows], cursor=cursor)
