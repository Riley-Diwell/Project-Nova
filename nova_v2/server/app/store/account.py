"""
store/account.py - deleting an account and everything in it  (Riley)

    delete(user_id)  -> the account and all its data, gone for good

In order:
  1. the account is written to the deletion journal, so
     deploy/scrub-backups.sh takes every row of it out of the nightly dumps
     too. First, so a failure later can't leave data that the backups keep
     forever: if this fails, nothing is deleted and the caller says so;
  2. anything held in process memory for the user is dropped - a pending
     reminder offer or question, the notification batcher's queue, cached
     Canvas responses;
  3. the Supabase Auth user is deleted. Every table's user_id references
     auth.users with on delete cascade (db/schema.sql), so notes, chunks,
     episodes, Persona and its tombstones, reminders, profile, Canvas
     connection and the rest all go with it, in one transaction;
  4. GoTrue's audit log lines naming the account (its email among them) are
     deleted - the cascade doesn't reach them.

The deletion journal has no foreign key, so step 3 leaves the journal row for
the scrub. The server holds no files for a user - note audio only ever lives
on the phone, which wipes its own copy.
"""
from __future__ import annotations

from typing import Union
from uuid import UUID

from app.core import gotrue
from app.core.db import get_client
from app.store import deletion_journal

UserId = Union[UUID, str]


def delete(user_id: UserId) -> None:
    """Raises if the journal can't be written (nothing deleted) or GoTrue
    refuses or can't be reached (journalled, not yet deleted - a retry
    finishes it)."""
    key = str(user_id)
    deletion_journal.record(key, "account", [key])
    _forget_in_memory(key)
    gotrue.delete_user(key)
    try:
        lines = get_client().rpc("delete_auth_audit", {"p_user": key}).execute().data
        print(f"[account] deleted {key} ({lines} audit line(s))")
    except Exception as e:
        # The account is gone either way. deploy/scrub-backups.sh deletes
        # these lines again, live and in the backups, before it clears the
        # journal, so a miss here is caught within minutes.
        print(f"[account] deleted {key}, but its audit log lines weren't: {e}")


def _forget_in_memory(user_id: str) -> None:
    from app import intent_surface
    from app.tools.canvas import client as canvas_client
    from app.tools.functions import notification_management

    for forget in (
        intent_surface.forget_pending_for_user,
        notification_management.forget_batcher,
        lambda _: canvas_client.clear_cache(),
    ):
        try:
            forget(user_id)
        except Exception as e:
            # Process memory only - it goes on the next restart anyway, and
            # the account must not survive because a cache wouldn't clear.
            print(f"[account] in-memory state not cleared: {e}")
