"""Tests for the Memory store seam (section 5.5) against the in-memory fake.

These run with no Supabase credentials. The Supabase implementation shares the
same MemoryStore contract, so passing here means the seam behaves as promised.
"""
import pytest

from app.memory import (
    Action,
    InMemoryMemoryStore,
    MemoryEntry,
    MemoryFilter,
    MemoryNotFound,
    Outcome,
    append,
    delete,
    query,
    set_store,
    update_outcome,
)


@pytest.fixture(autouse=True)
def fresh_store():
    set_store(InMemoryMemoryStore())


def make_entry(tool="calendar.add_event", text="add lecture at 2pm"):
    return MemoryEntry(
        event={"type": "user_request", "text": text},
        user_state={"activity": "walking", "confidence": 0.8},
        action=Action(tool=tool, params={"title": "Lecture"}),
    )


def test_append_then_query_roundtrip():
    entry_id = append(make_entry())
    assert entry_id

    results = query(MemoryFilter(tool="calendar.add_event"))
    assert len(results) == 1
    assert results[0].id == entry_id
    assert results[0].action.tool == "calendar.add_event"
    assert results[0].created_at is not None  # stamped by the store


def test_two_phase_outcome_patch():
    entry_id = append(make_entry())
    updated = update_outcome(entry_id, Outcome(status="accepted", gain_delta=0.1))
    assert updated.outcome.status == "accepted"
    assert updated.outcome.gain_delta == 0.1


def test_gain_can_read_recent_outcomes_for_a_tool():
    # Proof (a): a rejected outcome is retrievable by Naoise's tool+status filter.
    entry_id = append(make_entry(tool="notif.summarise"))
    update_outcome(entry_id, Outcome(status="rejected"))

    rejects = query(MemoryFilter(tool="notif.summarise", status="rejected", limit=20))
    assert len(rejects) == 1
    assert rejects[0].outcome.status == "rejected"


def test_query_filters_by_tool():
    append(make_entry(tool="calendar.add_event"))
    append(make_entry(tool="notes.remember"))
    assert len(query(MemoryFilter(tool="notes.remember"))) == 1


def test_query_orders_newest_first_and_respects_limit():
    ids = [append(make_entry(text=f"note {i}")) for i in range(5)]
    results = query(MemoryFilter(limit=3))
    assert len(results) == 3
    assert results[0].id == ids[-1]  # newest first


def test_update_missing_entry_raises():
    with pytest.raises(MemoryNotFound):
        update_outcome("does-not-exist", Outcome(status="accepted"))


def test_delete_removes_entry():
    entry_id = append(make_entry())
    delete(entry_id)
    assert query(MemoryFilter()) == []
