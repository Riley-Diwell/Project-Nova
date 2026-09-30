"""Reminder sync (app/store/reminders.py, api/reminders.py): last writer wins, the
cursor only returns what's new, and one account never sees another's reminders."""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import reminders as reminders_api
from app.core.auth import AuthUser, current_user
from app.store import reminders

ALICE = uuid.uuid4()
BOB = uuid.uuid4()
T0 = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)
NOW_MS = int(T0.timestamp() * 1000)


def item(id="r1", updated_at_ms=NOW_MS, status="pending", text="buy milk"):
    return {"id": id, "status": status, "updated_at_ms": updated_at_ms,
            "data": {"id": id, "text": text, "status": status}}


@pytest.fixture(autouse=True)
def store():
    s = reminders.InMemoryReminderStore()
    reminders.set_store(s)
    yield s
    reminders.set_store(None)


def sync(user, changes=(), since=None, at=T0):
    return reminders.sync(user, list(changes), since, now=at)


def test_first_sync_stores_and_returns_everything():
    rows, cursor = sync(ALICE, [item("r1"), item("r2")])
    assert {r["id"] for r in rows} == {"r1", "r2"}
    assert cursor == T0


def test_newer_edit_wins():
    sync(ALICE, [item(text="old", updated_at_ms=NOW_MS)])
    rows, _ = sync(ALICE, [item(text="new", updated_at_ms=NOW_MS + 1)], at=T0 + timedelta(seconds=1))
    assert rows[0]["data"]["text"] == "new"


def test_older_edit_loses_and_the_phone_gets_the_servers_copy():
    sync(ALICE, [item(text="from phone A", updated_at_ms=NOW_MS + 10)])
    _, cursor = sync(ALICE)
    # Phone B, offline, edited earlier - its change loses, and it's told what won,
    # even though that row is older than its cursor.
    rows, _ = sync(ALICE, [item(text="from phone B", updated_at_ms=NOW_MS + 5)],
                   since=cursor, at=T0 + timedelta(seconds=1))
    assert [r["data"]["text"] for r in rows] == ["from phone A"]


def test_cursor_returns_only_newer_changes():
    _, cursor = sync(ALICE, [item("r1")])
    sync(ALICE, [item("r2")], at=T0 + timedelta(seconds=5))
    rows, next_cursor = sync(ALICE, since=cursor, at=T0 + timedelta(seconds=6))
    assert [r["id"] for r in rows] == ["r2"]
    assert next_cursor == T0 + timedelta(seconds=5)


def test_nothing_new_keeps_the_cursor():
    _, cursor = sync(ALICE, [item("r1")])
    rows, again = sync(ALICE, since=cursor, at=T0 + timedelta(seconds=5))
    assert rows == [] and again == cursor


def test_accounts_are_separate():
    sync(ALICE, [item("r1", text="alice's")])
    rows, _ = sync(BOB)
    assert rows == []
    # Same reminder id on another account is a different row, not a takeover.
    sync(BOB, [item("r1", text="bob's", updated_at_ms=NOW_MS + 100)])
    alice_rows, _ = sync(ALICE)
    assert alice_rows[0]["data"]["text"] == "alice's"


def test_finished_reminders_are_purged_after_30_days():
    old = NOW_MS - int(timedelta(days=31).total_seconds() * 1000)
    sync(ALICE, [item("done", status="done", updated_at_ms=old),
                 item("open", status="pending", updated_at_ms=old)])
    rows, _ = sync(ALICE)
    assert [r["id"] for r in rows] == ["open"]


# --- the endpoint -------------------------------------------------------------

@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(reminders_api.router)
    app.dependency_overrides[current_user] = lambda: AuthUser(id=ALICE, email=None)
    return TestClient(app)


def test_endpoint_round_trip(client):
    r = client.post("/reminders/sync", json={"changes": [item()]})
    assert r.status_code == 200
    body = r.json()
    assert body["reminders"][0]["data"]["text"] == "buy milk"
    assert body["cursor"] is not None


def test_endpoint_uses_the_token_user_not_the_body(client, store):
    client.post("/reminders/sync", json={"changes": [item()], "user_id": str(BOB)})
    assert all(uid == str(ALICE) for uid, _ in store.rows)


def test_endpoint_rejects_oversized_data(client):
    big = item()
    big["data"]["text"] = "x" * 9_000
    assert client.post("/reminders/sync", json={"changes": [big]}).status_code == 422


def test_endpoint_store_failure_is_503(client, monkeypatch):
    def boom(*_, **__):
        raise RuntimeError("db down")
    monkeypatch.setattr(reminders, "sync", boom)
    assert client.post("/reminders/sync", json={}).status_code == 503
