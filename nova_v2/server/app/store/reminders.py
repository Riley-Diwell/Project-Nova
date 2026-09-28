"""
store/reminders.py - each user's reminders, synced from their phone  (Riley)

The phone owns reminders day to day: it fires the alarms and works offline.
This store is the account's copy, so reminders survive signing out,
reinstalling or changing phone.

The server doesn't interpret a reminder: `data` is the phone's ReminderEntity,
field for field, stored as-is. Only what sync needs is pulled out into columns:

    sync(user_id, changes, since) -> (reminders, cursor)

`changes` are the phone's edits since its last sync. Each one wins if its
`updated_at_ms` (the phone's clock at the edit) is newer than the stored copy's
- last writer wins, which is all two phones of one student need. The reply is
every row the server wrote after `since` (the cursor from the phone's previous
sync; None means everything), plus the server's copy of any change that lost,
so the phone converges on it.

Every function takes the user explicitly; nothing here can read across users.
Tests swap the Supabase store for
InMemoryReminderStore with set_store().
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Protocol
from uuid import UUID

from app.core.db import get_client

# Finished reminders are forgotten after this long, on the phone (ReminderRepository's
# PURGE_AFTER_MILLIS) and here.
PURGE_AFTER = timedelta(days=30)
FINISHED = ("done", "cancelled")


class ReminderStore(Protocol):
    def versions(self, user_id: UUID, ids: list[str]) -> dict[str, int]: ...
    def upsert(self, user_id: UUID, rows: list[dict[str, Any]], synced_at: datetime) -> None: ...
    def by_ids(self, user_id: UUID, ids: list[str]) -> list[dict[str, Any]]: ...
    def changed_since(self, user_id: UUID, since: datetime | None) -> list[dict[str, Any]]: ...
    def purge_finished(self, user_id: UUID, before_ms: int) -> None: ...


class SupabaseReminderStore:
    _TABLE = "reminders"
    _PAGE = 1000

    def versions(self, user_id: UUID, ids: list[str]) -> dict[str, int]:
        if not ids:
            return {}
        rows = (
            get_client().table(self._TABLE).select("id,updated_at_ms")
            .eq("user_id", str(user_id)).in_("id", ids).execute().data
        )
        return {r["id"]: r["updated_at_ms"] for r in rows}

    def upsert(self, user_id: UUID, rows: list[dict[str, Any]], synced_at: datetime) -> None:
        if not rows:
            return
        payload = [{**r, "user_id": str(user_id), "synced_at": synced_at.isoformat()} for r in rows]
        get_client().table(self._TABLE).upsert(payload, on_conflict="user_id,id").execute()

    def by_ids(self, user_id: UUID, ids: list[str]) -> list[dict[str, Any]]:
        if not ids:
            return []
        return (
            get_client().table(self._TABLE).select("*")
            .eq("user_id", str(user_id)).in_("id", ids).execute().data
        )

    def changed_since(self, user_id: UUID, since: datetime | None) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        while True:
            query = get_client().table(self._TABLE).select("*").eq("user_id", str(user_id))
            if since is not None:
                query = query.gt("synced_at", since.isoformat())
            page = (
                query.order("synced_at").order("id")
                .range(len(rows), len(rows) + self._PAGE - 1).execute().data
            )
            rows.extend(page)
            if len(page) < self._PAGE:
                return rows

    def purge_finished(self, user_id: UUID, before_ms: int) -> None:
        (
            get_client().table(self._TABLE).delete()
            .eq("user_id", str(user_id)).in_("status", list(FINISHED))
            .lt("updated_at_ms", before_ms).execute()
        )


class InMemoryReminderStore:
    def __init__(self) -> None:
        self.rows: dict[tuple[str, str], dict[str, Any]] = {}

    def versions(self, user_id: UUID, ids: list[str]) -> dict[str, int]:
        return {i: r["updated_at_ms"] for i in ids if (r := self.rows.get((str(user_id), i)))}

    def upsert(self, user_id: UUID, rows: list[dict[str, Any]], synced_at: datetime) -> None:
        for r in rows:
            self.rows[(str(user_id), r["id"])] = {
                **r, "user_id": str(user_id), "synced_at": synced_at.isoformat()}

    def by_ids(self, user_id: UUID, ids: list[str]) -> list[dict[str, Any]]:
        return [r for i in ids if (r := self.rows.get((str(user_id), i)))]

    def changed_since(self, user_id: UUID, since: datetime | None) -> list[dict[str, Any]]:
        mine = [r for (uid, _), r in self.rows.items() if uid == str(user_id)]
        if since is not None:
            mine = [r for r in mine if datetime.fromisoformat(r["synced_at"]) > since]
        return sorted(mine, key=lambda r: (r["synced_at"], r["id"]))

    def purge_finished(self, user_id: UUID, before_ms: int) -> None:
        for key, r in list(self.rows.items()):
            if key[0] == str(user_id) and r["status"] in FINISHED and r["updated_at_ms"] < before_ms:
                del self.rows[key]


_store: ReminderStore | None = None


def get_store() -> ReminderStore:
    global _store
    if _store is None:
        _store = SupabaseReminderStore()
    return _store


def set_store(store: ReminderStore | None) -> None:
    global _store
    _store = store


def _wire(row: dict[str, Any]) -> dict[str, Any]:
    return {k: row[k] for k in ("id", "status", "updated_at_ms", "data")}


def sync(
    user_id: UUID, changes: list[dict[str, Any]], since: datetime | None,
    now: datetime | None = None,
) -> tuple[list[dict[str, Any]], datetime | None]:
    """Apply the phone's `changes` (last writer wins) and return what it should adopt,
    plus the cursor to send next time. See the module docstring."""
    store = get_store()
    now = now or datetime.now(timezone.utc)

    stored = store.versions(user_id, [c["id"] for c in changes])
    winners = [c for c in changes if c["updated_at_ms"] > stored.get(c["id"], -1)]
    losers = [c["id"] for c in changes if c["updated_at_ms"] <= stored.get(c["id"], -1)]
    store.upsert(user_id, [_wire(c) for c in winners], synced_at=now)

    before_ms = int((now - PURGE_AFTER).timestamp() * 1000)
    store.purge_finished(user_id, before_ms)

    changed = store.changed_since(user_id, since)
    cursor = max((datetime.fromisoformat(r["synced_at"]) for r in changed), default=since)
    seen = {r["id"] for r in changed}
    changed += [r for r in store.by_ids(user_id, losers) if r["id"] not in seen]
    return [_wire(r) for r in changed], cursor
